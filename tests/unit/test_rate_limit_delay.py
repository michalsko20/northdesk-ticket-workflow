from datetime import timedelta

import httpx2
import pytest
from openai import AsyncOpenAI
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from northdesk.activities import Activities
from northdesk.agent.glm import GLMProvider
from northdesk.agent.providers import OpenAIProvider
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk


@pytest.mark.parametrize("mode", ["openai", "glm"])
async def test_provider_retry_after_reaches_temporal_activity_error(tmp_path, mode):
    calls = []

    def limited(request):
        calls.append(request)
        return httpx2.Response(
            429, headers={"Retry-After": "60"}, json={"error": {"message": "temporary rate limit"}}
        )

    provider = (
        OpenAIProvider("not-a-real-key", "test")
        if mode == "openai"
        else GLMProvider("not-a-real-key", "test", "https://example.test/v4")
    )
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="not-a-real-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(limited)),
    )
    try:
        activities = Activities(provider, Catalog(), FakeHelpdesk(str(tmp_path / "effects.db")))
        with pytest.raises(ApplicationError) as caught:
            await ActivityEnvironment().run(
                activities.decide_ticket, Ticket("quota", "C-200", "Password", "Forgot password")
            )
        assert len(calls) == 1
        assert not caught.value.non_retryable
        assert caught.value.next_retry_delay == timedelta(seconds=60)
    finally:
        await provider.close()
