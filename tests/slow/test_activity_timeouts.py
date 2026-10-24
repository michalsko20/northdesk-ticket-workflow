"""Opt-in production deadline tests; about seven minutes when run sequentially."""

import asyncio
import os
import uuid
from dataclasses import asdict

import pytest
from temporalio import activity
from temporalio.worker import Worker

from northdesk.domain.models import ActionInput, Decision, Ticket, ValidationInput
from northdesk.workflows.ticket import TicketWorkflow

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SLOW_TIMEOUT_TESTS") != "1", reason="opt-in real activity timeout tests"
)

DECISION = Decision("other", "normal", "close", reason="Synthetic timeout test")


class StalledActivities:
    def __init__(self, target):
        self.target = target
        self.attempts = {"decide": 0, "validate": 0, "act": 0}
        self.gate = asyncio.Event()

    async def enter(self, name):
        self.attempts[name] += 1
        if self.target == name:
            await self.gate.wait()

    @activity.defn(name="decide_ticket")
    async def decide(self, ticket: Ticket) -> dict:
        await self.enter("decide")
        return asdict(DECISION)

    @activity.defn(name="validate_ticket")
    async def validate(self, request: ValidationInput) -> Decision:
        await self.enter("validate")
        return DECISION

    @activity.defn(name="act_on_ticket")
    async def act(self, request: ActionInput) -> dict:
        await self.enter("act")
        return {"action": request.decision.action}


@pytest.mark.parametrize("target,attempts", [("decide", 3), ("validate", 3), ("act", 5)])
async def test_real_timeout_exhaustion_terminates_safely(temporal_env, target, attempts):
    activities = StalledActivities(target)
    queue = f"real-timeout-{uuid.uuid4()}"
    with temporal_env.auto_time_skipping_disabled():
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[TicketWorkflow],
            activities=[activities.decide, activities.validate, activities.act],
        ):
            handle = await temporal_env.client.start_workflow(
                TicketWorkflow.run,
                Ticket(f"timeout-{target}", "C-200", "Hello", "Hello"),
                id=f"ticket/timeout-{target}",
                task_queue=queue,
            )
            result = await asyncio.wait_for(handle.result(), timeout=330)
            assert activities.attempts[target] == attempts
            if target == "act":
                assert result.step == "failed"
                assert result.result is None
                assert result.error.startswith("action_failed:TimeoutError:")
            else:
                assert result.step == "completed"
                assert result.result["action"] == "escalate"
                assert result.decision.reason.startswith("decision_failed:TimeoutError:")
