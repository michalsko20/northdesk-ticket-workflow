from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    customer_id: str
    subject: str
    body: str


@dataclass(frozen=True)
class Decision:
    category: str
    priority: str
    action: str
    message: str | None = None
    articles: list[str] = field(default_factory=list)
    invoice_id: str | None = None
    amount_eur: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Approval:
    approved: bool
    reviewer: str


@dataclass
class TicketState:
    ticket_id: str = ""
    workflow_id: str = ""
    step: str = "starting"
    proposal: dict | None = None
    decision: Decision | None = None
    approval: Approval | None = None
    approval_deadline: str | None = None
    result: dict | None = None
    error: str | None = None


@dataclass(frozen=True)
class ValidationInput:
    ticket: Ticket
    proposal: dict


@dataclass(frozen=True)
class ActionInput:
    ticket: Ticket
    decision: Decision
    idempotency_key: str


def escalation(reason: str) -> Decision:
    return Decision(category="other", priority="normal", action="escalate", reason=reason)
