from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowQueryFailedError,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.service import RPCError, RPCStatusCode

from northdesk.api import create_app
from northdesk.domain.models import TicketState


@pytest.fixture
async def backend_api():
    temporal = MagicMock(spec=Client)
    temporal.service_client.check_health = AsyncMock(return_value=True)
    handle = temporal.get_workflow_handle_for.return_value
    handle.query = AsyncMock()
    handle.execute_update = AsyncMock()
    handle.describe = AsyncMock()
    handle.describe.return_value = SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
    handle.result = AsyncMock(return_value=TicketState(step="completed"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=temporal)), base_url="http://test"
    ) as api:
        yield api, temporal, handle


@pytest.mark.parametrize("operation", ["query", "approval", "health"])
@pytest.mark.parametrize(
    "status,expected",
    [
        (RPCStatusCode.UNAVAILABLE, 503),
        (RPCStatusCode.DEADLINE_EXCEEDED, 504),
        (RPCStatusCode.CANCELLED, 504),
    ],
)
async def test_backend_transport_errors_map_consistently(backend_api, operation, status, expected):
    api, temporal, handle = backend_api
    failure = RPCError("private transport detail", status, b"")
    if operation == "query":
        handle.query.side_effect = failure
        response = await api.get("/tickets/any")
    elif operation == "approval":
        handle.execute_update.side_effect = failure
        response = await api.post(
            "/tickets/any/approval", json={"approved": True, "reviewer": "operator"}
        )
    else:
        temporal.service_client.check_health.side_effect = failure
        response = await api.get("/health/ready")
    if operation == "query" and status == RPCStatusCode.DEADLINE_EXCEEDED:
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
    else:
        assert response.status_code == expected
    assert "private transport detail" not in response.text


async def test_failed_query_has_actionable_error(backend_api):
    api, _, handle = backend_api
    handle.query.side_effect = WorkflowQueryFailedError("private workflow detail")
    response = await api.get("/tickets/any")
    assert response.status_code == 503
    assert "private workflow detail" not in response.text


async def test_closed_ticket_reads_persisted_result_without_query(backend_api):
    api, _, handle = backend_api
    handle.describe.return_value = SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)
    assert (await api.get("/tickets/any")).status_code == 200
    handle.result.assert_awaited_once()
    handle.query.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.TIMED_OUT,
    ],
)
async def test_abnormal_closed_execution_is_reported_as_failed(backend_api, status):
    api, _, handle = backend_api
    handle.describe.return_value = SimpleNamespace(status=status)
    response = await api.get("/tickets/any")
    assert response.status_code == 200
    assert response.json()["step"] == "failed"
    assert response.json()["error"] == "workflow_" + status.name.lower()
    handle.query.assert_not_called()


@pytest.mark.parametrize(
    "failure,expected",
    [
        (WorkflowUpdateFailedError(ValueError("conflict")), 409),
        (WorkflowUpdateRPCTimeoutOrCancelledError(), 504),
    ],
)
async def test_approval_failure_and_ambiguous_timeout(backend_api, failure, expected):
    api, _, handle = backend_api
    handle.execute_update.side_effect = failure
    response = await api.post(
        "/tickets/any/approval", json={"approved": True, "reviewer": "operator"}
    )
    assert response.status_code == expected
    handle.execute_update.assert_awaited_once()


@pytest.mark.parametrize("exists,expected", [(False, 404), (True, 409)])
async def test_update_not_found_distinguishes_missing_and_closed_ticket(
    backend_api, exists, expected
):
    api, _, handle = backend_api
    handle.execute_update.side_effect = RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
    if not exists:
        handle.describe.side_effect = RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
    response = await api.post(
        "/tickets/any/approval", json={"approved": True, "reviewer": "operator"}
    )
    assert response.status_code == expected
    handle.describe.assert_awaited_once()


async def test_readiness_false_does_not_change_liveness(backend_api):
    api, temporal, _ = backend_api
    temporal.service_client.check_health.return_value = False
    assert (await api.get("/health/ready")).status_code == 503
    assert (await api.get("/health/live")).status_code == 200


@pytest.mark.parametrize(
    "workflow,activities,expected", [(1, 1, 200), (0, 1, 503), (1, 0, 503), (0, 0, 503)]
)
async def test_worker_health_requires_recent_pollers_for_both_queues(
    backend_api, workflow, activities, expected
):
    api, temporal, _ = backend_api
    timestamp = Timestamp(seconds=int(datetime.now(UTC).timestamp()))
    temporal.workflow_service.describe_task_queue = AsyncMock(
        side_effect=[
            SimpleNamespace(pollers=[SimpleNamespace(last_access_time=timestamp)] * count)
            for count in (workflow, activities)
        ]
    )
    assert (await api.get("/health/worker")).status_code == expected


async def test_old_poller_does_not_count_as_worker_health(backend_api):
    api, temporal, _ = backend_api
    stale = Timestamp(seconds=int(datetime.now(UTC).timestamp()) - 121)
    temporal.workflow_service.describe_task_queue = AsyncMock(
        return_value=SimpleNamespace(pollers=[SimpleNamespace(last_access_time=stale)])
    )
    assert (await api.get("/health/worker")).status_code == 503
