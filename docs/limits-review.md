# Limits and operating practices, 2026-10-23

The additional checks found three issues: final workflow payload growth (F6),
unclassified JSON parser resource failures (F7), and lost provider retry delays
(F8). The load probe also exposes an operating limit: 200 concurrent submissions
followed by intensive polling produce state-read timeouts while workflows still
eventually finish correctly. No application code was changed in this stage.

## Load and read availability

The test uses a separate `northdesk-limits` Compose project, fake mode, empty keys,
one production worker, its configured limit of ten concurrent activities, and
separate ports/volumes. The Docker Desktop VM has shared resources; this is a
bounded local exercise, not a production capacity or live-model benchmark.

| Concurrent submissions | Unique tickets | Acceptance p95 | Accepted requests/s | Workflow duration p95 | State-read 504 responses |
| --- | --- | --- | --- | --- | --- |
| 1 | 150 | 49.71 ms | 31.84 | 0.78 s | 0 |
| 10 | 150 | 94.66 ms | 149.88 | 5.06 s | 0 |
| 50 | 150 | 360.61 ms | 143.75 | 5.99 s | 0 |
| 100 | 150 | 1299.73 ms | 100.84 | 5.06 s | 0 |
| 200, initial run | 500 | 1246.98 ms | 170.10 | 20.49 s | 177 |
| 200, controlled repeat | 500 | 1480.01 ms | 139.52 | 21.05 s | 184 |

Acceptance is HTTP 202, not workflow completion. Workflow duration comes from
Temporal start/close timestamps, independently of the polling client. The initial
200-concurrency run overlapped the synthetic payload experiment; the controlled
repeat ran without that experiment and reproduced the read degradation.

Across the three runs:

- All **4,090 submission requests** returned 202. They represent **1,693 unique
  tickets**, including duplicate bursts of 400, 1,000 and 1,000 requests.
- Every unique ticket reached the expected completed reply. Independent SQLite
  inspection found exactly one correct effect per tested ticket.
- The duplicate bursts selected exactly one new start each. All repeated starts
  returned `duplicate=true`; no additional effects were created.
- Ninety maximum-length Unicode tickets completed across the three runs.
- All 378 liveness probes returned 200. Liveness did not imply availability of
  workflow state reads under the heavier polling load.
- The worker reached **527 OS threads** in one observation. Installed SDK
  inspection shows a default workflow executor of up to 500 threads when
  `max_concurrent_workflow_tasks` is not supplied. The application explicitly
  limits activities, but leaves workflow concurrency/cache at SDK defaults.
  This observation does not prove that thread count alone caused the timeouts.

Raw evidence: [baseline](review-artifacts/limits/load.json),
[initial high load](review-artifacts/limits/load-high.json),
[controlled high-load repeat](review-artifacts/limits/load-high-isolated.json),
[Temporal durations](review-artifacts/limits/temporal-cross-check.json),
[effect counts](review-artifacts/limits/sqlite-cross-check.json),
[resource snapshot](review-artifacts/limits/resources.json),
[environment](review-artifacts/limits/environment.json).
Resource snapshots are point-in-time observations, not peak measurements.
The JSON `success` field means eventual correctness; use
`all_http_operations_succeeded` and `query_errors` to assess read availability.

## F6 — P2: accumulated state can exceed the completion payload limit

A valid reply proposal with an 800,000-character message passes domain policy.
The same text appears in the retained proposal, validated decision and action
receipt. Each activity payload fits the SDK's 2 MiB blob limit, but the final
workflow result is **2,400,717 bytes**. The completion command is rejected after
the effect is already committed.

The real Temporal dev-server experiment observed repeated `PayloadsTooLarge`
completion failures, an unreadable Query, one committed effect and a still-running
workflow after a bounded 20-second observation. The synthetic execution was then
terminated for cleanup. An independent repeat reproduced it.

Controls: a 100,000-character message completed successfully; a 2,200,000-character
proposal was rejected earlier by SDK payload enforcement and the bounded activity
retry path completed an escalation. Thus, the problematic range is below the
individual activity limit but above the final aggregate state limit.

This is **fault injection** of an oversized provider response, not evidence that
the currently configured GLM/OpenAI token caps normally generate this text size.
It demonstrates the missing local output-size invariant if a provider, proxy,
adapter or future data source exceeds expectations.

Repair: validate output byte budgets before performing the effect, keep completion
results compact, and avoid duplicating large data in workflow payloads. Consider
external storage plus identifiers for genuinely large audit data. Recovery of
already-recorded oversized state requires a workflow compatibility plan.
Temporal describes payload/history limits and the external-storage pattern in its
[worker guidance](https://docs.temporal.io/best-practices/worker).

Evidence: [payload controls](review-artifacts/limits/payloads.json),
[repeat](review-artifacts/limits/payloads-recheck.json),
[SDK/runtime log](review-artifacts/limits/payload-runtime.log),
`tests/unit/test_size_limits.py` (strict xfail).

## F7 — P3: JSON decoder resource failures bypass tool error classification

`execute_tool` maps `JSONDecodeError` to a permanent invalid-argument error, but
Python can also raise `RecursionError` for excessive JSON nesting or `ValueError`
when an integer exceeds its configured digit limit. These currently escape as
unclassified exceptions. Real activities then use Temporal's general retry path
instead of the intended permanent invalid-tool-argument path.

Two strict xfail cases use the real decoder. The nesting probe is deliberately
extreme and is not a claim about ordinary model output. The integer probe needs
only about 4.3 KB of tool JSON on the default Python configuration.

Repair: bound argument bytes/structure and map decoder resource errors to the
chosen permanent tool error, preserving cancellation and unexpected operational
exceptions rather than adding a broad catch-all.

## F8 — P2: Retry-After disappears before the Temporal retry

With a provider HTTP 429 and `Retry-After: 60`, both adapters create a transient
error but discard the delay. The real activity wrapper raises `ApplicationError`
with no `next_retry_delay`. Its normal retry policy therefore uses approximately
one- and two-second backoff, potentially exhausting all attempts before the
provider's requested wait has passed.

`tests/unit/test_rate_limit_delay.py` exercises the real SDK transport, adapters
and activity wrapper. Both cases preserve transient classification and one SDK
request but fail the delay-propagation assertion.

Disabling SDK retries is appropriate when Temporal owns the retry budget. The
application must then propagate validated server-directed delays itself. The
[official SDK implementation](https://github.com/openai/openai-python/blob/main/src/openai/_base_client.py)
handles Retry-After in its own retry path; that path is disabled here.
Respect the remaining activity schedule budget when choosing the Temporal delay.

## Request bytes and field lengths are separate limits

The maximum Unicode request used about 82 KB of UTF-8 JSON and was accepted.
Separately, adding over 2 MiB of JSON whitespace to a small valid ticket also
returned 202, including when sent as a chunked body. A 2 MiB invalid field returned
422 with a response of roughly the same size because validation echoed the input.

The field-length contract works, but there is no total JSON body-byte cap before
parsing, and error response size is not bounded independently. Configure a raw
byte limit at the ingress/ASGI boundary, including streamed bodies, and bounded
validation details. Multipart form limits do not impose a JSON body limit.
Starlette distinguishes [buffered JSON/body access and streaming](https://raw.githubusercontent.com/Kludex/starlette/main/docs/requests.md).
Measurements: [request sizes](review-artifacts/limits/requests.json).

This is a missing operating control, not a claim that a particular byte threshold
is already part of the application's public contract.

## Practices cross-check and priorities

| Area | Current result | Recommended action |
| --- | --- | --- |
| Input characters, IDs and strict approval boolean | Explicit boundaries tested | Preserve the existing checks |
| Whole request bytes / validation response bytes | No early total cap; oversized input is echoed | Add a justified byte budget and bounded errors |
| Model rounds, tools and activity retries | Proven whole-ticket maximum of 18 provider steps and 36 read-tool calls in the exercised failure path | Preserve finite budgets; expose usage metrics |
| Model token request caps | OpenAI 2000 / GLM 4096 verified on SDK requests | Retain caps and independently validate returned bytes |
| Temporal payload size | F6 after an effect is committed | Guard aggregate output before action; compact the workflow result |
| Retry ownership | SDK retries disabled; Temporal budgets finite | Preserve single ownership; repair Retry-After propagation |
| Activity timeout vs in-flight operation | Retry completed while the original committed call still ran | Preserve durable idempotency and reconciliation semantics |
| Worker concurrency and cache | Ten activity slots; workflow executor/cache use defaults | Set and measure explicit workflow/cache budgets for the deployment |
| API overload behavior | State reads return 504 under the larger burst | Add bounded admission/polling and queue/latency alerts |
| Health signals | Liveness stays green during query timeouts | Monitor queue age, query errors and worker availability separately |
| Workflow upgrades | Five frozen replay fixtures pass | Require replay checks for workflow changes |
| SQLite lifecycle | Earlier F4 remains | Close connections explicitly |

Uvicorn provides [concurrency limits and overload rejection](https://uvicorn.dev/settings/#resource-limits).
Its connection/task limit is different from a requests-per-second rate limit or a
JSON body-size limit. Temporal recommends measuring worker settings for the
workload rather than relying on defaults; no single configuration is established
as optimal by these tests.

The new real timeout-overlap test passed in 16.44 seconds: attempt two returned
the stored refund receipt while attempt one was still blocked after commit. There
was one completed refund and one row. This extends the previous lost-acknowledgement
and worker-process-crash evidence to overlapping activity attempts.

## Repeat

```bash
docker compose -p northdesk-limits -f docker-compose.yml -f tests/limits/compose.yml up --build -d --wait
uv run --locked python scripts/load_check.py
uv run --locked python scripts/load_check.py --concurrency 200 --tickets 500 --burst 1000 --output /tmp/northdesk-load-high.json
uv run --locked python scripts/request_size_check.py
uv run --locked python scripts/payload_check.py
make test
docker compose -p northdesk-limits -f docker-compose.yml -f tests/limits/compose.yml down
```

Use a new output path to preserve previous measurements. Generated IDs are fresh
per run. The payload script only terminates its own synthetic running executions;
its fake effect databases are temporary. Compose shutdown preserves named volumes.
This test setup does not modify the original GLM service or call a paid model.
