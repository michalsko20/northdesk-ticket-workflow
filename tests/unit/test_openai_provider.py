import json
from dataclasses import asdict

import httpx2
import pytest
from openai import AsyncOpenAI

from northdesk.agent.loop import decide
from northdesk.agent.providers import (
    MalformedResponse,
    OpenAIProvider,
    PermanentAgentError,
    TransientAgentError,
)
from northdesk.domain.models import Decision, Ticket
from northdesk.services.catalog import Catalog


def response(output):
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": "completed",
        "output": output,
    }


def message(text):
    return {
        "id": "msg-test",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


async def provider_with_transport(handler):
    provider = OpenAIProvider("test-key-not-real", "test-model")
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="test-key-not-real",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return provider


async def test_real_sdk_round_trip_keeps_tool_call_and_returns_its_result():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx2.Response(
                200,
                json=response(
                    [
                        {
                            "id": "fc-test",
                            "type": "function_call",
                            "call_id": "call-test",
                            "name": "get_customer",
                            "arguments": "{}",
                            "status": "completed",
                        }
                    ]
                ),
            )
        items = payload["input"]
        assert items[1]["call_id"] == "call-test"
        tool_result = items[2]
        assert tool_result["type"] == "function_call_output"
        assert tool_result["call_id"] == "call-test"
        customer = json.loads(tool_result["output"])["result"]
        assert customer["plan"] == "free"
        result = asdict(Decision("other", "low", "close", reason="Out of scope"))
        return httpx2.Response(200, json=response([message(json.dumps(result))]))

    provider = await provider_with_transport(handler)
    try:
        decision = await decide(Ticket("sdk", "C-200", "Lunch", "Pizza?"), provider, Catalog())
        assert decision["action"] == "close"
        assert len(requests) == 2
        assert requests[0]["store"] is False
        assert requests[0]["text"]["format"]["strict"] is True
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "status,error",
    [
        (408, TransientAgentError),
        (429, TransientAgentError),
        (500, TransientAgentError),
        (401, PermanentAgentError),
        (400, PermanentAgentError),
    ],
)
async def test_provider_classifies_errors_without_hidden_retries(status, error):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(status, json={"error": {"message": "test error", "type": "test"}})

    provider = await provider_with_transport(handler)
    try:
        with pytest.raises(error):
            await provider.step([], [])
        assert len(requests) == 1
    finally:
        await provider.close()


async def test_refusal_or_invalid_json_is_bounded_by_activity_retries():
    provider = await provider_with_transport(
        lambda request: httpx2.Response(200, json=response([message("not JSON")]))
    )
    try:
        with pytest.raises(MalformedResponse):
            await provider.step([], [])
    finally:
        await provider.close()
