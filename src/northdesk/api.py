import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowQueryFailedError,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from northdesk.connection import connect
from northdesk.domain.models import Approval, Ticket, TicketState
from northdesk.domain.payloads import bounded_state
from northdesk.http_limits import HttpLimits
from northdesk.logging import configure_logging
from northdesk.settings import Settings
from northdesk.workflows.ticket import TicketWorkflow

logger = logging.getLogger(__name__)
RPC_TIMEOUT = timedelta(seconds=5)
TICKET_ID_PATTERN = r"^[A-Za-z0-9_-]{1,80}$"
TicketIdPath = Annotated[str, Path(pattern=TICKET_ID_PATTERN, max_length=80)]


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ticket_id: str = Field(pattern=TICKET_ID_PATTERN)
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


async def get_client(request: Request) -> Client:
    return cast(Client, request.app.state.temporal_client)


def ticket_log(ticket_id: str, event: str) -> str:
    workflow_id = f"ticket/{ticket_id}"
    logger.info(event, extra={"ticket_id": ticket_id, "workflow_id": workflow_id})
    return workflow_id


def create_app(settings: Settings | None = None, client: Client | None = None) -> FastAPI:
    config = settings or Settings()
    query_slots = asyncio.Semaphore(config.http_query_concurrency)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if application.state.temporal_client is None:
            application.state.temporal_client = await connect(config)
        yield

    application = FastAPI(title="Northdesk", version="0.1.0", lifespan=lifespan)
    application.state.temporal_client = client
    application.add_middleware(
        HttpLimits,
        max_body_bytes=config.http_max_body_bytes,
        max_inflight=config.http_max_inflight,
        body_timeout=config.http_body_timeout_seconds,
    )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        def safe(value: object, limit: int) -> str:
            return str(value).encode("utf-8", errors="replace").decode("utf-8")[:limit]

        # Do not traverse or echo rejected input/context; either can be huge or non-JSON.
        errors = [
            {
                "type": safe(error.get("type", "invalid"), 80),
                "loc": [safe(part, 100) for part in error.get("loc", ())[:8]],
                "msg": safe(error.get("msg", "Invalid input"), 200),
            }
            for error in exc.errors()[:10]
        ]
        return JSONResponse({"detail": errors}, status_code=422)

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

    @application.get("/health/worker")
    async def worker_health(temporal: Annotated[Client, Depends(get_client)]) -> dict:
        responses = await asyncio.gather(
            *(
                temporal.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=config.temporal_namespace,
                        task_queue=TaskQueue(name=config.temporal_task_queue),
                        task_queue_type=kind,
                    ),
                    timeout=timedelta(seconds=2),
                )
                for kind in (
                    TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
                    TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
                )
            )
        )
        cutoff = datetime.now(UTC).timestamp() - 120
        counts = [
            sum(poller.last_access_time.seconds >= cutoff for poller in response.pollers)
            for response in responses
        ]
        if not all(counts):
            raise HTTPException(503, "Worker has no recent poller on one or both ticket queues")
        return {"status": "polling", "workflow_pollers": counts[0], "activity_pollers": counts[1]}

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
        ticket_id: TicketIdPath, temporal: Annotated[Client, Depends(get_client)]
    ) -> TicketState:
        workflow_id = ticket_log(ticket_id, "ticket_status_read")
        handle = temporal.get_workflow_handle_for(TicketWorkflow.run, workflow_id)
        try:
            async with asyncio.timeout(RPC_TIMEOUT.total_seconds()):
                description = await handle.describe(rpc_timeout=RPC_TIMEOUT)
                if description.status == WorkflowExecutionStatus.COMPLETED:
                    # Closed results live in history and do not need a polling worker.
                    return bounded_state(await handle.result(rpc_timeout=RPC_TIMEOUT))
                if description.status != WorkflowExecutionStatus.RUNNING:
                    kind = description.status.name.lower() if description.status else "unknown"
                    return TicketState(
                        ticket_id=ticket_id,
                        workflow_id=workflow_id,
                        step="failed",
                        error=f"workflow_{kind}",
                    )
                if query_slots.locked():
                    raise HTTPException(
                        503, "Ticket queries are busy; retry", headers={"Retry-After": "1"}
                    )
                async with query_slots:
                    try:
                        async with asyncio.timeout(config.http_query_timeout_seconds):
                            return bounded_state(
                                await handle.query(
                                    TicketWorkflow.status,
                                    rpc_timeout=timedelta(
                                        seconds=config.http_query_timeout_seconds
                                    ),
                                )
                            )
                    except TimeoutError as exc:
                        raise HTTPException(
                            503, "Active ticket state is busy; retry", headers={"Retry-After": "1"}
                        ) from exc
                    except RPCError as exc:
                        if exc.status == RPCStatusCode.DEADLINE_EXCEEDED:
                            raise HTTPException(
                                503,
                                "Active ticket state is busy; retry",
                                headers={"Retry-After": "1"},
                            ) from exc
                        raise
        except TimeoutError as exc:
            raise HTTPException(504, "Ticket read timed out; retry") from exc
        except WorkflowQueryFailedError as exc:
            raise HTTPException(
                503, "Ticket state cannot currently be read", headers={"Retry-After": "1"}
            ) from exc

    @application.post("/tickets/{ticket_id}/approval", response_model=Approval)
    async def approve(
        ticket_id: TicketIdPath,
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
