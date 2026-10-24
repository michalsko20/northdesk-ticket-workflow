# Handoff

Original baseline verified on 2026-10-23; fixes verified on 2026-10-24. This document is for continuing development or preparing the technical walkthrough.

## Project and current state

- Repository: <https://github.com/michalsko20/northdesk-ticket-workflow>
- Local checkout: `/Users/michalskoneczny/Projects/elevenlabs/northdesk-ticket-workflow`
- Current branch: `test/adversarial-inputs`. Fixes and evidence are committed locally; not pushed. Core fix `2b21fa5`, repeatable verification scripts `aeddeb6`. The main-branch CI below is historical.
- Latest verified implementation/documentation commit before this handoff: `563565c`.
- CI for that commit: [successful run](https://github.com/michalsko20/northdesk-ticket-workflow/actions/runs/39973655003).
- Historical main-branch suite: **86 tests**, Ruff lint, Ruff format check and mypy. Tests ran in a container with external networking disabled; CI also passed on AMD64.
- All seven example tickets were exercised through the running HTTP API with fake mode and the live GLM profile. Worker restart, Temporal restart, duplicate requests and persistent action receipts were checked.
- At handoff, the API, Temporal and worker containers are running locally. The worker uses `LLM_MODE=glm`, model `glm-5.3-flash`. A fresh checkout defaults to keyless fake mode.

No implementation work is currently blocked or required to complete the agreed backend scope. Production deployment and sending the repository to a recruiter have not been performed.

## Current fix verification (2026-10-24)

- Eight confirmed review findings fixed; see [fix report](docs/fixes.md).
- Current container suite: 271 passed, 3 opt-in slow tests skipped. All three slow tests were also run separately and passed. Ruff/format/mypy pass.
- Five frozen histories replay; original oversized completion recovers on a real Temporal server with one effect and the original receipt.
- HTTP body 256 KiB/10 s; admission 32 requests/process; active Query 4 slots/1 s; worker 10 activity slots, 16 workflow slots, cache 512. All settings are in `.env.example` and README.
- Keep workflow patches `readable-activity-errors-v1` and `bounded-ticket-state-v1` while old histories remain retained.
- 500 unique tickets/concurrency 200 completed; controlled 503 shedding is recorded explicitly. 531 unique effects across all load phases; zero unexpected HTTP errors.
- New Playwright evidence: 20 screenshots, worker/full-stack restart and lost-ack recovery, no JavaScript page errors.
- [Polish startup guide](README.pl.md), [architecture/research](docs/architecture.md), [SVG diagram](docs/architecture.svg).
- `make test` is the offline container gate. Load test scripts write ignored `artifacts/` by default; committed baseline reports must not be overwritten.
- Main API/worker upgraded with the existing GLM profile preserved; seven new live examples and their receipts verified. Health endpoints return 200.
- Extra `northdesk-limits` stack uses ports 8002/7235/8235 and separate volumes; is now stopped with volumes preserved. Main stack remains running on 8000/7233/8233.

## Subsequent extensive review (2026-10-24)

- Review baseline `d39a825`; see [detailed findings and evidence](docs/extensive-review.md).
- Provider exception context and startup-validation inputs now avoid leaking synthetic private markers into formatted errors; provider failure serialization is explicitly tested.
- Early overload rejection leaves connection lifecycle to Uvicorn, avoiding TCP reset during legal streamed uploads. Two actual socket tests complement ASGI tests.
- Read/approval path IDs now share the creation ID contract, rejecting invalid IDs before backend dispatch.
- Load success/CLI exit now require zero unexpected errors; eventual completion is recorded separately. A failed run is retained. The probe expires idle client sockets at 2 s, below the server default of 5 s.
- Container suite: 302 passed, 3 opt-in timeout tests skipped. Current changes do not alter workflow command order, policy, action keys, persistence schema or frozen histories.

## Resume here — extensive review handoff

- Core review fixes committed: `e7df875`. Strict load gate/client idle-pool margin: `7a62566`. Documentation/evidence is saved in the next handoff commit. No push was performed.
- Full final container result: **302 passed, 3 skipped in 46.51 s**; Ruff lint/format and mypy passed. See `docs/extensive-artifacts/checks.json`.
- Five review findings addressed: streamed-overload TCP reset, provider exception-chain details, startup validation exposing raw credential input, load-test false-green exit, and missing path-ID validation.
- Regression proof against `d39a825`: 16 failures and 4 passing controls in privacy/config/path tests; two actual Uvicorn socket tests additionally fail with ReadError on the baseline and pass with current code.
- A high-load run failed with one POST RemoteProtocolError; **preserve** `docs/extensive-artifacts/load-before-idle-pool-margin.json`. Idle-connection expiry race is plausible, not proven. HTTPX probe pool expiry was changed from 5 s to 2 s, below the server's 5 s keep-alive.
- Two subsequent 500-ticket/concurrency-200 rounds passed with zero unexpected HTTP/transport errors; both also exercised 1,000 duplicate submissions and 30 maximum-Unicode tickets. Explicit 503 shedding remains recorded and expected. New action-row counts have not been independently audited.
- Main API/worker runs the final code with the original GLM profile preserved. All health endpoints return 200; seven retained GLM examples still return the original receipts. No new paid model calls were made during this review. See `runtime.json`.
- Additional fake limits stack is stopped with volumes preserved. Main stack remains running. Credentials stay outside Git; never dump complete container environments.

### Next reviewer / unfinished verification

1. Read [extensive review](docs/extensive-review.md), including the failed run, scope boundaries and source cross-checks.
2. Review `git diff d39a825..HEAD -- src tests scripts`; run `make test` if making changes.
3. Optionally audit SQLite/history effects for the two new load reports. Prior 531-effect audit remains evidence only for the preceding run.
4. Investigate the isolated protocol error further if claiming its root cause. Two passing samples after changing pool expiry do not prove causation.
5. AMD64 CI, live OpenAI and a second independent review have not been run for these changes. Three slow timeout tests were not repeated because their code did not change.
6. The previous 20 browser screenshots and live GLM run are historical; do not describe them as rerun during this pass.

## Working agreement

- Keep changes small and make a separate descriptive commit after each completed stage. Preserve the readable history.
- Run relevant tests and the existing lint/type checks for code changes. Use the container suite before delivery.
- Keep credentials and personal profile files outside Git and image builds.
- Keep the original assignment document outside the repository. The packaged example data is intentional.
- Prefer the existing small architecture; optional infrastructure, UI or additional frameworks are not required.

## Entry points and commands

- Swagger: <http://localhost:8000/docs>
- Temporal UI: <http://localhost:8233>
- Health: `GET /health/live` and `GET /health/ready`.
- Full operating instructions, API examples, architecture and Azure proposal: [README](README.md).
- Recorded live-model comparison: [GLM evaluation](docs/glm-evaluation.md).

```bash
cd /Users/michalskoneczny/Projects/elevenlabs/northdesk-ticket-workflow

# Complete lint / format / type / test suite.
# Build needs network; test execution has network_mode: none.
make test

# Local checks with uv and Python 3.12.
make check

# Explicitly run keyless fake mode.
LLM_MODE=fake docker compose up --build -d --wait

# Run GLM when the existing profile variables are exported in this shell.
LLM_MODE=glm docker compose up --build -d --wait

# Exercise examples, explicitly acting as approver for simulated refunds.
python3 scripts/demo.py --suffix=-handoff-demo --approve-refunds

docker compose logs -f api worker
docker compose down
```

Use a new suffix for each fresh demonstration. Existing IDs return their original workflows and decisions. Named volumes preserve history and fake effects across normal shutdowns; do not remove them unless intentionally resetting the demonstration.

## GLM configuration

The existing profile supplies `ZAI_API_KEY`, `GLM_MODEL` and `GLM_BASE_URL`. The observed model is `glm-5.3-flash`. The adapter accepts either a base URL or a full Chat Completions URL; Compose passes exported profile values through at runtime. A new shell must load the profile before selecting GLM mode. No secret has been copied into a repository file.

The service does not read Codex configuration files. The repository's default endpoint is the standard Z.ai API; the development profile can override it. Default fake mode and all automated tests require no key.

GLM requires its own Chat Completions adapter. Preserve native tool-call IDs and `reasoning_content` in continuation messages, without logging reasoning. Final proposals use `submit_decision`; this is a data-returning tool, not an action or approval tool. A live test showed that forcing JSON-object mode alongside tools could return planning/logging objects instead of real calls. That approach was replaced and is documented in the evaluation.

## Code map

| Area | File or directory | Responsibility |
| --- | --- | --- |
| HTTP | `src/northdesk/api.py` | Start, Query, approval Update, request validation and HTTP error mapping |
| Workflow | `src/northdesk/workflows/ticket.py` | Replay-safe state, 24-hour approval timer, retry policies and terminal outcomes |
| Activities | `src/northdesk/activities.py` | I/O boundaries and retryable/non-retryable service errors |
| Policy | `src/northdesk/domain/` | Framework-independent models and decision validation |
| Agent | `src/northdesk/agent/loop.py`, `tools.py` | Shared bounded loop, tool allowlist and customer scope |
| Providers | `src/northdesk/agent/providers.py`, `glm.py` | Fake, OpenAI Responses and GLM Chat Completions adapters |
| Helpdesk | `src/northdesk/services/helpdesk.py` | Durable fake effects, transactional receipts and injected failures |
| Catalog | `src/northdesk/services/catalog.py`, `data/` | Trusted customer, invoice and KB facts |
| Runtime | `settings.py`, `connection.py`, `logging.py`, `worker.py` | Environment settings, startup, JSON logs and worker lifecycle |
| Tests | `tests/unit/`, `tests/workflow/`, `tests/api/` | Policy, protocols, effects, Temporal time skipping and HTTP integration |
| Containers / CI | `Dockerfile`, `docker-compose.yml`, `.github/workflows/check.yml` | Native ARM64/AMD64 test server, offline checks and GitHub Actions |

## Invariants to preserve

1. The model proposes; trusted code decides whether the action is permitted. Tool output and prompts do not replace invoice ownership, amount or approval checks.
2. Customer-scoped tools derive the customer from the ticket, never from model-supplied customer arguments. The model has no tool that performs a refund.
3. Refunds use positive finite decimal EUR amounts with cent precision, within a paid invoice belonging to that customer.
4. A refund waits for a human response for at most 24 hours. The first response wins. Premature/conflicting/expired approvals are rejected; rejection and timeout escalate.
5. Workflow ID `ticket/{ticket_id}` and `REJECT_DUPLICATE` deduplicate starts, including retained completed executions. Duplicate content never updates the first ticket.
6. Action key `ticket/{ticket_id}/action` remains stable across retries. The fake effect and its receipt commit atomically in SQLite. Lost acknowledgement must not create a second refund.
7. Retry budgets are finite. Permanent lookup/configuration errors do not retry indefinitely. Exhausted Helpdesk delivery returns `step=failed`; it must not claim a successful escalation.
8. Network/file access and random failures stay in activities. Workflow code stays deterministic; production workflow changes require compatibility with stored histories.

## Known pitfalls and boundaries

- An Update on a closed workflow can return Temporal `NOT_FOUND`. The approval endpoint uses Describe on that error to distinguish closed (`409`) from nonexistent (`404`). Do not simplify this to unconditional HTTP 404.
- Completed workflows read their persisted result without a worker. Active Query needs a polling worker and returns 503/Retry-After after 1 s or when its four slots are full. `/health/ready` checks Temporal; `/health/worker` checks recent pollers (120 s).
- The replacement-worker test disables workflow caching to force replay and avoid sticky-routing behavior in the limited test server.
- Temporal's dev image runs as user `temporal`: persistence is mounted under `/home/temporal`, not a root-owned directory. Test-tool caches use `/tmp` because the test container also runs without root.
- The pinned Temporal test server is downloaded and checksum-verified during image build. Do not restore SDK auto-downloads at test runtime.
- Workflow start deduplication is bounded by Temporal retention. A permanent ticket registry/read projection would be needed for longer guarantees.
- SQLite and the fake action row are sufficient for this local fake. Multi-host workers need shared persistence; a real Helpdesk needs native idempotency or reconciliation.
- There is no production IAM, cumulative refund accounting across distinct tickets, live OpenAI-key validation, deployed Azure infrastructure or distributed tracing. The README explains these limits and next steps.
- Live GLM and fake choose the same final actions on the recorded examples, but T-4 categorization and T-6 priority differ. T-7 is rejected by the trusted lookup before any model call.

## Suggested walkthrough

Start with T-2: show its model proposal, independent validation, pending approval, restart recovery, approval and a single persisted effect. Then show the T-6 test where a malicious provider proposes EUR 5000 and code escalates it. Explain time-skipping tests and the committed-refund/lost-acknowledgement test. Finish with the recorded GLM integration correction, the Azure proposal and production limitations.
