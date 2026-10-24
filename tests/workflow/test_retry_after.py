import json
import uuid
from dataclasses import asdict

import httpx2
import pytest
from openai import AsyncOpenAI
from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.glm import GLMProvider
from northdesk.agent.providers import OpenAIProvider
from northdesk.domain.models import Decision, Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.workflows.ticket import TicketWorkflow


@pytest.mark.parametrize("mode", ["openai", "glm"])
async def test_temporal_waits_for_provider_retry_after_before_repeating(
    mode, temporal_env, tmp_path
):
    requests = []
    proposal = asdict(Decision("other", "low", "close", reason="Out of scope"))

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                429, headers={"Retry-After": "60"}, json={"error": {"message": "rate limited"}}
            )
        if mode == "glm":
            value = {
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(proposal)},
                    }
                ],
            }
        else:
            value = {
                "id": "test",
                "object": "response",
                "created_at": 0,
                "model": "test",
                "status": "completed",
                "output": [
                    {
                        "id": "message",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": json.dumps(proposal), "annotations": []}
                        ],
                    }
                ],
            }
        return httpx2.Response(200, json=value)

    provider = (
        OpenAIProvider("test-not-real", "test")
        if mode == "openai"
        else GLMProvider("test-not-real", "test", "https://example.test/v4")
    )
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="test-not-real",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    activities = Activities(provider, Catalog(), FakeHelpdesk(str(tmp_path / "effects.db")))
    queue = f"retry-after-{uuid.uuid4()}"
    try:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[TicketWorkflow],
            activities=[
                activities.decide_ticket,
                activities.validate_ticket,
                activities.act_on_ticket,
            ],
        ):
            handle = await temporal_env.client.start_workflow(
                TicketWorkflow.run,
                Ticket("quota", "C-200", "Lunch", "Pizza?"),
                id="ticket/quota",
                task_queue=queue,
            )
            state = await handle.result()
            assert state.step == "completed" and state.result["action"] == "close"
            assert len(requests) == 2
            description = await handle.describe()
            assert (description.close_time - description.start_time).total_seconds() >= 60
    finally:
        await provider.close()
