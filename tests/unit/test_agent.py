import json
from importlib.resources import files

import pytest

from northdesk.agent.loop import decide
from northdesk.agent.providers import (
    FakeProvider,
    MalformedResponse,
    PermanentAgentError,
    ToolCall,
    Turn,
)
from northdesk.agent.tools import execute_tool
from northdesk.domain.models import Ticket
from northdesk.domain.rules import validate_decision
from northdesk.services.catalog import Catalog, UnknownCustomer

TICKETS = [
    Ticket(**t) for t in json.loads(files("northdesk").joinpath("data/tickets.json").read_text())
]


@pytest.mark.parametrize(
    "index,action",
    [(0, "reply"), (1, "refund"), (2, "escalate"), (3, "reply"), (4, "close"), (5, "escalate")],
)
async def test_fake_examples_pass_through_real_tools_and_policy(index, action):
    catalog = Catalog()
    proposal = await decide(TICKETS[index], FakeProvider(), catalog)
    decision = validate_decision(
        TICKETS[index], proposal, catalog.invoices, {a["article_id"] for a in catalog.articles}
    )
    assert decision.action == action
    if index == 5:
        assert proposal["amount_eur"] == "5000"
        assert decision.reason == "invalid_refund_amount"


async def test_unknown_customer_is_permanent():
    with pytest.raises(UnknownCustomer):
        await decide(TICKETS[6], FakeProvider(), Catalog())


async def test_fake_is_not_keyed_on_example_ticket_ids():
    ticket = Ticket("totally-new", "C-200", "Login", "I forgot my password")
    catalog = Catalog()
    catalog.articles[0]["text"] = "Changed trusted password instructions"
    result = await decide(ticket, FakeProvider(), catalog)
    assert result["message"] == "Changed trusted password instructions"


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_invoices", '{"customer_id":"C-300"}'),
        ("refund", "{}"),
        ("search_kb", "[]"),
        ("get_customer", "invalid"),
    ],
)
def test_tool_scope_and_allowlist(name, args):
    with pytest.raises(PermanentAgentError):
        execute_tool(Catalog(), TICKETS[0], name, args)


class RepeatingProvider:
    async def step(self, messages, tools):
        return Turn(calls=[ToolCall("call", "get_customer", "{}")])


async def test_agent_rounds_and_calls_are_bounded():
    with pytest.raises(PermanentAgentError, match="agent_round_limit"):
        await decide(TICKETS[0], RepeatingProvider(), Catalog(), max_rounds=2)
    with pytest.raises(PermanentAgentError, match="agent_tool_limit"):
        await decide(TICKETS[0], RepeatingProvider(), Catalog(), max_calls=1)


class BrokenProvider:
    async def step(self, messages, tools):
        return Turn(decision="not JSON")


async def test_unreadable_decision_has_clear_error():
    with pytest.raises(MalformedResponse):
        await decide(TICKETS[0], BrokenProvider(), Catalog())
