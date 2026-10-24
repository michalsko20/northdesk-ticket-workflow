import logging
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ActivityError, RetryState, TimeoutType
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

from northdesk.domain.models import Decision, Ticket
from northdesk.workflows.ticket import TicketWorkflow


@pytest.mark.parametrize("target", ["decide", "act"])
async def test_timeout_failure_reason_is_readable(monkeypatch, target):
    monkeypatch.setattr("northdesk.workflows.ticket.workflow.patched", lambda _: True)
    timeout = TemporalTimeoutError(
        "activity deadline exceeded", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[]
    )
    failure = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="review-worker",
        activity_type=target,
        activity_id="1",
        retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
    )
    failure.__cause__ = timeout
    reply = Decision("account", "normal", "reply", message="Reset password", articles=["KB-1"])
    results = (
        [failure, {"action": "escalate"}] if target == "decide" else [asdict(reply), reply, failure]
    )
    monkeypatch.setattr(
        "northdesk.workflows.ticket.workflow.execute_activity", AsyncMock(side_effect=results)
    )
    monkeypatch.setattr(
        "northdesk.workflows.ticket.workflow.info",
        lambda: SimpleNamespace(workflow_id="ticket/timeout"),
    )
    monkeypatch.setattr(
        "northdesk.workflows.ticket.workflow.logger", logging.getLogger("review-test")
    )
    result = await TicketWorkflow().run(Ticket("timeout", "C-200", "Password", "Forgot password"))
    if target == "act":
        assert result.step == "failed"
        assert result.result is None
        reason = result.error
    else:
        assert result.step == "completed"
        assert result.result["action"] == "escalate"
        reason = result.decision.reason
    assert "Timeout" in reason, reason
