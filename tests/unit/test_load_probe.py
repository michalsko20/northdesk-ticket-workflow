"""A recovered request must not hide an unexpected load-test error."""

import runpy
from pathlib import Path

import httpx
import pytest

phase = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/load_check.py"))["phase"]


@pytest.mark.parametrize("first_read", [200, 503, 500, "disconnect"])
async def test_load_gate_distinguishes_admission_retries_from_unexpected_errors(first_read):
    reads = 0

    def handler(request):
        nonlocal reads
        if request.method == "POST":
            return httpx.Response(202, json={"duplicate": False})
        reads += 1
        if reads == 1:
            if first_read == "disconnect":
                raise httpx.ReadError("synthetic disconnect", request=request)
            if first_read != 200:
                return httpx.Response(
                    first_read, headers={"Retry-After": "0.1"} if first_read == 503 else {}
                )
        return httpx.Response(
            200, json={"step": "completed", "result": {"action": "reply"}, "error": None}
        )

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await phase(client, "gate", 1, 1, "review")
    assert result["eventual_completion"] is True
    assert result["success"] is (first_read in (200, 503))
    assert result["no_unexpected_http_errors"] is (first_read in (200, 503))
    assert result["all_http_operations_succeeded"] is (first_read == 200)
    if first_read != 200:
        key = "ReadError" if first_read == "disconnect" else str(first_read)
        assert result["query_errors"] == {key: 1}


async def test_admission_retry_reuses_ticket_and_keeps_raw_rejection_count():
    payloads = []

    def handler(request):
        if request.method == "POST":
            payloads.append(request.content)
            if len(payloads) == 1:
                return httpx.Response(503, headers={"Retry-After": "0.1"})
            return httpx.Response(202, json={"duplicate": False})
        return httpx.Response(
            200, json={"step": "completed", "result": {"action": "reply"}, "error": None}
        )

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await phase(client, "admission", 1, 1, "review")
    assert result["success"] is True
    assert result["all_http_operations_succeeded"] is False
    assert result["http_statuses"] == {"503": 1, "202": 1}
    assert len(payloads) == 2 and payloads[0] == payloads[1]
