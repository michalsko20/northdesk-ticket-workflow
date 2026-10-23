import json
from dataclasses import asdict

from northdesk.agent.providers import MalformedResponse, PermanentAgentError, Provider
from northdesk.agent.tools import execute_tool, tool_definitions
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog


async def decide(
    ticket: Ticket, provider: Provider, catalog: Catalog, max_rounds: int = 6, max_calls: int = 12
) -> dict:
    # Existence is a code-level check even when the model skips get_customer.
    catalog.get_customer(ticket.customer_id)
    messages = [{"role": "user", "content": json.dumps(asdict(ticket))}]
    count = 0
    for _ in range(max_rounds):
        turn = await provider.step(messages, tool_definitions())
        if not turn.calls:
            if not isinstance(turn.decision, dict):
                raise MalformedResponse("llm_unreadable_decision")
            return turn.decision
        messages.extend(turn.output)
        for call in turn.calls:
            count += 1
            if count > max_calls:
                raise PermanentAgentError("agent_tool_limit")
            result = execute_tool(catalog, ticket, call.name, call.arguments)
            messages.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps({"tool": call.name, "result": result}),
                }
            )
    raise PermanentAgentError("agent_round_limit")
