import json
from dataclasses import asdict

from opentelemetry import trace

from northdesk.agent.providers import MalformedResponse, PermanentAgentError, Provider
from northdesk.agent.tools import execute_tool, tool_definitions
from northdesk.domain.models import Ticket
from northdesk.domain.payloads import MAX_PROPOSAL_BYTES, MAX_PROVIDER_TURN_BYTES, json_fits
from northdesk.services.catalog import Catalog


async def decide(
    ticket: Ticket, provider: Provider, catalog: Catalog, max_rounds: int = 6, max_calls: int = 12
) -> dict:
    # Existence is a code-level check even when the model skips get_customer.
    catalog.get_customer(ticket.customer_id)
    messages: list[dict] = [{"role": "user", "content": json.dumps(asdict(ticket))}]
    count = 0
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("agent.decide") as span:
        _set_ticket_attributes(span, ticket)
        for _ in range(max_rounds):
            turn = await provider.step(messages, tool_definitions())
            if not json_fits(turn.output, MAX_PROVIDER_TURN_BYTES):
                raise PermanentAgentError("invalid_provider_turn_encoding_or_size")
            if not turn.calls:
                if not isinstance(turn.decision, dict):
                    raise MalformedResponse("llm_unreadable_decision")
                if not json_fits(turn.decision, MAX_PROPOSAL_BYTES):
                    raise PermanentAgentError("invalid_proposal_encoding_or_size")
                _set_decision_attributes(span, turn.decision)
                return turn.decision
            messages.extend(turn.output)
            for call in turn.calls:
                count += 1
                if count > max_calls:
                    raise PermanentAgentError("agent_tool_limit")
                with tracer.start_as_current_span(f"agent.tool.{call.name}") as tool_span:
                    tool_span.set_attribute("gen_ai.tool.name", call.name)
                    tool_span.set_attribute("northdesk.ticket_id", ticket.ticket_id)
                    result = execute_tool(catalog, ticket, call.name, call.arguments)
                    tool_span.set_attribute(
                        "northdesk.tool.result_bytes",
                        len(json.dumps(result, default=str).encode("utf-8")),
                    )
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps({"tool": call.name, "result": result}),
                    }
                )
        raise PermanentAgentError("agent_round_limit")


def _set_ticket_attributes(span: trace.Span, ticket: Ticket) -> None:
    span.set_attribute("northdesk.ticket_id", ticket.ticket_id)
    span.set_attribute("northdesk.customer_id", ticket.customer_id)
    span.set_attribute("northdesk.subject_length", len(ticket.subject))
    span.set_attribute("northdesk.body_length", len(ticket.body))


def _set_decision_attributes(span: trace.Span, decision: dict) -> None:
    for key in ("category", "priority", "action"):
        value = decision.get(key)
        if isinstance(value, str):
            span.set_attribute(f"northdesk.decision.{key}", value)
    invoice = decision.get("invoice_id")
    if isinstance(invoice, str):
        span.set_attribute("northdesk.decision.invoice_id", invoice)
    amount = decision.get("amount_eur")
    if isinstance(amount, str):
        span.set_attribute("northdesk.decision.amount_eur", amount)
    span.set_attribute("northdesk.decision.article_count", len(decision.get("articles", [])))
