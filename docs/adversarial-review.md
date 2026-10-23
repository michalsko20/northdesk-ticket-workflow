# Adversarial review, 2026-10-23

Target: handoff commit `cfb50f7`. This stage adds reproductions and boundary tests;
application code is unchanged. No live model requests or successful ticket submissions
were needed for the live probes. Existing Temporal and Helpdesk volumes were preserved.

## Findings

### F1 — P2: rejected input can break the validation error response

`create_app` in `src/northdesk/api.py` uses FastAPI's default request validation error
handler. That handler includes rejected values in the response. Some values accepted
by Python's JSON decoder cannot be serialized by the response encoder:

- An escaped lone surrogate (`"\ud800"`) causes `UnicodeEncodeError`. Confirmed in
  ticket IDs, subjects, bodies and unknown fields. Pydantic rejects the value, but
  encoding the rejection fails, producing HTTP **500** instead of **422**.
- `NaN`, `Infinity`, or `1e400` in a field requiring a string or boolean cause
  `ValueError: Out of range float values are not JSON compliant`. `NaN` and
  `Infinity` are nonstandard JSON; `1e400` is valid JSON number syntax that Python
  decodes to infinity. The server should reject them without a server error.

The tests cover both ticket submission and approval. No workflow start or approval
handle lookup occurs in these rejected requests.

### F2 — P2: encoding rejected nested input exhausts recursion

An unknown field containing 1,100 nested arrays produces HTTP **500**. The request
is approximately 2.2 KB. The observed traceback ends in repeated recursive calls to
FastAPI's `jsonable_encoder` while it encodes the rejected input in a validation
error. This is not evidence of a parser crash or permanent service outage.

All three payload families were also confirmed against the running Docker API on
`localhost:8000` (numeric probes used ticket submission). `/health/live` still
returned **200** afterward. One reused HTTP connection was disconnected after an
error; subsequent probes used separate connections to isolate response behavior.

## Reproduce

These tests use an in-process HTTP client and a mocked Temporal client. They need no
Temporal server, credentials, or external network:

```bash
uv run --locked pytest tests/api/test_adversarial.py --runxfail -q
```

Expected on this commit: **8 failed, 13 passed**. Each failing test expects a client
error and receives HTTP 500. Normal test runs mark those eight cases as strict
expected failures; a future fix will produce XPASS and require removing the marks.

The 13 passing cases cover empty/oversized bodies, invalid IDs, extra fields,
customer ID type confusion, legitimate Unicode, and approval boolean coercion.
The original suite additionally exercises refund ownership, amount limits, human
approval, retries, and durable effects. No bypass of those rules was demonstrated.

## Suggested repair

Add a narrow request validation exception handler that returns bounded, JSON-safe
error details without echoing arbitrary rejected `input` or exception context.
Preserve useful field locations and error types. Verify all three payload families
and legitimate Unicode, then remove the corresponding strict xfail marks. Avoid a
generic catch-all that would hide unrelated application failures.

Readiness without an available worker, absent production IAM, cumulative refunds
across distinct tickets, and retention-bounded deduplication were already disclosed
in the handoff and are not counted as new findings.

## Validation

- Original offline container suite: **86 passed**, Ruff and mypy passed.
- New adversarial file: **13 passed, 8 xfailed**; with `--runxfail`, **8 failed,
  13 passed**, all failures from HTTP 500 responses.
- Final offline container suite: **99 passed, 8 xfailed**; Ruff lint, formatting,
  and mypy passed. The eight expected failures remain unfixed and are intentional.
