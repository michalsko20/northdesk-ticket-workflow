from dataclasses import asdict

import pytest

from northdesk.domain.models import Decision, TicketState
from northdesk.domain.payloads import MAX_STATE_BYTES, bounded_state, json_fits


@pytest.mark.parametrize("bad", ["\ud800", "x" * 800000, float("nan")])
def test_legacy_projection_is_small_json_safe_and_preserves_refund_identity(bad):
    decision = Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur="49.00")
    state = TicketState(
        ticket_id="old",
        workflow_id="ticket/old",
        step="completed",
        proposal={"untrusted": bad},
        decision=decision,
        result={
            "action_id": "receipt",
            "ticket_id": "old",
            "action": "refund",
            "details": {"untrusted": bad},
        },
    )
    view = bounded_state(state)
    assert json_fits(asdict(view), MAX_STATE_BYTES)
    assert view.payload_warning
    assert bounded_state(view) == view
    assert view.decision == decision
    assert view.result["action_id"] == "receipt"
    assert view.result["action"] == "refund"
    assert state.proposal["untrusted"] is bad


def test_small_valid_state_has_no_warning_and_keeps_full_receipt():
    state = TicketState(
        ticket_id="normal",
        decision=Decision("other", "low", "close", reason="Done"),
        result={"action_id": "normal", "action": "close", "details": {"reason": "Done"}},
    )
    assert bounded_state(state) == state
