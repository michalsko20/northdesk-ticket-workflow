from dataclasses import asdict

import pytest

from northdesk.domain.models import Decision, Ticket
from northdesk.domain.rules import validate_decision
from northdesk.services.catalog import Catalog

TICKET = Ticket("test", "C-100", "Refund", "Charged twice")
REFUND = asdict(Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur="49"))


def validate(proposal):
    return validate_decision(TICKET, proposal, Catalog().invoices, {"KB-1", "KB-2"})


@pytest.mark.parametrize("amount", ["49", "49.00", "0.01"])
def test_valid_refund(amount):
    result = validate({**REFUND, "amount_eur": amount})
    assert result.action == "refund"
    assert result.amount_eur == format(float(amount), ".2f")


@pytest.mark.parametrize(
    "amount", ["5000", "0", "-1", "NaN", "Infinity", "oops", "0.001", 49, None]
)
def test_unsafe_amount_escalates(amount):
    assert validate({**REFUND, "amount_eur": amount}).action == "escalate"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("category", "pizza", "invalid_category"),
        ("priority", "critical", "invalid_priority"),
        ("action", "execute_code", "invalid_action"),
        ("invoice_id", "INV-3001", "invoice_customer_mismatch"),
        ("invoice_id", "missing", "unknown_invoice"),
    ],
)
def test_invalid_decision_escalates(field, value, reason):
    decision = validate({**REFUND, field: value})
    assert decision.action == "escalate"
    assert decision.reason == reason


def test_unpaid_invoice_escalates():
    ticket = Ticket("x", "C-400", "Refund", "Refund")
    decision = validate_decision(
        ticket, {**REFUND, "invoice_id": "INV-4001"}, Catalog().invoices, set()
    )
    assert decision.reason == "invoice_not_paid"


@pytest.mark.parametrize("proposal", [None, [], {}, {**REFUND, "skip_approval": True}])
def test_malformed_shape_escalates(proposal):
    assert validate(proposal).action == "escalate"


def test_reply_and_reasons():
    reply = asdict(
        Decision("account", "normal", "reply", message="Reset password", articles=["KB-1"])
    )
    assert validate(reply).action == "reply"
    assert validate({**reply, "message": " "}).reason == "missing_reply_message"
    assert validate({**reply, "articles": ["KB-999"]}).reason == "invalid_reply_articles"
    assert validate({**reply, "articles": [{}]}).action == "escalate"
    for action in ("close", "escalate"):
        assert (
            validate(asdict(Decision("other", "low", action, reason="Out of scope"))).action
            == action
        )
        assert validate(asdict(Decision("other", "low", action))).reason == "missing_action_reason"
