# Fix verification — 2026-10-24

All eight confirmed findings from the adversarial review have regression tests
and fixes. The earlier review documents are historical baselines.

| Finding | Resolution | Regression evidence |
| --- | --- | --- |
| F1: invalid Unicode/non-finite validation input caused 500 | Bounded error projection excludes rejected input/context | API adversarial and HTTP-limit tests |
| F2: nested error input caused recursion failure | Validation errors serialize safe fields only | Deep JSON tests, including depth 20,000 |
| F3: malformed model output polluted durable state | Strict UTF-8/JSON and byte budgets before persistence; safe historical projection | Model-output and state-projection tests |
| F4: SQLite connections stayed open | Explicit connection closure around atomic transaction | Helpdesk lifecycle and effect tests |
| F5: unreadable timeout labels | Named SDK timeout kinds; replay-compatible patch | Timeout unit tests and real activity timeout tests |
| F6: oversized final state failed after effect committed | Bound new proposals before actions; bounded legacy completion preserves receipt | Real Temporal recovery and frozen-history replay |
| F7: tool JSON parser exceptions escaped classification | Size checks and permanent classification of parser failures | Tool boundary tests |
| F8: provider Retry-After was ignored | Carry bounded delay into Temporal retry scheduling | Header cases and both adapters' workflow tests |

Additional operational fixes: HTTP byte/body-time limits, bounded admission and
active queries, explicit worker slots/cache, worker-poller diagnostics, and
completed-result reads without a polling worker. Clients can retry explicit 503
responses with Retry-After and the same ticket ID. A 413 response is tested with
real oversized uploads, including chunked transport; it must remain readable to
the caller rather than resetting the connection mid-upload.

## Verification

- Container suite: **271 passed, 3 skipped**, 44.42 seconds. The three opt-in real
  timeout tests were also run separately: validation 33.43 s, action 90.44 s,
  decision 273.42 s, all passed. These timings are observations, not SLAs.
- Ruff lint/format and mypy pass. All previously expected-failing regressions now
  pass normally. Five frozen pre-fix histories replay successfully.
- [Real legacy recovery](fix-artifacts/legacy-recovery.json): the original frozen
  worker hit the server's actual 2 MiB limit after committing an effect. The new
  worker completed with a **662-byte** result and the original receipt: one
  invocation, one effect, no second delivery.
- [Payload probes](fix-artifacts/payloads.json): 16,384-character ASCII message
  succeeds; 16,385, 800,000 and 2,200,000 characters safely escalate. Every case
  completes with one effect and no failed workflow task.
- [HTTP probes](fix-artifacts/requests.json): maximum legal Unicode input returns
  202; three oversized upload forms return 413 with a 48-byte response.
- [Load test](fix-artifacts/load-200-final.json): 500 unique tickets at concurrency
  200 all complete in 33.85 s; logical admission p95 is 6,542 ms including retries.
  **726 POST 503 and 2,169 GET 503 are intentional load shedding**, not successful
  HTTP operations. There are zero unexpected HTTP/transport errors. Health stays
  200 for 153/153 probes, p95 26.07 ms. The full run also covers 1,000 duplicate
  submissions and 30 maximum Unicode tickets. An independent SQLite audit finds
  exactly one action for each of the 531 unique tickets.
- [Browser evidence](fix-artifacts/e2e/README.md): 20 Playwright screenshots cover
  Swagger controls, seven examples, approval/rejection, duplicate requests,
  404/409/422, lost acknowledgement, worker/full-stack restarts, exhausted delivery
  retries and Temporal UI. No JavaScript page errors. Separate history/SQLite
  verification is in [independent-audit.json](fix-artifacts/independent-audit.json).

- [Live GLM smoke](fix-artifacts/live-glm.json): upgraded main API/worker while
  preserving the existing GLM profile. All seven examples completed with expected
  actions and one persisted receipt each; live/ready/worker health returned 200.

The load test uses one fake-mode worker on the local Docker host. It does not
measure paid provider capacity or establish a production SLA. Current checks ran
on the local ARM64 container; the previous AMD64 CI result is historical and has
not been rerun for these changes.

## Design review and use

The ownership boundaries fit this application: durable orchestration in Temporal,
I/O in activities, policy in ordinary code, and atomic fake effects with stable
idempotency keys. This is an assessment against the linked official guidance,
not a claim that the local demo is production infrastructure.

See [architecture and primary sources](architecture.md),
[flowchart SVG](architecture.svg), [editable Mermaid](architecture.mmd), and
[Polish startup guide](../README.pl.md). Production boundaries remain documented
there: identity/authentication, shared storage, real payment reconciliation and
cross-ticket refund accounting require separate implementation.
