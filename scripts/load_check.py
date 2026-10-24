"""Bounded local HTTP load probe. Run against the isolated limits Compose project."""

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from pathlib import Path

import httpx


def latency(values: list[float]) -> dict:
    ordered = sorted(values)
    return (
        {
            f"p{percentile}_ms": round(
                ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)] * 1000, 2
            )
            for percentile in (50, 95, 99)
        }
        if ordered
        else {}
    )


async def phase(client, label, count, concurrency, suffix, *, duplicate=False, unicode_body=False):
    semaphore = asyncio.Semaphore(concurrency)
    statuses = Counter()
    durations = []
    duplicate_flags = Counter()
    expected = {}
    failures = []

    async def submit(index):
        ticket_id = f"limit-{suffix}-{label}-{0 if duplicate else index}"
        expected[ticket_id] = "reply"
        payload = {
            "ticket_id": ticket_id,
            "customer_id": "C-200",
            "subject": "Password",
            "body": "🧪" * 20000 if unicode_body else "Forgot password",
        }
        async with semaphore:
            started = time.perf_counter()
            try:
                for _attempt in range(8):
                    response = await client.post("/tickets", json=payload)
                    statuses[str(response.status_code)] += 1
                    if response.status_code != 503 or "retry-after" not in response.headers:
                        break
                    # Admission rejects before dispatch. Reusing the ID also
                    # makes a transport-ambiguous start safe to repeat.
                    delay = min(5, max(0.1, float(response.headers["retry-after"])))
                    await asyncio.sleep(delay + (index % 7) * 0.01)
                if response.status_code == 202:
                    duplicate_flags[str(response.json()["duplicate"])] += 1
                else:
                    failures.append({"ticket_id": ticket_id, "status": response.status_code})
            except httpx.HTTPError as exc:
                statuses[type(exc).__name__] += 1
                failures.append({"ticket_id": ticket_id, "error": type(exc).__name__})
            durations.append(time.perf_counter() - started)

    started = time.perf_counter()
    await asyncio.gather(*(submit(index) for index in range(count)))
    submission_seconds = time.perf_counter() - started
    pending = set(expected)
    completed = {}
    query_errors = Counter()
    unexpected_query_errors = Counter()
    deadline = time.monotonic() + 90

    async def read(ticket_id):
        async with semaphore:
            try:
                response = await client.get(f"/tickets/{ticket_id}")
                if response.status_code != 200:
                    query_errors[str(response.status_code)] += 1
                    if response.status_code == 503 and "retry-after" in response.headers:
                        await asyncio.sleep(
                            min(5, max(0.1, float(response.headers["retry-after"])))
                        )
                    else:
                        unexpected_query_errors[str(response.status_code)] += 1
                    return
                state = response.json()
                if state["step"] in ("completed", "failed"):
                    completed[ticket_id] = {
                        "step": state["step"],
                        "action": (state["result"] or {}).get("action"),
                        "error": state["error"],
                    }
                    pending.discard(ticket_id)
            except httpx.HTTPError as exc:
                query_errors[type(exc).__name__] += 1
                unexpected_query_errors[type(exc).__name__] += 1

    while pending and time.monotonic() < deadline:
        await asyncio.gather(*(read(ticket_id) for ticket_id in list(pending)))
        if pending:
            await asyncio.sleep(0.2)
    correct = all(
        state["step"] == "completed" and state["action"] == expected[ticket_id]
        for ticket_id, state in completed.items()
    )
    expected_starts = 1 if duplicate else count
    eventual_completion = (
        not failures
        and not pending
        and correct
        and duplicate_flags["False"] == expected_starts
        and duplicate_flags["True"] == count - expected_starts
    )
    no_unexpected_http_errors = not failures and not unexpected_query_errors
    success = eventual_completion and no_unexpected_http_errors
    return {
        "label": label,
        "requests": count,
        "concurrency": concurrency,
        "unique_tickets": len(expected),
        "success": success,
        "eventual_completion": eventual_completion,
        "all_http_operations_succeeded": not failures and not query_errors and not statuses["503"],
        "no_unexpected_http_errors": no_unexpected_http_errors,
        "latency_definition": "Logical submission including admission retries",
        "submission_seconds": round(submission_seconds, 3),
        "submission_requests_per_second": round(count / submission_seconds, 2),
        "settled_seconds": round(time.perf_counter() - started, 3),
        "submission_latency": latency(durations),
        "http_statuses": dict(statuses),
        "duplicate_flags": dict(duplicate_flags),
        "query_errors": dict(query_errors),
        "unexpected_query_errors": dict(unexpected_query_errors),
        "failures": failures,
        "pending": sorted(pending),
        "completed": completed,
    }


async def run(args):
    suffix = str(time.time_ns())[-12:]
    health = []
    stop = asyncio.Event()
    # Uvicorn closes idle connections after 5 s. Retire this client's pool
    # earlier so it does not reuse a socket at the server's idle deadline.
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=128, keepalive_expiry=2)
    async with (
        httpx.AsyncClient(base_url=args.base_url, timeout=15, limits=limits) as client,
        httpx.AsyncClient(base_url=args.base_url, timeout=5) as health_client,
    ):
        (await client.get("/health/ready")).raise_for_status()

        async def probe():
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    response = await health_client.get("/health/live")
                    health.append(
                        {"status": response.status_code, "seconds": time.perf_counter() - started}
                    )
                except httpx.HTTPError as exc:
                    health.append(
                        {"status": type(exc).__name__, "seconds": time.perf_counter() - started}
                    )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except TimeoutError:
                    pass

        monitor = asyncio.create_task(probe())
        try:
            phases = []
            for concurrency in args.concurrency:
                result = await phase(client, f"c{concurrency}", args.tickets, concurrency, suffix)
                phases.append(result)
                print(
                    json.dumps({key: value for key, value in result.items() if key != "completed"}),
                    flush=True,
                )
            phases.append(
                await phase(client, "duplicates", args.burst, 100, suffix, duplicate=True)
            )
            phases.append(await phase(client, "max-unicode", 30, 10, suffix, unicode_body=True))
        finally:
            stop.set()
            await monitor
    success = (
        bool(health)
        and all(item["success"] for item in phases)
        and all(item["status"] == 200 for item in health)
    )
    result = {
        "profile": "isolated fake mode, one worker, activity concurrency 10",
        "base_url": args.base_url,
        "suffix": suffix,
        "success": success,
        "all_http_operations_succeeded": success
        and all(item["all_http_operations_succeeded"] for item in phases),
        "no_unexpected_http_errors": success
        and all(item["no_unexpected_http_errors"] for item in phases),
        "success_definition": "Eventual correct completion with no unexpected HTTP/transport "
        "errors and healthy probes; explicit 503/Retry-After is retried and reported",
        "phases": phases,
        "health": {
            "samples": len(health),
            "statuses": dict(Counter(str(item["status"]) for item in health)),
            "latency": latency([item["seconds"] for item in health]),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"success": success, "output": str(args.output), "health": result["health"]}))
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--tickets", type=int, default=150)
    parser.add_argument("--burst", type=int, default=400)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 10, 50, 100])
    parser.add_argument("--output", type=Path, default=Path("artifacts/load.json"))
    args = parser.parse_args()
    if not 1 <= args.tickets <= 500 or not 1 <= args.burst <= 1000:
        parser.error("Use 1–500 tickets per phase and 1–1000 duplicate requests")
    if any(not 1 <= value <= 200 for value in args.concurrency):
        parser.error("Concurrency must be between 1 and 200")
    raise SystemExit(asyncio.run(run(args)))
