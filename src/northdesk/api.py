import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from temporalio.client import (
    Client,
    WorkflowQueryFailedError,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from northdesk.connection import connect
from northdesk.domain.models import Approval, Ticket, TicketState
from northdesk.logging import configure_logging
from northdesk.settings import Settings
from northdesk.workflows.ticket import TicketWorkflow

logger = logging.getLogger(__name__)
RPC_TIMEOUT = timedelta(seconds=5)


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ticket_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    customer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    approved: StrictBool
    reviewer: str = Field(min_length=1, max_length=100)


class TicketAccepted(BaseModel):
    ticket_id: str
    workflow_id: str
    duplicate: bool


def get_client(request: Request) -> Client:
    return cast(Client, request.app.state.temporal_client)


def ticket_log(ticket_id: str, event: str) -> str:
    workflow_id = f"ticket/{ticket_id}"
    logger.info(event, extra={"ticket_id": ticket_id, "workflow_id": workflow_id})
    return workflow_id


def create_app(settings: Settings | None = None, client: Client | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if application.state.temporal_client is None:
            application.state.temporal_client = await connect(config)
        yield

    application = FastAPI(title="Northdesk", version="0.1.0", lifespan=lifespan)
    application.state.temporal_client = client

    @application.exception_handler(RPCError)
    async def rpc_error(request: Request, exc: RPCError) -> JSONResponse:
        code = {
            RPCStatusCode.NOT_FOUND: 404,
            RPCStatusCode.FAILED_PRECONDITION: 409,
            RPCStatusCode.DEADLINE_EXCEEDED: 504,
            RPCStatusCode.CANCELLED: 504,
        }.get(exc.status, 503)
        detail = {
            404: "Ticket not found",
            409: "Workflow cannot accept this operation",
            504: "Temporal request timed out; retry or check ticket status",
            503: "Temporal is unavailable",
        }[code]
        return JSONResponse(status_code=code, content={"detail": detail})

    @application.get("/health/live")
    async def live() -> dict:
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready(temporal: Annotated[Client, Depends(get_client)]) -> dict:
        if not await temporal.service_client.check_health(timeout=timedelta(seconds=2)):
            raise HTTPException(503, "Temporal is unavailable")
        return {"status": "ready"}

    @application.post("/tickets", status_code=202, response_model=TicketAccepted)
    async def submit(
        payload: TicketRequest, temporal: Annotated[Client, Depends(get_client)]
    ) -> TicketAccepted:
        workflow_id = ticket_log(payload.ticket_id, "ticket_submission")
        duplicate = False
        try:
            await temporal.start_workflow(
                TicketWorkflow.run,
                Ticket(**payload.model_dump()),
                id=workflow_id,
                task_queue=config.temporal_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=RPC_TIMEOUT,
            )
        except WorkflowAlreadyStartedError:
            duplicate = True
        return TicketAccepted(
            ticket_id=payload.ticket_id, workflow_id=workflow_id, duplicate=duplicate
        )

    @application.get("/tickets/{ticket_id}", response_model=TicketState)
    async def status(
        ticket_id: str, temporal: Annotated[Client, Depends(get_client)]
    ) -> TicketState:
        workflow_id = ticket_log(ticket_id, "ticket_status_read")
        handle = temporal.get_workflow_handle_for(TicketWorkflow.run, workflow_id)
        try:
            return await handle.query(TicketWorkflow.status, rpc_timeout=RPC_TIMEOUT)
        except WorkflowQueryFailedError as exc:
            raise HTTPException(503, "Ticket state cannot currently be read") from exc

    @application.post("/tickets/{ticket_id}/approval", response_model=Approval)
    async def approve(
        ticket_id: str,
        payload: ApprovalRequest,
        temporal: Annotated[Client, Depends(get_client)],
    ) -> Approval:
        workflow_id = ticket_log(ticket_id, "ticket_approval_request")
        handle = temporal.get_workflow_handle_for(TicketWorkflow.run, workflow_id)
        try:
            return await handle.execute_update(
                TicketWorkflow.approve,
                Approval(**payload.model_dump()),
                rpc_timeout=RPC_TIMEOUT,
            )
        except WorkflowUpdateFailedError as exc:
            raise HTTPException(409, "Ticket is not awaiting this approval decision") from exc
        except WorkflowUpdateRPCTimeoutOrCancelledError as exc:
            raise HTTPException(
                504, "Approval timed out; check ticket status before retrying"
            ) from exc
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                # A closed execution can return NOT_FOUND for Update even though
                # the ticket still exists. Describe distinguishes it from a bad ID.
                await handle.describe(rpc_timeout=RPC_TIMEOUT)
                raise HTTPException(409, "Ticket workflow has already closed") from exc
            raise

    return application


app = create_app()
