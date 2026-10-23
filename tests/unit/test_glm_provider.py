import json
from dataclasses import asdict

import httpx2
import pytest
from openai import AsyncOpenAI

from northdesk.agent.glm import GLMProvider
from northdesk.agent.loop import decide
from northdesk.agent.providers import MalformedResponse, PermanentAgentError, TransientAgentError
from northdesk.domain.models import Decision, Ticket
from northdesk.services.catalog import Catalog
from northdesk.settings import Settings


def completion(message, finish_reason="stop"):
    return {
        "id": "glm-test",
        "object": "chat.completion",
        "created": 0,
        "model": "glm-test",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


async def provider_with_transport(handler):
    provider = GLMProvider(
        "not-a-real-key", "glm-test", "https://api.z.ai/api/paas/v4/chat/completions"
    )
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="not-a-real-key",
        base_url="https://api.z.ai/api/paas/v4/",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return provider


async def test_glm_protocol_preserves_tool_ids_and_reasoning_between_rounds():
    requests = []

    def handler(request):
        assert str(request.url) == "https://api.z.ai/api/paas/v4/chat/completions"
        payload = json.loads(request.content)
        requests.append(payload)
        assert "response_format" not in payload
        assert payload["tools"][-1]["function"]["name"] == "submit_decision"
        assert payload["reasoning_effort"] == "low"
        assert payload["tools"][0]["function"]["name"] == "get_customer"
        if len(requests) == 1:
            return httpx2.Response(
                200,
                json=completion(
                    {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "opaque-test-reasoning",
                        "tool_calls": [
                            {
                                "id": "glm-call",
                                "type": "function",
                                "function": {
                                    "name": "get_customer",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "tool_calls",
                ),
            )
        messages = payload["messages"]
        assert messages[2]["role"] == "assistant"
        assert messages[2]["reasoning_content"] == "opaque-test-reasoning"
        assert messages[3]["role"] == "tool"
        assert messages[3]["tool_call_id"] == "glm-call"
        assert json.loads(messages[3]["content"])["result"]["plan"] == "free"
        return httpx2.Response(
            200,
            json=completion(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "final",
                            "type": "function",
                            "function": {
                                "name": "submit_decision",
                                "arguments": json.dumps(
                                    asdict(Decision("other", "low", "close", reason="Out of scope"))
                                ),
                            },
                        }
                    ],
                },
                "tool_calls",
            ),
        )

    provider = await provider_with_transport(handler)
    try:
        result = await decide(Ticket("glm", "C-200", "Lunch", "Pizza?"), provider, Catalog())
        assert result["action"] == "close"
        assert len(requests) == 2
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "base", ["https://api.z.ai/api/paas/v4/", "https://api.z.ai/api/paas/v4/chat/completions"]
)
async def test_glm_accepts_base_url_or_full_endpoint(base):
    provider = GLMProvider("not-a-real-key", "glm-test", base)
    try:
        assert str(provider.client.base_url) == "https://api.z.ai/api/paas/v4/"
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "status,error",
    [
        (408, TransientAgentError),
        (429, TransientAgentError),
        (503, TransientAgentError),
        (401, PermanentAgentError),
    ],
)
async def test_glm_errors_have_no_hidden_retries_or_secret_error_bodies(status, error):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(status, json={"error": {"message": "private-provider-details"}})

    provider = await provider_with_transport(handler)
    try:
        with pytest.raises(error) as caught:
            await provider.step([], [])
        assert len(calls) == 1
        assert "private-provider-details" not in str(caught.value)
        assert caught.value.__suppress_context__
    finally:
        await provider.close()


@pytest.mark.parametrize("content,finish", [("not JSON", "stop"), ("{}", "length")])
async def test_glm_malformed_or_truncated_response_is_bounded(content, finish):
    provider = await provider_with_transport(
        lambda request: httpx2.Response(
            200,
            json=completion(
                {
                    "role": "assistant",
                    "content": content,
                },
                finish,
            ),
        )
    )
    try:
        with pytest.raises(MalformedResponse):
            await provider.step([], [])
    finally:
        await provider.close()


def test_glm_requires_its_own_key_and_fake_does_not():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ZAI_API_KEY is required"):
        Settings(_env_file=None, llm_mode="glm", zai_api_key="")
    settings = Settings(_env_file=None, llm_mode="glm", zai_api_key="not-a-real-key")
    assert settings.glm_model == "glm-5.3-flash"


@pytest.mark.parametrize("arguments,include_read", [("invalid-json", False), ("{}", True)])
async def test_glm_rejects_malformed_final_proposals_and_mixed_batches(arguments, include_read):
    calls = [
        {
            "id": "final",
            "type": "function",
            "function": {
                "name": "submit_decision",
                "arguments": arguments,
            },
        }
    ]
    if include_read:
        calls.append(
            {
                "id": "read",
                "type": "function",
                "function": {
                    "name": "get_customer",
                    "arguments": "{}",
                },
            }
        )
    provider = await provider_with_transport(
        lambda request: httpx2.Response(
            200,
            json=completion(
                {
                    "role": "assistant",
                    "tool_calls": calls,
                },
                "tool_calls",
            ),
        )
    )
    try:
        with pytest.raises(MalformedResponse):
            await provider.step([], [])
    finally:
        await provider.close()


@pytest.mark.parametrize("error", [httpx2.ConnectError, httpx2.ReadTimeout])
async def test_glm_transport_failures_are_retryable_without_sdk_retries(error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("synthetic transport failure", request=request)

    provider = await provider_with_transport(handler)
    try:
        with pytest.raises(TransientAgentError, match="glm_connection_error"):
            await provider.step([], [])
        assert len(calls) == 1
    finally:
        await provider.close()


async def test_empty_glm_choices_are_a_bounded_malformed_response():
    provider = await provider_with_transport(
        lambda request: httpx2.Response(
            200, json={**completion({"role": "assistant"}), "choices": []}
        )
    )
    try:
        with pytest.raises(MalformedResponse, match="glm_empty_response"):
            await provider.step([], [])
    finally:
        await provider.close()
