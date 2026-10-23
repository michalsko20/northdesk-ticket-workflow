import json

from northdesk.agent.providers import PermanentAgentError
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog


def tool_definitions() -> list[dict]:
    tools = []
    for name, description, properties in (
        ("get_customer", "Get this ticket customer's plan and status.", {}),
        ("get_invoices", "Get this ticket customer's invoices.", {}),
        (
            "search_kb",
            "Find relevant support knowledge base articles.",
            {
                "query": {"type": "string", "description": "Search keywords"},
            },
        ),
    ):
        tools.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
        )
    return tools


def execute_tool(catalog: Catalog, ticket: Ticket, name: str, arguments: str) -> object:
    try:
        params = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise PermanentAgentError("invalid_tool_arguments") from exc
    if not isinstance(params, dict):
        raise PermanentAgentError("invalid_tool_arguments")
    # Customer scope comes from the ticket, never from LLM-controlled arguments.
    if name == "get_customer" and not params:
        return catalog.get_customer(ticket.customer_id)
    if name == "get_invoices" and not params:
        return catalog.get_invoices(ticket.customer_id)
    if name == "search_kb" and set(params) == {"query"}:
        query = params["query"]
        if isinstance(query, str) and 0 < len(query) <= 1000:
            return catalog.search_kb(query)
    raise PermanentAgentError("unknown_tool_or_invalid_arguments")
