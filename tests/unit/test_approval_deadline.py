from datetime import UTC, datetime, timedelta

import pytest

from northdesk.domain.models import Approval
from northdesk.workflows.ticket import TicketWorkflow


@pytest.mark.parametrize("offset,valid", [(-1, True), (0, False), (1, False)])
def test_first_approval_deadline_is_exclusive(monkeypatch, offset, valid):
    workflow = TicketWorkflow()
    workflow.state.step = "awaiting_approval"
    workflow.deadline = datetime(2026, 10, 24, tzinfo=UTC)
    now = workflow.deadline + timedelta(microseconds=offset)
    monkeypatch.setattr("northdesk.workflows.ticket.workflow.now", lambda: now)
    approval = Approval(True, "operator")
    if valid:
        workflow.validate_approval(approval)
    else:
        with pytest.raises(ValueError, match="deadline has expired"):
            workflow.validate_approval(approval)
    assert workflow.state.approval is None


def test_identical_retry_after_deadline_preserves_existing_approval(monkeypatch):
    workflow = TicketWorkflow()
    approval = Approval(False, "operator")
    workflow.state.approval = approval
    workflow.state.step = "acting"
    workflow.deadline = datetime(2026, 10, 24, tzinfo=UTC)
    monkeypatch.setattr(
        "northdesk.workflows.ticket.workflow.now", lambda: workflow.deadline + timedelta(days=1)
    )
    workflow.validate_approval(approval)
    with pytest.raises(ValueError, match="already been recorded"):
        workflow.validate_approval(Approval(True, "operator"))
    assert workflow.state.approval == approval
