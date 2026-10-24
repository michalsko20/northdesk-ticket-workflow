"""Check the real SDK exception boundary, including durable failure encoding."""

import logging
import traceback

import httpx2
import pytest
from openai import AsyncOpenAI
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from northdesk.activities import Activities
from northdesk.agent.glm import GLMProvider
from northdesk.agent.providers import OpenAIProvider
from northdesk.domain.models import Ticket
from northdesk.logging import JsonFormatter
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk

PRIVATE = "synthetic-private-provider-details"


@pytest.mark.parametrize("mode", ["openai", "glm"])
@pytest.mark.parametrize("failure", ["connect", "timeout", "backoff", "unauthorized"])
async def test_provider_details_do_not_enter_traceback_logs_or_temporal_failure(
    tmp_path, mode, failure
):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "connect":
            raise httpx2.ConnectError(PRIVATE, request=request)
        if failure == "timeout":
            raise httpx2.ReadTimeout(PRIVATE, request=request)
        return httpx2.Response(
            429 if failure == "backoff" else 401,
            headers={"retry-after": "301"},
            json={"error": {"message": PRIVATE}},
        )

    provider = (
        OpenAIProvider("fake-test-key", "test")
        if mode == "openai"
        else GLMProvider("fake-test-key", "test", "https://example.invalid/v4/")
    )
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="fake-test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    activities = Activities(provider, Catalog(), FakeHelpdesk(str(tmp_path / "effects.db")))
    try:
        with pytest.raises(ApplicationError) as caught:
            await ActivityEnvironment().run(
                activities.decide_ticket, Ticket("privacy", "C-200", "Password", "Forgot password")
            )
        error = caught.value
        permanent = failure in ("backoff", "unauthorized")
        assert error.type == ("PermanentAgentError" if permanent else "TransientAgentError")
        assert error.non_retryable is permanent
        assert len(requests) == 1
        assert PRIVATE not in "".join(traceback.format_exception(error))
        record = logging.LogRecord(
            "review",
            logging.ERROR,
            __file__,
            1,
            "activity failed",
            (),
            (type(error), error, error.__traceback__),
        )
        assert PRIVATE not in JsonFormatter().format(record)
        durable_failure = Failure()
        await DataConverter.default.encode_failure(error, durable_failure)
        assert PRIVATE.encode() not in durable_failure.SerializeToString()
    finally:
        await provider.close()
