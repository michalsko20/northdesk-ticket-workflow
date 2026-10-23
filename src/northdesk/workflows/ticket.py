from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from northdesk.domain.models import (
    ActionInput,
    Approval,
    Decision,
    Ticket,
    TicketState,
    ValidationInput,
    escalation,
)

LLM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=5), maximum_attempts=3
)
ACTION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=8), maximum_attempts=5
)


@workflow.defn
class TicketWorkflow:
    def __init__(self) -> None:
        self.state = TicketState()
        self.deadline: datetime | None = None

    def transition(self, step: str) -> None:
        self.state.step = step
        workflow.logger.info(
            "ticket_step",
            extra={
                "ticket_id": self.state.ticket_id,
                "workflow_id": self.state.workflow_id,
                "step": step,
            },
        )

    @workflow.query
    def status(self) -> TicketState:
        return self.state

    @workflow.update
    def approve(self, approval: Approval) -> Approval:
        self.state.approval = approval
        workflow.logger.info(
            "approval_received",
            extra={
                "ticket_id": self.state.ticket_id,
                "workflow_id": self.state.workflow_id,
            },
        )
        return approval

    @approve.validator
    def validate_approval(self, approval: Approval) -> None:
        if self.state.approval is not None:
            if self.state.approval == approval:
                return
            raise ValueError("An approval decision has already been recorded")
        if self.state.step != "awaiting_approval":
            raise ValueError("Ticket is not awaiting approval")
        if self.deadline is None or workflow.now() >= self.deadline:
            raise ValueError("Approval deadline has expired")

    @workflow.run
    async def run(self, ticket: Ticket) -> TicketState:
        self.state = TicketState(
            ticket_id=ticket.ticket_id, workflow_id=workflow.info().workflow_id
        )
        self.transition("deciding")
        try:
            proposal = await workflow.execute_activity(
                "decide_ticket",
                ticket,
                result_type=dict,
                start_to_close_timeout=timedelta(seconds=90),
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY,
            )
            self.state.proposal = proposal
            self.transition("validating")
            decision = await workflow.execute_activity(
                "validate_ticket",
                ValidationInput(ticket, proposal),
                result_type=Decision,
                start_to_close_timeout=timedelta(seconds=10),
                schedule_to_close_timeout=timedelta(seconds=30),
                retry_policy=LLM_RETRY,
            )
        except ActivityError as exc:
            reason = getattr(exc.cause, "type", type(exc.cause).__name__)
            decision = escalation(f"decision_failed:{reason}")
        self.state.decision = decision

        if decision.action == "refund":
            self.deadline = workflow.now() + timedelta(hours=24)
            self.state.approval_deadline = self.deadline.isoformat()
            self.transition("awaiting_approval")
            try:
                await workflow.wait_condition(
                    lambda: self.state.approval is not None, timeout=timedelta(hours=24)
                )
            except TimeoutError:
                decision = escalation("approval_timeout")
            else:
                if self.state.approval is not None and not self.state.approval.approved:
                    decision = escalation("approval_rejected")
            self.state.decision = decision

        self.transition("acting")
        try:
            self.state.result = await workflow.execute_activity(
                "act_on_ticket",
                ActionInput(ticket, decision, f"ticket/{ticket.ticket_id}/action"),
                result_type=dict,
                start_to_close_timeout=timedelta(seconds=15),
                schedule_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTION_RETRY,
            )
        except ActivityError as exc:
            reason = getattr(exc.cause, "type", type(exc.cause).__name__)
            self.state.error = f"action_failed:{reason}"
            self.transition("failed")
        else:
            self.transition("completed")
        return self.state
