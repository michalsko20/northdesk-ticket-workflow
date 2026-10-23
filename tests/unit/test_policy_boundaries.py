from dataclasses import asdict
from fractions import Fraction

import pytest

from northdesk.domain.models import Decision, Ticket
from northdesk.domain.rules import validate_decision
from northdesk.services.catalog import Catalog

TICKET = Ticket("boundaries", "C-100", "Refund", "Duplicate payment")
REFUND = asdict(Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur="49"))


@pytest.mark.parametrize("field", ["category", "priority", "action"])
@pytest.mark.parametrize("value", [None, False, 0, 1, [], {}, ""])
def test_wrong_enum_types_are_safe_escalations(field, value):
    decision = validate_decision(TICKET, {**REFUND, field: value}, Catalog().invoices, set())
    assert decision.action == "escalate"
    assert decision.reason == f"invalid_{field}"


@pytest.mark.parametrize(
    "amount",
    [
        "sNaN",
        "-Infinity",
        "1e99999999999999999999999999999",
        "1e-9999999999999999999999999999",
        "9" * 33,
        "49.01",
        "49.0000000000000000000000000001",
        "0.00999999999999999999999999999",
        "-0.00",
        "0.000",
        "",
        True,
        [],
        {},
    ],
)
def test_refund_extremes_never_escape_policy(amount):
    decision = validate_decision(
        TICKET, {**REFUND, "amount_eur": amount}, Catalog().invoices, set()
    )
    assert decision.action == "escalate"


def test_refund_amounts_agree_with_independent_rational_oracle():
    invoices = Catalog().invoices
    # Cover every cent around the invoice limit, and fractions of a cent.
    pairs = [(n, 2) for n in range(-3, 4904)]
    pairs += [(n, scale) for scale in range(6) for n in (-490001, -1, 0, 1, 49, 4900, 490001)]
    for numerator, scale in pairs:
        magnitude = str(abs(numerator)).zfill(scale + 1)
        amount = magnitude if scale == 0 else magnitude[:-scale] + "." + magnitude[-scale:]
        if numerator < 0:
            amount = "-" + amount
        exact = Fraction(numerator, 10**scale)
        permitted = 0 < exact <= 49 and (exact * 100).denominator == 1
        decision = validate_decision(TICKET, {**REFUND, "amount_eur": amount}, invoices, set())
        assert (decision.action == "refund") == permitted, amount
        if permitted:
            cents = int(exact * 100)
            assert decision.amount_eur == f"{cents // 100}.{cents % 100:02d}"


def test_all_catalog_customer_invoice_pairs_enforce_ownership_and_paid_status():
    catalog = Catalog()
    for customer in catalog.customers:
        for invoice in catalog.invoices:
            ticket = Ticket("pairs", customer["customer_id"], "Refund", "Refund")
            decision = validate_decision(
                ticket,
                {**REFUND, "invoice_id": invoice["invoice_id"], "amount_eur": "0.01"},
                catalog.invoices,
                set(),
            )
            permitted = invoice["customer_id"] == ticket.customer_id and invoice["status"] == "paid"
            assert (decision.action == "refund") == permitted
