# Browser E2E evidence

Verified 2026-10-23 using Playwright through Swagger UI and Temporal UI. The
isolated `northdesk-e2e` Compose project uses fake mode, empty API keys and its own
ports/volumes. The original GLM stack on ports 8000/8233 was preserved.

The browser clicks Swagger's operation panels, fills request fields and clicks
Execute. Assertions use the resulting HTTP responses, not direct replacement
requests. The lost-acknowledgement case deliberately routes the approval to the
server and then aborts delivery of its response to the browser.

## Results and screenshots

| Scenario | Evidence |
| --- | --- |
| Readiness | [Swagger ready](00-swagger-ready.png) |
| Password reply | [T-1](t1-completed-reply.png) |
| Refund waits, no effect yet | [Awaiting approval](02-refund-awaiting-approval.png) |
| Approved refund EUR 49.00 | [T-2](t2-completed-refund.png) |
| Changed-content duplicate preserves first invoice | Assertion in [results.json](results.json), same workflow |
| Late conflicting approval gets 409 | [Conflict](03-closed-approval-conflict.png) |
| Enterprise outage escalates urgently | [T-3](t3-completed-escalate.png) |
| Suspended account reply | [T-4](t4-completed-reply.png) |
| Off-topic request closes | [T-5](t5-completed-close.png) |
| Excessive refund blocked by code | [T-6](t6-completed-escalate.png) |
| Missing customer escalates | [T-7](t7-completed-escalate.png) |
| Rejected refund escalates | [Rejection](08-rejected-refund-escalated.png) |
| Missing ticket gets 404 | [Not found](09-ticket-not-found.png) |
| Invalid body gets 422 | [Validation](10-input-validation.png) |
| Pending approval survives worker restart | [Before](11-before-worker-restart.png), [after](12-restart-recovered-refund.png) |
| Workflow and refund visible in Temporal | [List](13-temporal-workflows.png), [details](14-temporal-recovered-refund.png) |
| Lost approval acknowledgement preserves approval/refund | [Completed refund](15-approval-ack-lost-but-refund-completed.png) |
| Helpdesk fails through all five attempts | [Failed without result](16-helpdesk-retries-exhausted.png) |
| Completed refund survives full stack restart | [After restart](17-after-full-stack-restart.png) |

Screenshots expand the live response JSON and hide static schema examples for
readability. Request controls, HTTP status and actual response values are retained.

The tested browser flows reported no JavaScript page exceptions. The
[console log](browser-console.log) contains expected 404/409/422 responses and the
deliberately aborted approval response; those are test scenarios, not clean-console
claims.

[Temporal history cross-check](temporal-cross-check.json) independently verifies
eleven executions, one completed approval update where applicable, and the final
failed delivery's attempt number of five. [SQLite cross-check](sqlite-cross-check.json)
verifies ten successful tickets each have exactly one expected effect; the failed
ticket has no receipt. Full browser results are in [results.json](results.json).

Temporal UI reports the delivery-failure workflow as **Completed**, because the
workflow returned a business result with `step=failed`. The API correctly exposes
that failed step and no result. This distinction is intentional in the current code.

## Repeat

Requires Docker Compose 2.24.4+ and a Playwright-enabled browser runner. Swagger's
initial page load also uses its CDN assets. Browser E2E is separate from the
offline pytest container suite.

```bash
docker compose -p northdesk-e2e -f docker-compose.yml -f tests/e2e/compose.yml up --build -d --wait
```

Load the function in `tests/e2e/swagger.playwright.js` using the Playwright MCP
`browser_run_code_unsafe` tool. Pass the existing absolute screenshot directory as
`artifactDir`; `baseURL` defaults to `http://127.0.0.1:8001`. A fresh suffix is
generated automatically. The returned `pendingRestartTicket` supports the next phase:

```bash
docker compose -p northdesk-e2e -f docker-compose.yml -f tests/e2e/compose.yml restart worker
```

Run the function again with `resumeTicket` set to that ticket ID. For the delivery
failure, recreate the isolated worker with the extra override, then invoke the
function with `expectFailed: true`:

```bash
docker compose -p northdesk-e2e -f docker-compose.yml -f tests/e2e/compose.yml -f tests/e2e/fail-helpdesk.yml up -d --no-deps worker
```

For the persistence read, restart the isolated Temporal/API/worker containers and
invoke with `readCompletedTicket` set to the successfully resumed refund's ID.
`http://127.0.0.1:8234` is the isolated Temporal UI.

After inspection, stop only the test stack, preserving its volumes:

```bash
docker compose -p northdesk-e2e -f docker-compose.yml -f tests/e2e/compose.yml down
```

The fake mode browser run does not establish live-model answer quality, real
payment behavior or production browser compatibility beyond the tested browser.
