"""Exercise actual HTTP framing; ASGITransport cannot detect TCP resets."""

import asyncio
import json
import socket
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import uvicorn
from temporalio.client import Client

from northdesk.api import create_app
from northdesk.settings import Settings


@pytest.fixture
async def saturated_server():
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()

    temporal = MagicMock(spec=Client)
    temporal.start_workflow = AsyncMock(side_effect=blocked)
    app = create_app(Settings(_env_file=None, http_max_inflight=1), temporal)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False)
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if serving.done():
                    await serving
                    raise AssertionError("HTTP server stopped before startup")
                await asyncio.sleep(0.01)
        yield base, entered, release, temporal
    finally:
        release.set()
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, timeout=5)
        finally:
            sock.close()


@pytest.mark.parametrize("chunked", [False, True])
async def test_overload_response_survives_streamed_valid_body_and_connection_reuse(
    saturated_server, chunked
):
    base, entered, release, temporal = saturated_server
    ticket = {
        "ticket_id": "blocked",
        "customer_id": "C-200",
        "subject": "Password",
        "body": "Forgot password",
    }
    body = json.dumps({**ticket, "ticket_id": "rejected", "body": "🧪" * 20000}).encode()

    async def upload():
        for offset in range(0, len(body), 8192):
            yield body[offset : offset + 8192]
            await asyncio.sleep(0.005)

    async with httpx.AsyncClient(base_url=base, timeout=5) as api:
        first = asyncio.create_task(api.post("/tickets", json=ticket))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            headers = {"content-type": "application/json"}
            if not chunked:
                headers["content-length"] = str(len(body))
            response = await api.post("/tickets", content=upload(), headers=headers)
            assert response.status_code == 503
            assert response.headers["retry-after"] == "1"
            assert "busy" in response.json()["detail"]
            temporal.start_workflow.assert_awaited_once()
            assert (await api.get("/health/live")).status_code == 200
        finally:
            release.set()
            assert (await first).status_code == 202
        assert (await api.post("/tickets", json={**ticket, "ticket_id": "next"})).status_code == 202
        assert temporal.start_workflow.await_count == 2
