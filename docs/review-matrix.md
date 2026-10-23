# Cross-check matrix, 2026-10-23

The core refund, approval and idempotency rules held in the exercised cases. Five
findings remain open: F1/F2 HTTP validation error handling, F3 unreadable persisted
model proposals, F4 SQLite connection closure, and F5 timeout diagnostics. Details
and repair suggestions are in the [review report](adversarial-review.md).

The later [limits review](limits-review.md) adds F6 (aggregate workflow payloads),
F7 (JSON decoder resource errors), F8 (Retry-After propagation), and bounded load
measurements up to 200 concurrent submissions. Its evidence includes 1,693 unique
tickets cross-checked against persisted effects.

This matrix cross-checks the implemented backend and its documented contracts.
It records evidence rather than claiming exhaustive proof over all inputs,
deployments or future model outputs. Production features already excluded in the
handoff remain excluded.

## Research used to choose tests

Temporal recommends emphasizing integration tests and replaying representative
recorded histories when changing workflows. Its time-skipping environment also
does not automatically skip while activities run. This guided the crash/restart
tests, five frozen histories, and separate wall-clock timeout tests.
[Temporal testing guide](https://docs.temporal.io/develop/python/best-practices/testing-suite).

Update validators reject requests before acceptance; handlers must preserve the
workflow's consistency. That prompted conflicting concurrent approvals, exact
deadline boundaries and recovery across an offline interval.
[Temporal message passing](https://docs.temporal.io/develop/python/workflows/message-passing).

FastAPI supports overriding request validation exception handlers. Pydantic strict
validation avoids coercing approval values. These informed malformed request,
serialization and boolean boundary checks.
[FastAPI error handling](https://fastapi.tiangolo.com/tutorial/handling-errors/),
[Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/).

Python documents that SQLite transaction context management does not close a
connection. This led to testing resource lifetime in addition to committed effects.
[Python SQLite connection context manager](https://docs.python.org/3.12/library/sqlite3.html#how-to-use-the-connection-context-manager).

## HTTP and API contract

| Case | Evidence | Result |
| --- | --- | --- |
| Submit/read, first content wins, duplicate after completion | Existing API integration tests | Pass |
| 24 concurrent starts with different customer/content/action candidates | `test_concurrent_different_payloads_preserve_the_accepted_ticket` | One winner; its content determines result |
| Unknown ticket and closed-workflow approval | Temporal integration + backend error tests + browser | 404 / 409 as documented |
| Empty body, oversized body, illegal/oversized IDs, extra fields, wrong JSON types | `test_adversarial.py` | Rejected before Temporal mutation |
| Exact 80/80/500/20000 character boundaries | `test_exact_maximum_ticket_lengths_are_accepted` | Accepted |
| Unicode text including Polish and emoji | `test_legitimate_unicode_is_accepted` | Accepted |
| Approval booleans: strings, numbers, null, objects, arrays | `test_approval_coercion_is_rejected` | Rejected |
| Reviewer blank, length 100 and 101 | `test_reviewer_length_boundaries` | Correct boundary |
| Lone surrogate / nonfinite number in rejected input | Eight strict xfail cases | F1 |
| Nested rejected JSON, approximately 2.2 KB | Strict xfail reproduction | F2 |
| Query, approval and readiness transport errors | Nine backend error combinations | 503 / 504 without exposing transport detail |
| Failed query, failed update, ambiguous update timeout | `test_backend_errors.py` | 503 / 409 / 504 |
| Readiness false while liveness remains available | `test_readiness_false_does_not_change_liveness` | 503 / 200 |
| Start without worker | Existing real Temporal integration | Accepted and retained |

## Agent and provider contracts

| Case | Evidence | Result |
| --- | --- | --- |
| Fake provider really uses customer/invoice/KB results | Existing unit tests with changed catalog data | Pass |
| Unknown customer, no model invocation | Real activity tested with `ActivityEnvironment` | Permanent error, zero model calls |
| Unknown tools and customer override arguments | Existing/new tool tests | Rejected in code |
| Search query wrong type, empty, 1000 and 1001 characters | `test_agent.py` | Correct boundaries |
| Tool call budget 12 / 13 in one batch | `test_tool_batch_limit_accepts_boundary_and_rejects_one_over` | Boundary accepted; overrun rejected |
| Decision in round 6 / needed round 7 | `test_final_decision_must_fit_within_six_rounds` | Boundary accepted; overrun rejected |
| Model returns a non-object decision | Existing loop test | Bounded malformed-response error |
| OpenAI native tool IDs and continuation | SDK transport test | Pass |
| GLM native IDs, reasoning continuation, final tool, mixed batches | SDK transport tests | Pass |
| HTTP 408/429/5xx and configuration/auth errors | Provider transport tests | Retryable vs permanent; one SDK request |
| Connection failure and read timeout, both providers | Four new SDK transport tests | Retryable; no hidden SDK retries |
| Incomplete/failed/in-progress OpenAI result | Three transport cases | Not accepted as a decision |
| Empty/truncated/malformed GLM result | Existing/new transport cases | Bounded malformed-response errors |
| Rejected or accepted model text containing a lone surrogate | Real GLM adapter → Temporal → SQLite → HTTP | F3 |
| Real activity wrappers preserve permanent/transient classification | `test_activity_errors.py` | Pass |

## Business rules

| Case | Evidence | Result |
| --- | --- | --- |
| Every cent near EUR 49 limit, plus sub-cent values and negatives | 4,949 cases against an independent `Fraction` oracle in one test | Matches policy |
| Extreme exponents, nonfinite decimals, long amount strings, wrong types | `test_policy_boundaries.py` | Safe escalation |
| Wrong category/priority/action types and values | 21 new type cases + existing enum tests | Safe escalation |
| All packaged customer/invoice ownership and paid-status combinations | Catalog cross-product test | No invalid refund accepted |
| Unknown/unpaid/foreign invoice | Existing unit and workflow tests | Safe escalation |
| Unknown KB reference, malformed article list, empty reply/reason | Existing rule tests | Safe escalation |
| Model proposes EUR 5000 | Unit, workflow integration and browser | Blocked before approval/effect |
| Accepted reply message with invalid Unicode | End-to-end model-output reproduction | F3; fake effect accepts invalid text |

## Workflow durability and effects

| Case | Evidence | Result |
| --- | --- | --- |
| Normal reply, refund approval, rejection and expiry | Existing workflow tests + browser where practical | Correct action |
| Approval before decision or for a non-refund | Existing real Update tests | Rejected |
| 32 simultaneous conflicting approvals | `test_simultaneous_conflicting_approvals_record_one_winner` | Exactly one winner and action |
| Identical repeat vs conflicting repeat | Existing real Update test | Repeat harmless; conflict rejected |
| One microsecond before, exactly at, and after deadline | `test_approval_deadline.py` | First response accepted only before deadline |
| Worker offline across 24-hour deadline | New restart integration | Escalates; no refund or approval |
| Pending approval survives worker replacement | Existing replay-based test + browser restart | Pass |
| Decide, validate and action transient/permanent failures | Existing/new retry integration + real activity wrappers | Bounded retries and correct fallback |
| Stalled decide/validate/action with real production timeouts | Three opt-in wall-clock experiments | Correct terminal behavior; F5 labels |
| Helpdesk unavailable through all retries | Workflow test + browser + live history cross-check | `failed`, no receipt/result |
| Concurrent first writes, no receipt pre-created | 32 threads against real SQLite | One effect |
| Duplicate calls after restart and receipt already present | Existing SQLite tests | Original receipt returned |
| Conflicting payload for same action key | Existing SQLite test + real activity wrapper | Permanent error |
| Failure before commit | Existing SQLite test | No effect |
| Lost acknowledgement after commit | Existing activity retry integration | One effect |
| Abrupt exit of separate process after commit | New subprocess SQLite test | Receipt survives |
| Whole worker process dies immediately after refund commit | New subprocess worker integration, replacement uses failure rate 1 | Completed original refund, exactly one row |
| Connections close on init/success/duplicate/failure | Tracked real SQLite connections | F4 |
| Compatibility with recorded reply/approved/rejected/expired/failed histories | Five frozen-history replay tests | Pass |

## Browser E2E and independent cross-check

Playwright operates Swagger controls rather than bypassing them with direct API
calls. Its UI flows cover the seven example behaviors, rejection, changed-content
duplicates, late conflicting approval, 404, 422, worker restart, lost approval
acknowledgement, and Helpdesk retry exhaustion. Temporal UI shows the recovered
refund. Saved screenshots and machine-readable results are linked in the
[browser evidence index](review-artifacts/e2e/README.md).

Eleven resulting histories were independently read using the Temporal SDK. Each
applicable ticket contains one completed approval update; the delivery failure's
last activity attempt is 5. SQLite was independently read in read-only mode: each
of the ten successful tickets has exactly one expected effect, and the failed
ticket has none. These checks complement browser assertions.

The completed refund also remained readable with its original result after
restarting the isolated Temporal, API and worker containers together.

## Verification results

- Original handoff: 86 passing tests.
- Before the limits follow-up: **204 passed, 13 xfailed, 3 skipped**; Ruff
  lint, Ruff formatting and mypy passed. This is 220 collected pytest cases.
- The 13 strict expected failures reproduce F1/F2 (8), F3 (2), F4 (1) and F5 (2).
  They remain defects, not passing assertions.
- The 3 skipped cases are the opt-in wall-clock tests. They were actually run
  separately: each reached its expected safe terminal behavior and failed only
  the final diagnostic-name assertion, exposing F5. A sequential rerun takes
  approximately seven minutes.
- Playwright exercised the application through Swagger and Temporal UI with
  **20 saved PNG screenshots**, plus JSON results and independent persistence
  cross-checks. Browser runtime exceptions were absent; expected HTTP error and
  lost-response console entries are retained.
- Application files under `src/` and the original live GLM stack were unchanged.
- After the limits follow-up: **210 passed, 18 xfailed, 3 skipped** in the offline
  container, with lint/format/mypy passing. The five added expected failures cover
  F6 (1), F7 (2), F8 (2). The timeout-overlap and whole-ticket budget tests pass.

## Boundaries and remaining checks

- Browser E2E uses the isolated fake profile, not live GLM/OpenAI or real billing.
  GLM protocol defects are tested with the real SDK and mocked HTTP; the earlier
  handoff's live GLM smoke run is separate evidence.
- No Azure deployment, production IAM, cumulative accounting across different
  ticket IDs, multi-host SQLite, or retention-expired registry is implemented.
- The limits follow-up adds bounded load probes. There is still no long-duration
  soak test, stress-to-exhaustion, disk-corruption/disk-full experiment, or
  exhaustive evaluation of model answer quality.
- Frozen replay histories cover the current workflow paths; future workflow
  versions and migrations still require an explicit compatibility decision.
- The pinned embedded Temporal server is useful for tests but does not establish
  exact production timing. Wall-clock activity tests deliberately avoid time
  skipping and retain the diagnostic failure as a visible reproduction.
- Readiness checks Temporal service connectivity, not pollers. That disclosed
  operational limitation remains unchanged.
