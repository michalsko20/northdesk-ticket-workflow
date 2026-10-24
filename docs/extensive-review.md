# Extensive code review — 2026-10-24

Baseline: `d39a825` on `test/adversarial-inputs`. This review covers the local
support backend, its regression suite, delivery configuration and verification
scripts. Findings below were reproduced before being changed. This is a review
by the implementing agent; a second reviewer has not yet reviewed these changes.

## Findings and resolutions

### R1 — P2: early overload rejection reset streamed uploads

**Trigger:** one business request occupies the only admission slot; a second
caller streams a valid ticket containing 20,000 emoji, with either Content-Length
or chunked transfer. The previous `Connection: close` on the early 503 closed the
socket while the caller was still sending. A real HTTPX client received
`ReadError`, losing the readable rejection and Retry-After advice.

**Fix:** [HttpLimits](../src/northdesk/http_limits.py) leaves connection lifecycle
to Uvicorn for early overload responses. Requests still receive immediate 503
before business dispatch; the application does not buffer the rejected body.
Uvicorn's completed-response handling discards remaining incoming data.

**Evidence:** [two actual socket regressions](../tests/api/test_http_transport.py)
fail with `httpx.ReadError` on the old source and pass after the fix. They also
check health availability, no rejected backend dispatch and subsequent requests.
[Recorded baseline failures](extensive-artifacts/baseline-http-transport.json).
ASGITransport alone cannot verify this behavior.

### R2 — P2: provider exception chains leaked private details

**Trigger:** OpenAI transport failure, or either adapter receiving 429 with
Retry-After 301 seconds. The OpenAI connection path explicitly chained the SDK
exception, including its underlying transport exception. The over-budget backoff
helper raised inside an SDK error handler without suppressing that context.
A synthetic private marker appeared in formatted tracebacks; the explicit
OpenAI transport chain also entered Temporal's serialized failure.

**Fix:** [provider boundaries](../src/northdesk/agent/providers.py) suppress the
original transport/error context on these paths. Public error names, finite
retry budgets and permanent/transient classifications remain available.

**Evidence:** [eight SDK transport/activity cases](../tests/unit/test_provider_failure_privacy.py)
cover both adapters, connection/read timeout, excessive backoff and authentication
failure. Tests inspect the full traceback, JsonFormatter output and
`DataConverter.encode_failure` bytes, rather than only `str(error)`. Four cases
failed on the baseline; the four already-safe controls continued passing.
No claim is made that a real key had leaked; all markers and keys used were synthetic.

### R3 — P2: startup validation could print another provider's key

**Trigger:** select a real provider without its required key while the other
provider's key is present. Pydantic's model-validator error can render the raw
input dictionary. SecretStr protects the validated field representation; it does
not by itself protect the original input printed in a validation exception.
A short synthetic credential was reproduced in that startup error.

**Fix:** [Settings](../src/northdesk/settings.py) sets `hide_input_in_errors=True`.
Errors still identify the missing configuration. This protects formatted errors;
it does not erase raw input from memory or sanitize an explicit `errors()` dump.

**Evidence:** [both configuration profiles](../tests/unit/test_settings_logging.py)
fail the new credential check on the baseline and pass with this change.

### R4 — P2 verification gap: load probe could exit successfully after a GET 500

**Trigger:** POST succeeds, the first GET returns 500 and the next returns the
correct completed state. The previous probe correctly recorded the unexpected
error in its JSON, but `success` and the CLI exit code only required eventual
completion. That made a green exit insufficient evidence of an error-free run.
This finding concerns the verification tool, not a demonstrated backend 500.

**Fix:** [load probe](../scripts/load_check.py) requires eventual correct completion,
no unexpected HTTP/transport errors and at least one healthy probe. Explicit
503/Retry-After remains allowed and counted. `eventual_completion` remains a
separate measurement; raw HTTP success is also reported separately.

**Evidence:** [baseline counterexample](extensive-artifacts/baseline-load-gate.json)
and [five behavioral tests](../tests/unit/test_load_probe.py): normal read,
503 with advice, recovered 500, recovered disconnect and admission retry with
identical ticket content/ID. Unexpected errors now fail the gate even after recovery.

### R5 — P3: path IDs bypassed the creation contract

**Trigger:** GET/approval with an ID longer than 80 characters, Unicode, spaces
or control characters. Creation rejected these IDs, but the other routes passed
them to Temporal and ticket logging. The real dev server returned 404 for a
1,100-character ID; a simulated backend INVALID_ARGUMENT was classified as 503. Neither
result validated the API's ID contract before backend dispatch.

**Fix:** [HTTP routes](../src/northdesk/api.py) share the ASCII ID pattern and
apply an 80-character path limit. Invalid routed IDs return a bounded 422 without
creating a workflow handle. Existing valid IDs retain their behavior.

**Evidence:** [14 route cases](../tests/api/test_adversarial.py): invalid IDs on
both operations, plus accepted lengths 1 and 80. Route separators still follow
normal router behavior and may return 404.

## Coverage and cross-checks

| Area | What was inspected and cross-checked |
| --- | --- |
| HTTP | All endpoints, input normalization, validation errors, RPC mappings, Describe/Query/result paths, timeout/admission slot release, streaming and connection reuse |
| Approval | First response, conflicting concurrent updates, identical retries, deadline boundary and worker offline across expiry; handlers contain no await points |
| Replay | Both workflow patches, unchanged command order/action key, five frozen pre-fix histories and legacy completion recovery; fixtures were not regenerated |
| Financial policy | Ownership, paid invoice, positive finite EUR cents, precision/limit extremes, independent Fraction oracle and all packaged customer/invoice pairs |
| Agent/tools | Read-only allowlist, ticket-derived customer scope, round/call/byte budgets, malformed JSON, refusal/truncation and unknown customer before model calls |
| Providers | Native tool IDs and continuation data, SDK retries disabled, status classification, Retry-After precedence/date/bounds, exception serialization and client shutdown |
| Effects | Explicit connection lifetime, BEGIN IMMEDIATE, receipt/payload fingerprint, concurrent duplicate/conflicting callers, committed effect with lost ack, overlapping attempts and process death |
| State | New byte budgets, malformed Unicode/non-finite data, legacy projection purity/idempotence and preservation of refund/receipt identity |
| Delivery | Locked dependencies, non-root images, isolated fake test stack, loopback bindings, persisted volumes, offline test execution and finite shutdown/retry budgets |
| Test evidence | Assertions versus recorded measurements, real versus emulated Temporal limits, actual HTTP versus ASGI tests, raw 503/error counts and CLI gate semantics |

The workflow, financial policy and action-store implementation did not change in
this pass. Existing regression tests for those areas ran again in the full suite.
A suspected one-slot worker configuration problem was not promoted to a finding:
the installed SDK accepted construction, startup and shutdown on an empty queue.
Documentation alone was not treated as proof of a runtime failure.

## Verification and retained failures

The ordinary container gate passed **302 tests**, with three opt-in wall-clock
activity timeout tests skipped. Those three passed in the preceding fix pass;
they were not repeated here because workflow/activity timeout logic did not change.
Ruff lint/format and mypy pass. See [machine-readable check results](extensive-artifacts/checks.json).

New tests total 31 cases. To check that the important regressions were not vacuous,
they were also run against a temporary source checkout of `d39a825` using the
same installed dependencies: 16 of 20 privacy/configuration/invalid-path cases
failed, while four privacy controls passed. Both additional actual HTTP socket
cases failed with ReadError. [Baseline regression results](extensive-artifacts/baseline-regressions.json).

The first repeated high-load run after the streaming fix **failed**: 499/500
starts returned 202, one POST hit RemoteProtocolError, and reads of the missing
ID produced 404 until the deadline. Health remained available. This is preserved
in [the failed run](extensive-artifacts/load-before-idle-pool-margin.json); it is
not counted as successful verification.

The probe previously used HTTPX's 5-second idle pool expiry, matching Uvicorn's
5-second server keep-alive deadline. The client now expires idle connections at
2 seconds to leave a margin. A stale pooled connection is a plausible explanation
for the protocol error, not a proven root cause. Two subsequent runs passed: [round 1](extensive-artifacts/load-200-round-1.json)
and [round 2](extensive-artifacts/load-200-round-2.json). Each includes 500 starts
at concurrency 200, 1,000 duplicate submissions and 30 maximum-Unicode tickets,
with zero unexpected HTTP/transport errors. The 500-ticket phases settled in
25.669 s and 31.020 s. Both contain explicit POST/GET 503 shedding, and
`all_http_operations_succeeded=false`; neither is an all-2xx run. Health probes
were 99/99 and 130/130 successful. Other local builds/tests ran during these
measurements, so they are observations rather than comparable performance SLAs.
Independent action-row counts for these two new runs have not yet been audited. The stricter gate continues to
fail on any unexpected transport error; such errors are not silently retried or
reclassified as normal admission shedding.

[Request-byte probes](extensive-artifacts/requests.json) also reran against the
actual isolated service. The maximum legal Unicode ticket returned 202; all
three oversized forms returned readable 413 responses.

No new paid model requests were required for these changes. Current provider
error behavior was checked through the real SDK with mocked transport. The prior
seven-ticket live GLM result and 20 browser screenshots remain historical evidence;
this review does not relabel them as new runs. Current runtime checks are recorded
separately in [runtime.json](extensive-artifacts/runtime.json).

## Primary-source cross-check

- [Python exception context](https://docs.python.org/3/library/exceptions.html#exception-context): explicit causes remain visible; `from None` suppresses implicit context in displayed tracebacks.
- [Temporal failure converter](https://python.temporal.io/temporalio.converter.DefaultFailureConverter.html): exception conversion was checked against the locked SDK's installed source and actual encoded failures.
- [Pydantic error configuration](https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.hide_input_in_errors): suppressing raw input in rendered validation errors is separate from SecretStr representation.
- [Uvicorn response completion](https://uvicorn.dev/server-behavior/): an explicit close header closes the connection; after a completed response Uvicorn discards remaining request data without unbounded buffering.
- [FastAPI path validation](https://fastapi.tiangolo.com/tutorial/path-params-numeric-validations/): path constraints belong at the HTTP boundary and appear in generated OpenAPI.

## Remaining scope and next reviewer

No additional P0/P1 defect was confirmed within the local demonstration contract.
This does not certify production readiness or exhaustive absence of bugs.
Before real customer/payment use, the documented boundaries still require work:
authenticated customer/operator identities, cumulative refund control across
separate ticket IDs, a real Helpdesk idempotency/reconciliation contract, shared
persistence for multiple hosts, production Temporal and operational monitoring.
The absence of those features is not hidden by this review's passing tests.

The current suite runs locally on ARM64. AMD64 CI and live OpenAI have not been
rerun/validated here. A dependency advisory scan and a representative model-quality
benchmark were not performed. HTTP transport behavior also depends on the server,
proxy and client; finite load samples cannot establish a universal transport guarantee.

For the next reviewer:

```bash
git diff d39a825..HEAD -- src tests scripts
make test
uv run --locked pytest tests/api/test_http_transport.py tests/unit/test_provider_failure_privacy.py tests/unit/test_settings_logging.py tests/unit/test_load_probe.py tests/api/test_adversarial.py -q
```

Inspect the retained failing load run as well as passing runs. Do not remove the
workflow patches or regenerate the frozen histories to make a regression pass.
The [startup guide](../README.pl.md), [architecture](architecture.md) and
[previous fix report](fixes.md) provide the rest of the handoff.
