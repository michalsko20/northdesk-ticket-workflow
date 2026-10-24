"""Deterministic byte budgets shared by activities, policy and read projections."""

import json
from dataclasses import asdict, replace

from northdesk.domain.models import Decision, TicketState

MAX_PROPOSAL_BYTES = 64 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_ARTICLES = 16
MAX_TOOL_ARGUMENT_BYTES = 16 * 1024
MAX_PROVIDER_TURN_BYTES = 128 * 1024
MAX_STATE_BYTES = 256 * 1024
OMITTED = "[omitted: invalid or oversized legacy text]"


def json_fits(value: object, budget: int) -> bool:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return len(encoded.encode("utf-8")) <= budget
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False


def text_fits(value: object, budget: int = MAX_TEXT_BYTES) -> bool:
    if not isinstance(value, str) or len(value) > budget:
        return False
    try:
        return len(value.encode("utf-8")) <= budget
    except UnicodeError:
        return False


def decision_fits(decision: Decision) -> bool:
    return (
        json_fits(asdict(decision), MAX_PROPOSAL_BYTES)
        and all(value is None or text_fits(value) for value in (decision.message, decision.reason))
        and len(decision.articles) <= MAX_ARTICLES
        and all(text_fits(article, 200) for article in decision.articles)
    )


def bounded_state(state: TicketState) -> TicketState:
    """Project old histories safely without mutating state or changing recorded effects."""
    omitted = []
    proposal = state.proposal
    if proposal is not None and not json_fits(proposal, MAX_PROPOSAL_BYTES):
        proposal = {"omitted": True, "reason": "invalid_or_oversized_legacy_proposal"}
        omitted.append("proposal")
    decision = state.decision
    if decision is not None and not decision_fits(decision):
        decision = replace(
            decision,
            message=decision.message
            if decision.message is None or text_fits(decision.message)
            else OMITTED,
            reason=decision.reason
            if decision.reason is None or text_fits(decision.reason)
            else OMITTED,
            articles=[
                article for article in decision.articles[:MAX_ARTICLES] if text_fits(article, 200)
            ],
        )
        omitted.append("decision_text")
    result = state.result
    if result is not None and not json_fits(result, MAX_PROPOSAL_BYTES):
        # Preserve the receipt identity/action; do not imply a different effect occurred.
        result = {
            key: result[key]
            for key in ("action_id", "ticket_id", "action")
            if key in result and text_fits(result[key], 200)
        }
        result["details_omitted"] = True
        omitted.append("result_details")
    approval = state.approval
    if approval is not None and not text_fits(approval.reviewer, 400):
        approval = replace(approval, reviewer=OMITTED)
        omitted.append("reviewer")
    error = state.error
    if error is not None and not text_fits(error, 512):
        error = "legacy_error_omitted"
        omitted.append("error_details")
    return replace(
        state,
        proposal=proposal,
        decision=decision,
        result=result,
        approval=approval,
        error=error,
        payload_warning="Legacy data omitted from view: " + ", ".join(omitted)
        if omitted
        else state.payload_warning,
    )
