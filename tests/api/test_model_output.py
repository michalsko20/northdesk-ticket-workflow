"""Malformed model output must not make a safely completed ticket unreadable."""

import asyncio
import json
import uuid

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI
from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.glm import GLMProvider
from northdesk.api import create_app
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.workflows.ticket import TicketWorkflow


@pytest.fixture
async def glm_for_proposal(proposal):
    completion = {
        "id": "review-model-output",
        "object": "chat.completion",
        "created": 0,
        "model": "review-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "decision",
                            "type": "function",
                            "function": {
                                "name": "submit_decision",
                                "arguments": json.dumps(proposal),
                            },
                        }
                    ],
                },
            }
        ],
    }
    provider = GLMProvider("review-key-not-real", "review-model", "https://example.test/v4")
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="review-key-not-real",
        max_retries=0,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=completion))
        ),
    )
    try:
        yield provider
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "proposal",
    [
        {"unexpected": float("nan")},
        {"unexpected": "\ud800"},
        {
            "category": "account",
            "priority": "normal",
            "action": "reply",
            "message": "\ud800",
            "articles": ["KB-1"],
        },
    ],
    ids=["rejected-nan", "rejected-surrogate", "rejected-surrogate-reply"],
)
async def test_model_output_keeps_completed_ticket_readable(
    temporal_env, tmp_path, proposal, glm_for_proposal
):
    queue = f"model-output-{uuid.uuid4()}"
    activities = Activities(
        glm_for_proposal, Catalog(), FakeHelpdesk(str(tmp_path / "helpdesk.db"))
    )
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide_ticket, activities.validate_ticket, activities.act_on_ticket],
    ):
        handle = await temporal_env.client.start_workflow(
            TicketWorkflow.run,
            Ticket("model-output", "C-200", "Password", "Forgot password"),
            id="ticket/model-output",
            task_queue=queue,
        )
        state = await asyncio.wait_for(handle.result(), timeout=20)
        assert state.step == "completed"
        assert state.result is not None
        assert state.decision.action == "escalate"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(client=temporal_env.client), raise_app_exceptions=False
            ),
            base_url="http://test",
        ) as api:
            response = await api.get("/tickets/model-output")
            assert response.status_code == 200, response.text
            assert response.json()["step"] == "completed"
