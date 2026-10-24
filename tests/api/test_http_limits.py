import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from temporalio.client import Client, WorkflowExecutionStatus

from northdesk.api import create_app
from northdesk.domain.models import TicketState
from northdesk.settings import Settings

TICKET = {
    "ticket_id": "bounded",
    "customer_id": "C-200",
    "subject": "Password",
    "body": "Forgot password",
}


@pytest.mark.parametrize("chunked", [False, True])
async def test_body_byte_limit_rejects_before_temporal_and_does_not_echo_input(chunked):
    temporal = AsyncMock(spec=Client)
    sent = 0

    async def chunks():
        nonlocal sent
        for _ in range(30):
            sent += 1
            yield b" " * 32768

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=temporal)), base_url="http://test"
    ) as api:
        response = await api.post(
            "/tickets",
            content=chunks() if chunked else b"x" * 300000,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert len(response.content) < 200
        temporal.start_workflow.assert_not_called()
        if chunked:
            assert sent < 30
        assert (await api.post("/tickets", json=TICKET)).status_code == 202


@pytest.mark.parametrize("escaped", [False, True])
async def test_full_valid_unicode_fields_fit_the_http_byte_budget(escaped):
    temporal = AsyncMock(spec=Client)
    payload = {
        **TICKET,
        "ticket_id": "x" * 80,
        "customer_id": "c" * 80,
        "subject": "🧪" * 500,
        "body": "🧪" * 20000,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=temporal)), base_url="http://test"
    ) as api:
        response = await api.post(
            "/tickets",
            content=json.dumps(payload, ensure_ascii=escaped).encode(),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 202


async def test_saturation_rejects_without_queueing_and_keeps_health_available():
    temporal = AsyncMock(spec=Client)
    temporal.service_client.check_health = AsyncMock(return_value=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()

    temporal.start_workflow.side_effect = blocked
    app = create_app(Settings(http_max_inflight=1), temporal)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        pending = asyncio.create_task(api.post("/tickets", json=TICKET))
        await entered.wait()
        try:
            busy = await api.post("/tickets", json=TICKET)
            assert busy.status_code == 503
            assert busy.headers["retry-after"] == "1"
            assert (await api.get("/health/live")).status_code == 200
            assert (await api.get("/health/ready")).status_code == 200
            temporal.start_workflow.assert_awaited_once()
        finally:
            release.set()
        assert (await pending).status_code == 202
        assert (await api.post("/tickets", json=TICKET)).status_code == 202


async def test_slow_body_is_bounded_and_releases_the_admission_slot():
    temporal = AsyncMock(spec=Client)
    app = create_app(Settings(http_body_timeout_seconds=0.02, http_max_inflight=1), temporal)

    async def stalled():
        await asyncio.Event().wait()
        yield b"never sent"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.post("/tickets", content=stalled())).status_code == 408
        temporal.start_workflow.assert_not_called()
        assert (await api.post("/tickets", json=TICKET)).status_code == 202


async def test_validation_detail_is_bounded_and_unicode_safe_for_unknown_keys():
    temporal = AsyncMock(spec=Client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=temporal)), base_url="http://test"
    ) as api:
        payload = {**TICKET, "\ud800": "x" * 100000, "subject": float("nan")}
        response = await api.post(
            "/tickets", content=json.dumps(payload), headers={"content-type": "application/json"}
        )
        assert response.status_code == 422
        assert len(response.content) < 2000
        assert "input" not in response.json()["detail"][0]
        assert "x" * 100 not in response.text


@pytest.mark.parametrize("depth", [1100, 20000])
async def test_deep_json_is_a_bounded_client_error(depth):
    temporal = AsyncMock(spec=Client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=temporal)), base_url="http://test"
    ) as api:
        response = await api.post(
            "/tickets",
            content=b'{"extra":' + b"[" * depth + b"0" + b"]" * depth + b"}",
            headers={"content-type": "application/json"},
        )
        assert response.status_code in (400, 422)
        assert len(response.content) < 2000
        temporal.start_workflow.assert_not_called()


async def test_slow_active_query_sheds_load_and_releases_its_slot():
    temporal = AsyncMock(spec=Client)
    handle = temporal.get_workflow_handle_for.return_value
    handle.describe = AsyncMock(
        return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
    )

    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    handle.query = AsyncMock(side_effect=stalled)
    app = create_app(Settings(http_query_timeout_seconds=0.02, http_query_concurrency=1), temporal)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get("/tickets/active")
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        handle.query.side_effect = None
        handle.query.return_value = TicketState(ticket_id="active", step="deciding")
        assert (await api.get("/tickets/active")).status_code == 200
