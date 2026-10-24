from datetime import UTC, datetime, timedelta

import pytest

from northdesk.agent.providers import PermanentAgentError, provider_retry_delay


@pytest.mark.parametrize(
    "headers,seconds",
    [
        ({}, None),
        ({"Retry-After": "60"}, 60),
        ({"retry-after-ms": "1500"}, 1.5),
        ({"retry-after-ms": "0", "retry-after": "2"}, 2),
        ({"retry-after-ms": "bad", "retry-after": "3"}, 3),
        ({"retry-after": "garbage"}, None),
        ({"retry-after": "-1"}, None),
        ({"retry-after": "NaN"}, None),
        ({"retry-after": "Infinity"}, None),
        ({"Retry-After": "Sat, 24 Oct 2026 10:01:00 GMT"}, 60),
        ({"Retry-After": "Sat, 24 Oct 2026 09:00:00 GMT"}, None),
        ({"retry-after": "300"}, 300),
    ],
)
def test_retry_header_parsing_and_bounds(headers, seconds):
    now = datetime(2026, 10, 24, 10, tzinfo=UTC)
    assert provider_retry_delay(headers, now) == (timedelta(seconds=seconds) if seconds else None)


def test_backoff_longer_than_activity_budget_does_not_retry_too_early():
    with pytest.raises(PermanentAgentError, match="backoff_exceeds_activity_budget"):
        provider_retry_delay({"retry-after": "301"})
