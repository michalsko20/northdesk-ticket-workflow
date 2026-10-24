"""Pure policy: no HTTP, Temporal, provider calls, or model-supplied evidence."""

from dataclasses import fields
from decimal import Decimal, InvalidOperation

from northdesk.domain.models import Decision, Ticket, escalation
from northdesk.domain.payloads import MAX_ARTICLES, MAX_PROPOSAL_BYTES, json_fits, text_fits

CATEGORIES = ("account", "billing", "technical", "other")
PRIORITIES = ("low", "normal", "high", "urgent")
ACTIONS = ("reply", "refund", "escalate", "close")


def validate_decision(
    ticket: Ticket, proposal: object, invoices: list[dict], article_ids: set[str]
) -> Decision:
    """Turn any invalid model proposal into an explicit, safe escalation."""
    if not isinstance(proposal, dict) or set(proposal) - {f.name for f in fields(Decision)}:
        return escalation("invalid_decision_shape")
    if not json_fits(proposal, MAX_PROPOSAL_BYTES):
        return escalation("invalid_decision_encoding_or_size")
    for key, allowed in (("category", CATEGORIES), ("priority", PRIORITIES), ("action", ACTIONS)):
        if proposal.get(key) not in allowed:
            return escalation(f"invalid_{key}")

    common = {key: proposal[key] for key in ("category", "priority", "action")}
    action = proposal["action"]
    if action == "reply":
        message, articles = proposal.get("message"), proposal.get("articles")
        if not isinstance(message, str) or not message.strip():
            return escalation("missing_reply_message")
        if not text_fits(message):
            return escalation("invalid_reply_encoding_or_size")
        if not isinstance(articles, list) or any(
            not isinstance(article, str) or article not in article_ids for article in articles
        ):
            return escalation("invalid_reply_articles")
        if len(articles) > MAX_ARTICLES:
            return escalation("too_many_reply_articles")
        return Decision(**common, message=message, articles=articles)

    if action in ("close", "escalate"):
        reason = proposal.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return escalation("missing_action_reason")
        if not text_fits(reason):
            return escalation("invalid_reason_encoding_or_size")
        return Decision(**common, reason=reason)

    invoice = next((i for i in invoices if i["invoice_id"] == proposal.get("invoice_id")), None)
    if invoice is None:
        return escalation("unknown_invoice")
    if invoice["customer_id"] != ticket.customer_id:
        return escalation("invoice_customer_mismatch")
    if invoice["status"] != "paid":
        return escalation("invoice_not_paid")
    try:
        # Strings make the wire format and arithmetic independent of binary floats.
        raw_amount = proposal.get("amount_eur")
        if not isinstance(raw_amount, str) or len(raw_amount) > 32:
            return escalation("invalid_refund_amount")
        amount = Decimal(raw_amount)
        maximum = Decimal(str(invoice["amount_eur"]))
        if not amount.is_finite() or amount <= 0 or amount > maximum:
            return escalation("invalid_refund_amount")
        if amount != amount.quantize(Decimal("0.01")):
            return escalation("invalid_refund_precision")
    except InvalidOperation:
        return escalation("invalid_refund_amount")
    return Decision(**common, invoice_id=invoice["invoice_id"], amount_eur=format(amount, ".2f"))
