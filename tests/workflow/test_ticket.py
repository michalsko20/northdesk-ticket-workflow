import asyncio
import uuid
from dataclasses import asdict
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from northdesk.domain.models import ActionInput, Approval, Decision, Ticket, ValidationInput
from northdesk.domain.rules import validate_decision
from northdesk.services.catalog import Catalog
from northdesk.workflows.ticket import TicketWorkflow

TICKET = Ticket("workflow-test", "C-100", "Refund", "Duplicate payment")
REFUND = Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur="49.00")
REPLY = Decision("account", "normal", "reply", message="Reset password", articles=["KB-1"])


class FakeActivities:
    def __init__(
        self,
        decision=REPLY,
        fail_action=0,
        fail_decide=0,
        permanent=False,
        decide_gate=None,
        action_gate=None,
        fail_validate=0,
    ):
        self.decision = decision
        self.fail_action = fail_action
        self.fail_decide = fail_decide
        self.permanent = permanent
        self.decide_attempts = 0
        self.action_attempts = 0
        self.requests = []
        self.decide_gate = decide_gate
        self.action_gate = action_gate
        self.fail_validate = fail_validate
        self.validate_attempts = 0

    @activity.defn(name="decide_ticket")
    async def decide(self, ticket: Ticket) -> dict:
        self.decide_attempts += 1
        if self.decide_gate is not None:
            await self.decide_gate.wait()
        if self.decide_attempts <= self.fail_decide:
            raise ApplicationError("model error", type="ModelError", non_retryable=self.permanent)
        return asdict(self.decision)

    @activity.defn(name="validate_ticket")
    async def validate(self, request: ValidationInput) -> Decision:
        self.validate_attempts += 1
        if self.validate_attempts <= self.fail_validate:
            raise ApplicationError(
                "catalog unavailable", type="CatalogError", non_retryable=self.permanent
            )
        catalog = Catalog()
        return validate_decision(
            request.ticket,
            request.proposal,
            catalog.invoices,
            {a["article_id"] for a in catalog.articles},
        )

    @activity.defn(name="act_on_ticket")
    async def act(self, request: ActionInput) -> dict:
        self.action_attempts += 1
        self.requests.append(request)
        if self.action_gate is not None:
            await self.action_gate.wait()
        if self.action_attempts <= self.fail_action:
            raise ApplicationError("temporary failure", type="HelpdeskError")
        return {"action": request.decision.action}


@pytest.fixture
def worker_for(temporal_env):
    def create(activities):
        return Worker(
            temporal_env.client,
            task_queue=f"test-{uuid.uuid4()}",
            workflows=[TicketWorkflow],
            activities=[activities.decide, activities.validate, activities.act],
            max_cached_workflows=0,
        )

    return create


async def start(env, worker):
    return await env.client.start_workflow(
        TicketWorkflow.run,
        TICKET,
        id=f"ticket/{uuid.uuid4()}",
        task_queue=worker.task_queue,
    )


async def awaiting(handle):
    for _ in range(200):
        state = await handle.query(TicketWorkflow.status)
        if state.step == "awaiting_approval":
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("Workflow did not reach awaiting_approval")


async def test_ticket_is_answered(temporal_env, worker_for):
    activities = FakeActivities()
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        result = await handle.result()
        assert result.step == "completed"
        assert result.result["action"] == "reply"
        assert (await handle.query(TicketWorkflow.status)) == result


@pytest.mark.parametrize("approved,expected", [(True, "refund"), (False, "escalate")])
async def test_refund_approval_or_rejection(temporal_env, worker_for, approved, expected):
    activities = FakeActivities(REFUND)
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        state = await awaiting(handle)
        assert state.approval_deadline
        assert activities.action_attempts == 0
        await handle.execute_update(TicketWorkflow.approve, Approval(approved, "reviewer"))
        result = await handle.result()
        assert result.result["action"] == expected
        assert result.approval.approved is approved


async def test_refund_escalates_after_24_hours(temporal_env, worker_for):
    activities = FakeActivities(REFUND)
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        await awaiting(handle)
        await temporal_env.sleep(timedelta(hours=24, seconds=1))
        result = await handle.result()
        assert result.decision.reason == "approval_timeout"
        assert result.result["action"] == "escalate"
        assert all(r.decision.action != "refund" for r in activities.requests)


@pytest.mark.parametrize("where", ["action", "decide"])
async def test_transient_activity_retries_then_succeeds(temporal_env, worker_for, where):
    activities = FakeActivities(
        fail_action=int(where == "action"), fail_decide=int(where == "decide")
    )
    async with worker_for(activities) as worker:
        result = await (await start(temporal_env, worker)).result()
        assert result.step == "completed"
        assert getattr(activities, f"{where}_attempts") == 2
        if where == "action":
            assert activities.requests[0].idempotency_key == activities.requests[1].idempotency_key


@pytest.mark.parametrize("permanent,attempts", [(True, 1), (False, 3)])
async def test_model_failures_escalate_with_bounded_retries(
    temporal_env, worker_for, permanent, attempts
):
    activities = FakeActivities(fail_decide=100, permanent=permanent)
    async with worker_for(activities) as worker:
        result = await (await start(temporal_env, worker)).result()
        assert result.decision.reason == "decision_failed:ModelError"
        assert result.result["action"] == "escalate"
        assert activities.decide_attempts == attempts


async def test_helpdesk_exhaustion_reports_failure(temporal_env, worker_for):
    activities = FakeActivities(fail_action=100)
    async with worker_for(activities) as worker:
        result = await (await start(temporal_env, worker)).result()
        assert result.step == "failed"
        assert result.result is None
        assert result.error == "action_failed:HelpdeskError"
        assert activities.action_attempts == 5


async def test_invalid_refund_never_waits_for_approval(temporal_env, worker_for):
    activities = FakeActivities(
        Decision("billing", "urgent", "refund", invoice_id="INV-1002", amount_eur="5000")
    )
    async with worker_for(activities) as worker:
        result = await (await start(temporal_env, worker)).result()
        assert result.decision.reason == "invalid_refund_amount"
        assert result.approval_deadline is None
        assert result.result["action"] == "escalate"


async def test_approval_for_nonrefund_is_rejected(temporal_env, worker_for):
    activities = FakeActivities(fail_action=1)
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(TicketWorkflow.approve, Approval(True, "reviewer"))
        assert (await handle.result()).result["action"] == "reply"


async def test_restart_worker_preserves_pending_refund(temporal_env, worker_for):
    activities = FakeActivities(REFUND)
    worker = worker_for(activities)
    queue = worker.task_queue
    async with worker:
        handle = await start(temporal_env, worker)
        before = await awaiting(handle)
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide, activities.validate, activities.act],
        max_cached_workflows=0,
    ):
        after = await handle.query(TicketWorkflow.status)
        assert after.decision == before.decision
        assert after.approval_deadline == before.approval_deadline
        await handle.execute_update(TicketWorkflow.approve, Approval(True, "reviewer"))
        assert (await handle.result()).result["action"] == "refund"
        assert activities.decide_attempts == 1


async def test_approval_cannot_be_submitted_before_decision(temporal_env, worker_for):
    gate = asyncio.Event()
    activities = FakeActivities(REFUND, decide_gate=gate)
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        assert (await handle.query(TicketWorkflow.status)).step == "deciding"
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(TicketWorkflow.approve, Approval(True, "operator"))
        assert (await handle.query(TicketWorkflow.status)).approval is None
        gate.set()
        await awaiting(handle)
        await handle.execute_update(TicketWorkflow.approve, Approval(False, "operator"))
        assert (await handle.result()).decision.reason == "approval_rejected"


async def test_first_approval_wins_and_identical_repeat_is_harmless(temporal_env, worker_for):
    gate = asyncio.Event()
    activities = FakeActivities(REFUND, action_gate=gate)
    approval = Approval(True, "operator")
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        await awaiting(handle)
        assert await handle.execute_update(TicketWorkflow.approve, approval) == approval
        assert await handle.execute_update(TicketWorkflow.approve, approval) == approval
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(TicketWorkflow.approve, Approval(False, "other-operator"))
        gate.set()
        assert (await handle.result()).result["action"] == "refund"
        assert activities.action_attempts == 1


async def test_simultaneous_conflicting_approvals_record_one_winner(temporal_env, worker_for):
    gate = asyncio.Event()
    activities = FakeActivities(REFUND, action_gate=gate)
    approvals = [Approval(bool(i % 2), f"reviewer-{i}") for i in range(32)]
    async with worker_for(activities) as worker:
        handle = await start(temporal_env, worker)
        await awaiting(handle)
        try:
            outcomes = await asyncio.gather(
                *(handle.execute_update(TicketWorkflow.approve, item) for item in approvals),
                return_exceptions=True,
            )
            winners = [item for item in outcomes if isinstance(item, Approval)]
            assert len(winners) == 1
            assert all(isinstance(item, (Approval, WorkflowUpdateFailedError)) for item in outcomes)
            assert (await handle.query(TicketWorkflow.status)).approval == winners[0]
        finally:
            gate.set()
        result = await handle.result()
        assert result.result["action"] == ("refund" if winners[0].approved else "escalate")
        assert activities.action_attempts == 1


async def test_worker_offline_across_approval_deadline_cannot_accept_late_refund(
    temporal_env, worker_for
):
    activities = FakeActivities(REFUND)
    worker = worker_for(activities)
    async with worker:
        handle = await start(temporal_env, worker)
        await awaiting(handle)
    await temporal_env.sleep(timedelta(hours=24, seconds=1))
    async with Worker(
        temporal_env.client,
        task_queue=worker.task_queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide, activities.validate, activities.act],
        max_cached_workflows=0,
    ):
        result = await handle.result()
        assert result.decision.reason == "approval_timeout"
        assert result.approval is None
        assert [r.decision.action for r in activities.requests] == ["escalate"]


@pytest.mark.parametrize(
    "failures,permanent,attempts", [(1, False, 2), (100, False, 3), (100, True, 1)]
)
async def test_validation_failures_retry_or_escalate(
    temporal_env, worker_for, failures, permanent, attempts
):
    activities = FakeActivities(fail_validate=failures, permanent=permanent)
    async with worker_for(activities) as worker:
        result = await (await start(temporal_env, worker)).result()
        assert result.step == "completed"
        assert activities.validate_attempts == attempts
        if failures == 1:
            assert result.decision == REPLY
        else:
            assert result.decision.reason == "decision_failed:CatalogError"
            assert result.result["action"] == "escalate"
