"""Known HTTP input failures; --runxfail makes the reproductions fail visibly."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from temporalio.client import Client

from northdesk.api import create_app


@pytest.fixture
async def input_api():
    temporal = AsyncMock(spec=Client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=temporal), raise_app_exceptions=False),
        base_url="http://test",
    ) as api:
        yield api, temporal


TICKET = {"ticket_id": "break-input", "customer_id": "C-100", "subject": "Test", "body": "Test"}


@pytest.mark.xfail(strict=True, reason="F1: validation errors cannot serialize lone surrogates")
@pytest.mark.parametrize("field", ["ticket_id", "subject", "body", "unexpected"])
async def test_lone_surrogate_is_a_client_error(input_api, field):
    api, temporal = input_api
    response = await api.post(
        "/tickets",
        content=json.dumps({**TICKET, field: "\ud800"}),
        headers={"content-type": "application/json"},
    )
    temporal.start_workflow.assert_not_called()
    assert response.status_code == 422, response.text


@pytest.mark.xfail(strict=True, reason="F1: validation errors cannot serialize nonfinite floats")
@pytest.mark.parametrize("number", ["NaN", "Infinity", "1e400"])
async def test_nonfinite_number_is_a_client_error(input_api, number):
    api, temporal = input_api
    response = await api.post(
        "/tickets/any/approval",
        content='{"approved":' + number + ',"reviewer":"operator"}',
        headers={"content-type": "application/json"},
    )
    temporal.get_workflow_handle_for.assert_not_called()
    assert response.status_code == 422, response.text


@pytest.mark.xfail(strict=True, reason="F2: error encoder recurses through rejected JSON")
async def test_deeply_nested_json_is_a_client_error(input_api):
    api, temporal = input_api
    # About 2.2 KB: this is a recursion-boundary probe, not a large-body load test.
    response = await api.post(
        "/tickets",
        content=b'{"extra":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
        headers={"content-type": "application/json"},
    )
    temporal.start_workflow.assert_not_called()
    assert response.status_code in (400, 422), response.text


@pytest.mark.parametrize(
    "payload",
    [
        {**TICKET, "body": " "},
        {**TICKET, "body": "x" * 20001},
        {**TICKET, "ticket_id": "../other"},
        {**TICKET, "ticket_id": "x" * 81},
        {**TICKET, "customer_id": ["C-100"]},
        {**TICKET, "skip_rules": True},
    ],
)
async def test_invalid_ticket_never_starts_workflow(input_api, payload):
    api, temporal = input_api
    response = await api.post("/tickets", json=payload)
    assert response.status_code == 422
    temporal.start_workflow.assert_not_called()


async def test_legitimate_unicode_is_accepted(input_api):
    api, temporal = input_api
    response = await api.post("/tickets", json={**TICKET, "body": "Zażółć gęślą jaźń 🧪"})
    assert response.status_code == 202
    temporal.start_workflow.assert_awaited_once()


@pytest.mark.parametrize("approved", ["true", 1, 0, None, [], {}])
async def test_approval_coercion_is_rejected(input_api, approved):
    api, temporal = input_api
    response = await api.post(
        "/tickets/any/approval", json={"approved": approved, "reviewer": "operator"}
    )
    assert response.status_code == 422
    temporal.get_workflow_handle_for.assert_not_called()
