> Historical pre-fix baseline. See [fixes and current verification](fixes.md) for the 2026-10-24 resolution.

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

## Expanded review

The initial HTTP pass above was followed by a broader review of ordinary failures,
concurrency, persistence, provider protocols, and demo behavior. See the
[coverage matrix](review-matrix.md) and [browser evidence](review-artifacts/e2e/README.md).
Application code remains unchanged; the following are additional confirmed findings.

### F3 — P2: malformed model output can permanently break ticket reads

`TicketWorkflow.run` retains the raw proposal before independent validation.
`validate_decision` safely escalates an unknown shape, but the retained proposal
may still contain a lone surrogate. Temporal's converter and the SQLite fake can
persist that value. The workflow completes, while every subsequent HTTP status
read fails with 500 when FastAPI tries to serialize the stored state.

The same character in a reply message passes the current string/nonempty checks,
so an invalid Unicode reply is also accepted by the fake Helpdesk. Rejecting only
HTTP request input does not address either path.

`tests/api/test_model_output.py` exercises the real GLM adapter and OpenAI SDK via
a mocked HTTP transport, then real activities, validation, Temporal, SQLite and
the HTTP status endpoint. Two cases reproduce this defect. A nonfinite numeric
proposal is a passing control: it safely escalates and remains readable in this
response serialization path. No live provider behavior or frequency is inferred.

Repair needs both model output validation and a safe representation of raw
proposals for status/audit reads, including histories already containing such data.

### F4 — P3: SQLite connections rely on garbage collection for closure

`FakeHelpdesk.__init__` and `execute` use `with self._connect() as db` without
calling `close`. Python's connection context manager handles transactions but does
not close the connection, as the [Python 3.12 documentation](https://docs.python.org/3.12/library/sqlite3.html#how-to-use-the-connection-context-manager)
explains. A tracked real connection confirms the omission after initialization,
successful writes, duplicate reads, conflicts and temporary failures.

Transactions still commit/roll back correctly. This is a resource-lifetime defect;
no permanent leak, descriptor exhaustion or service outage was demonstrated.
Use explicit connection closure while preserving the transaction boundary.

### F5 — P3: actual timeouts produce numeric failure labels

The workflow reads `getattr(exc.cause, "type", ...)` to classify activity errors.
For an SDK `TimeoutError`, `type` is a `TimeoutType` enum rather than an application
error name. It becomes `1` in the public failure reason.

Actual wall-clock tests, with automatic time skipping disabled, observed:

| Stalled activity | Attempts | Observed duration | Correct business outcome | Diagnostic defect |
| --- | --- | --- | --- | --- |
| Decide | 3 | 273.46 s | Completed escalation | `decision_failed:1` |
| Validate | 3 | 33.44 s | Completed escalation | `decision_failed:1` |
| Act | 5 | 90.45 s | Failed, no result | `action_failed:1` |

All three tests failed only their final assertion expecting a readable timeout
name; their attempt counts and terminal behavior assertions passed. These timings
come from the pinned test server, not a production latency guarantee. A faster
unit reproduction in `tests/unit/test_timeout_reporting.py` isolates the label
defect without waiting for deadlines.

The three real-time tests are opt-in and intentionally still expose the failure:

```bash
RUN_SLOW_TIMEOUT_TESTS=1 uv run --locked pytest tests/slow/test_activity_timeouts.py -q
```

They are skipped by default. SDK timeout subclasses should be classified
explicitly, retaining a readable timeout name/type.

## Limits follow-up

The [limits review](limits-review.md) documents three additional findings:
F6 (oversized aggregate completion state after an effect is committed), F7
(JSON parser resource exceptions bypassing permanent tool-error classification),
and F8 (provider Retry-After lost before Temporal retry scheduling). It also
records query timeouts under higher load, raw request byte behavior, and the
passing overlapping-refund-attempt test. No application fixes were applied.
