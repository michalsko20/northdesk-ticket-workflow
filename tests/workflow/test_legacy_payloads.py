import asyncio
import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import timedelta

import httpx
from legacy_ticket import SizeLimitedLegacyTicketWorkflow
from legacy_ticket import TicketWorkflow as LegacyTicketWorkflow
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import Replayer, Worker

from northdesk.api import create_app
from northdesk.domain.models import ActionInput, Decision, Ticket, ValidationInput, escalation
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.workflows.ticket import TicketWorkflow


class LegacyActivities:
    def __init__(self, path, decision, proposal=None):
        self.helpdesk = FakeHelpdesk(path)
        self.decision = decision
        self.proposal = asdict(decision) if proposal is None else proposal
        self.calls = 0

    @activity.defn(name="decide_ticket")
    async def decide(self, ticket: Ticket) -> dict:
        return self.proposal

    @activity.defn(name="validate_ticket")
    async def validate(self, request: ValidationInput) -> Decision:
        return self.decision

    @activity.defn(name="act_on_ticket")
    async def act(self, request: ActionInput) -> dict:
        self.calls += 1
        return await asyncio.to_thread(self.helpdesk.execute, request)


async def test_new_worker_finishes_legacy_oversized_state_without_repeating_effect(
    temporal_env, tmp_path, legacy_type=SizeLimitedLegacyTicketWorkflow, ticket_id="legacy-large"
):
    path = str(tmp_path / "legacy.db")
    queue = f"legacy-{uuid.uuid4()}"
    activities = LegacyActivities(
        path, Decision("account", "normal", "reply", message="x" * 800000, articles=["KB-1"])
    )
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[legacy_type],
        activities=[activities.decide, activities.validate, activities.act],
        max_cached_workflows=0,
    ):
        handle = await temporal_env.client.start_workflow(
            legacy_type.run,
            Ticket(ticket_id, "C-200", "Password", "Forgot password"),
            id=f"ticket/{ticket_id}",
            task_queue=queue,
            task_timeout=timedelta(seconds=1),
        )
        for _ in range(200):
            history = await handle.fetch_history()
            if any(
                event.HasField("workflow_task_failed_event_attributes") for event in history.events
            ):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("Legacy oversized completion did not fail")
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        with sqlite3.connect(path) as db:
            original = json.loads(db.execute("SELECT result FROM actions").fetchone()[0])
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide, activities.validate, activities.act],
        max_cached_workflows=0,
    ):
        result = await asyncio.wait_for(handle.result(), timeout=20)
        assert result.step == "completed"
        assert result.payload_warning
        assert result.result["action_id"] == original["action_id"]
        assert result.result["action"] == "reply"
        assert result.result["details_omitted"] is True
        assert activities.calls == 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(client=temporal_env.client)),
            base_url="http://test",
        ) as api:
            response = await api.get(f"/tickets/{ticket_id}")
            assert response.status_code == 200
            assert response.json()["payload_warning"]
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 1
        await Replayer(workflows=[TicketWorkflow]).replay_workflow(await handle.fetch_history())


async def test_http_can_read_old_unicode_history_even_before_worker_upgrade(temporal_env, tmp_path):
    queue = f"unicode-legacy-{uuid.uuid4()}"
    activities = LegacyActivities(
        str(tmp_path / "unicode.db"), escalation("invalid_decision_shape"), {"unexpected": "\ud800"}
    )
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[LegacyTicketWorkflow],
        activities=[activities.decide, activities.validate, activities.act],
    ):
        handle = await temporal_env.client.start_workflow(
            LegacyTicketWorkflow.run,
            Ticket("legacy-unicode", "C-200", "Hello", "Hello"),
            id="ticket/legacy-unicode",
            task_queue=queue,
        )
        await handle.result()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(client=temporal_env.client)),
            base_url="http://test",
        ) as api:
            response = await api.get("/tickets/legacy-unicode")
            assert response.status_code == 200
            state = response.json()
            assert state["proposal"]["omitted"] is True
            assert state["result"]["action"] == "escalate"
            assert state["payload_warning"]
    await Replayer(workflows=[TicketWorkflow]).replay_workflow(await handle.fetch_history())
