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


@pytest.mark.parametrize("calls,allowed", [(12, True), (13, False)])
async def test_tool_batch_limit_accepts_boundary_and_rejects_one_over(calls, allowed):
    class BatchProvider:
        def __init__(self):
            self.rounds = 0

        async def step(self, messages, tools):
            self.rounds += 1
            if self.rounds == 1:
                return Turn(calls=[ToolCall(str(i), "get_customer", "{}") for i in range(calls)])
            return Turn(decision={"action": "close"})

    provider = BatchProvider()
    if allowed:
        assert (await decide(TICKETS[0], provider, Catalog()))["action"] == "close"
        assert provider.rounds == 2
    else:
        with pytest.raises(PermanentAgentError, match="agent_tool_limit"):
            await decide(TICKETS[0], provider, Catalog())
        assert provider.rounds == 1


@pytest.mark.parametrize("reads,allowed", [(5, True), (6, False)])
async def test_final_decision_must_fit_within_six_rounds(reads, allowed):
    class RoundProvider:
        def __init__(self):
            self.rounds = 0

        async def step(self, messages, tools):
            self.rounds += 1
            if self.rounds <= reads:
                return Turn(calls=[ToolCall(str(self.rounds), "get_customer", "{}")])
            return Turn(decision={"action": "close"})

    provider = RoundProvider()
    if allowed:
        assert (await decide(TICKETS[0], provider, Catalog()))["action"] == "close"
    else:
        with pytest.raises(PermanentAgentError, match="agent_round_limit"):
            await decide(TICKETS[0], provider, Catalog())
    assert provider.rounds == 6


@pytest.mark.parametrize("query", [None, False, 1, {}, [], "", "x" * 1001])
def test_search_query_type_and_size_are_checked_in_code(query):
    with pytest.raises(PermanentAgentError, match="unknown_tool_or_invalid_arguments"):
        execute_tool(Catalog(), TICKETS[0], "search_kb", json.dumps({"query": query}))


def test_search_accepts_exact_limit_without_changing_customer_scope():
    assert execute_tool(Catalog(), TICKETS[0], "search_kb", json.dumps({"query": "x" * 1000})) == []
    with pytest.raises(PermanentAgentError):
        execute_tool(
            Catalog(),
            TICKETS[0],
            "search_kb",
            json.dumps({"query": "password", "customer_id": "C-300"}),
        )
