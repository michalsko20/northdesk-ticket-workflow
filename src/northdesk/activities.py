import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from northdesk.agent.loop import decide
from northdesk.agent.providers import (
    MalformedResponse,
    PermanentAgentError,
    Provider,
    TransientAgentError,
)
from northdesk.domain.models import ActionInput, Decision, Ticket, ValidationInput
from northdesk.domain.rules import validate_decision
from northdesk.services.catalog import Catalog, UnknownCustomer
from northdesk.services.helpdesk import (
    FakeHelpdesk,
    IdempotencyConflict,
    TemporaryHelpdeskError,
)


def log_activity(ticket: Ticket, event: str) -> None:
    info = activity.info()
    activity.logger.info(
        event,
        extra={
            "ticket_id": ticket.ticket_id,
            "workflow_id": info.workflow_id,
            "attempt": info.attempt,
        },
    )


class Activities:
    def __init__(self, provider: Provider, catalog: Catalog, helpdesk: FakeHelpdesk) -> None:
        self.provider, self.catalog, self.helpdesk = provider, catalog, helpdesk

    @activity.defn(name="decide_ticket")
    async def decide_ticket(self, ticket: Ticket) -> dict:
        log_activity(ticket, "agent_started")
        try:
            result = await decide(ticket, self.provider, self.catalog)
        except UnknownCustomer as exc:
            raise ApplicationError(
                "unknown_customer", type="UnknownCustomer", non_retryable=True
            ) from exc
        except PermanentAgentError as exc:
            raise ApplicationError(
                str(exc), type="PermanentAgentError", non_retryable=True
            ) from exc
        except TransientAgentError as exc:
            raise ApplicationError(
                str(exc), type="TransientAgentError", next_retry_delay=exc.retry_after
            ) from exc
        except MalformedResponse as exc:
            raise ApplicationError(str(exc), type=type(exc).__name__) from exc
        log_activity(ticket, "agent_decided")
        return result

    @activity.defn(name="validate_ticket")
    async def validate_ticket(self, request: ValidationInput) -> Decision:
        log_activity(request.ticket, "decision_validation")
        return validate_decision(
            request.ticket,
            request.proposal,
            self.catalog.invoices,
            {article["article_id"] for article in self.catalog.articles},
        )

    @activity.defn(name="act_on_ticket")
    async def act_on_ticket(self, request: ActionInput) -> dict:
        log_activity(request.ticket, "helpdesk_action_started")
        try:
            result = await asyncio.to_thread(self.helpdesk.execute, request)
        except IdempotencyConflict as exc:
            raise ApplicationError(
                str(exc), type="IdempotencyConflict", non_retryable=True
            ) from exc
        except TemporaryHelpdeskError as exc:
            raise ApplicationError(str(exc), type="TemporaryHelpdeskError") from exc
        log_activity(request.ticket, "helpdesk_action_completed")
        return result
