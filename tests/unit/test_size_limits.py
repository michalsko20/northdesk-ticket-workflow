import sys
from dataclasses import asdict

import pytest
from temporalio.converter import DataConverter

from northdesk.agent.providers import PermanentAgentError
from northdesk.agent.tools import execute_tool
from northdesk.api import TicketRequest
from northdesk.domain.models import ActionInput, Decision, Ticket, TicketState
from northdesk.domain.rules import validate_decision
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk

TEMPORAL_BLOB_LIMIT = 2 * 1024 * 1024


async def test_maximum_valid_ticket_fits_temporal_byte_limit():
    request = TicketRequest(
        ticket_id="t" * 80, customer_id="c" * 80, subject="🧪" * 500, body="🧪" * 20000
    )
    payloads = await DataConverter.default.encode([Ticket(**request.model_dump())])
    assert sum(len(payload.data) for payload in payloads) < TEMPORAL_BLOB_LIMIT


@pytest.mark.parametrize(
    "message_size",
    [
        100000,
        pytest.param(
            800000,
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="F6: repeated decision text exceeds final payload limit",
            ),
        ),
    ],
)
async def test_accepted_proposal_keeps_final_state_below_temporal_limit(tmp_path, message_size):
    ticket = Ticket("payload-limit", "C-200", "Password", "Forgot password")
    proposal = asdict(
        Decision("account", "normal", "reply", message="x" * message_size, articles=["KB-1"])
    )
    decision = validate_decision(ticket, proposal, Catalog().invoices, {"KB-1"})
    assert decision.action == "reply"
    result = FakeHelpdesk(str(tmp_path / "effects.db")).execute(
        ActionInput(ticket, decision, "ticket/payload-limit/action")
    )
    state = TicketState(
        ticket_id=ticket.ticket_id,
        workflow_id="ticket/payload-limit",
        step="completed",
        proposal=proposal,
        decision=decision,
        result=result,
    )
    payloads = await DataConverter.default.encode([state])
    size = sum(len(payload.data) for payload in payloads)
    assert size < TEMPORAL_BLOB_LIMIT, size


@pytest.mark.xfail(
    strict=True,
    raises=(RecursionError, ValueError),
    reason="F7: complex tool JSON escapes permanent argument error classification",
)
@pytest.mark.parametrize("kind", ["nesting", "integer-digits"])
def test_parser_resource_limits_are_permanent_tool_errors(kind):
    if kind == "nesting":
        # CPython 3.12's JSON C decoder has a different depth bound from the
        # Python recursion limit; this exceeds it on both supported platforms.
        depth = 20000
        arguments = '{"query":' + "[" * depth + "0" + "]" * depth + "}"
    else:
        limit = sys.get_int_max_str_digits()
        if limit == 0:
            pytest.skip("Python integer parser limit disabled")
        arguments = '{"query":' + "1" * (limit + 1) + "}"
    with pytest.raises(PermanentAgentError):
        execute_tool(Catalog(), Ticket("parser", "C-200", "Hi", "Hi"), "search_kb", arguments)


@pytest.mark.parametrize("digits,allowed", [(30, True), (31, False)])
def test_amount_text_length_at_32_characters(digits, allowed):
    ticket = Ticket("amount", "C-100", "Refund", "Refund")
    proposal = asdict(
        Decision(
            "billing", "normal", "refund", invoice_id="INV-1002", amount_eur="1." + "0" * digits
        )
    )
    result = validate_decision(ticket, proposal, Catalog().invoices, set())
    assert (result.action == "refund") is allowed
