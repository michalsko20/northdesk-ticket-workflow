import asyncio
import uuid
from dataclasses import asdict
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.providers import FakeProvider
from northdesk.api import create_app
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.settings import Settings
from northdesk.workflows.ticket import TicketWorkflow


@pytest.fixture
async def api(temporal_env, tmp_path):
    queue = f"api-{uuid.uuid4()}"
    settings = Settings(temporal_task_queue=queue)
    activities = Activities(FakeProvider(), Catalog(), FakeHelpdesk(str(tmp_path / "helpdesk.db")))
    application = create_app(settings, temporal_env.client)
    async with (
        Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[TicketWorkflow],
            activities=[
                activities.decide_ticket,
                activities.validate_ticket,
                activities.act_on_ticket,
            ],
        ),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client


def ticket(refund=False):
    return asdict(
        Ticket(
            f"api-{uuid.uuid4()}",
            "C-100" if refund else "C-200",
            "Charged twice" if refund else "Password",
            "Please refund" if refund else "Forgot password",
        )
    )


async def wait_for(api, ticket_id, step):
    for _ in range(200):
        response = await api.get(f"/tickets/{ticket_id}")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["step"] == step:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"Did not reach {step}")


async def test_submit_read_and_deduplicate_after_completion(api):
    payload = ticket()
    first = await api.post("/tickets", json=payload)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    state = await wait_for(api, payload["ticket_id"], "completed")
    assert state["result"]["action"] == "reply"
    duplicate = await api.post("/tickets", json=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["workflow_id"] == first.json()["workflow_id"]


async def test_concurrent_submissions_start_one_workflow(api):
    payload = ticket(refund=True)
    responses = await asyncio.gather(*(api.post("/tickets", json=payload) for _ in range(4)))
    assert all(r.status_code == 202 for r in responses)
    assert sum(not r.json()["duplicate"] for r in responses) == 1
    await wait_for(api, payload["ticket_id"], "awaiting_approval")
    assert (await api.post("/tickets", json={**payload, "body": "changed"})).json()["duplicate"]


async def test_concurrent_different_payloads_preserve_the_accepted_ticket(api):
    ticket_id = f"different-{uuid.uuid4()}"
    candidates = [
        ({"customer_id": "C-200", "subject": "Password", "body": "Forgot password"}, "reply"),
        ({"customer_id": "C-300", "subject": "API down", "body": "API outage"}, "escalate"),
        ({"customer_id": "C-100", "subject": "Lunch", "body": "Pizza?"}, "close"),
    ] * 8
    responses = await asyncio.gather(
        *(
            api.post("/tickets", json={"ticket_id": ticket_id, **payload})
            for payload, _ in candidates
        )
    )
    assert all(response.status_code == 202 for response in responses)
    winners = [
        index for index, response in enumerate(responses) if not response.json()["duplicate"]
    ]
    assert len(winners) == 1
    expected_action = candidates[winners[0]][1]
    state = await wait_for(api, ticket_id, "completed")
    assert state["result"]["action"] == expected_action


@pytest.mark.parametrize("approved,action", [(True, "refund"), (False, "escalate")])
async def test_refund_approval_api(api, approved, action):
    payload = ticket(refund=True)
    await api.post("/tickets", json=payload)
    await wait_for(api, payload["ticket_id"], "awaiting_approval")
    response = await api.post(
        f"/tickets/{payload['ticket_id']}/approval",
        json={
            "approved": approved,
            "reviewer": "operator",
        },
    )
    assert response.status_code == 200, response.text
    state = await wait_for(api, payload["ticket_id"], "completed")
    assert state["result"]["action"] == action
    late = await api.post(
        f"/tickets/{payload['ticket_id']}/approval",
        json={
            "approved": not approved,
            "reviewer": "operator",
        },
    )
    assert late.status_code == 409, late.text


async def test_not_found_and_invalid_input(api):
    assert (await api.get("/tickets/does-not-exist")).status_code == 404
    missing = await api.post(
        "/tickets/does-not-exist/approval",
        json={
            "approved": True,
            "reviewer": "operator",
        },
    )
    assert missing.status_code == 404
    assert (await api.post("/tickets", json={})).status_code == 422
    assert (await api.post("/tickets", json={**ticket(), "skip_rules": True})).status_code == 422
    assert (
        await api.post(
            "/tickets/any/approval",
            json={
                "approved": "false",
                "reviewer": "operator",
            },
        )
    ).status_code == 422


async def test_submission_does_not_require_a_worker(temporal_env):
    settings = Settings(temporal_task_queue=f"unpolled-{uuid.uuid4()}")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, temporal_env.client)),
        base_url="http://test",
    ) as client:
        payload = ticket()
        response = await client.post("/tickets", json=payload)
        assert response.status_code == 202
        handle = temporal_env.client.get_workflow_handle(response.json()["workflow_id"])
        description = await handle.describe(rpc_timeout=timedelta(seconds=2))
        assert description.close_time is None
        await handle.terminate("Test completed")


@pytest.mark.parametrize(
    "status,expected", [(RPCStatusCode.UNAVAILABLE, 503), (RPCStatusCode.DEADLINE_EXCEEDED, 504)]
)
async def test_unavailable_temporal_returns_clear_error(status, expected):
    client = AsyncMock(spec=Client)
    client.start_workflow.side_effect = RPCError("backend failed", status, b"")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client=client)),
        base_url="http://test",
    ) as api:
        response = await api.post("/tickets", json=ticket())
        assert response.status_code == expected
