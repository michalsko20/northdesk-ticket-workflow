# Browser verification — 2026-10-24

Actual Playwright interactions with Swagger at `localhost:8002` and Temporal UI
at `localhost:8235`, on the isolated fake-mode stack. The driver is
[`tests/e2e/swagger.playwright.js`](../../../tests/e2e/swagger.playwright.js).

- [Main run](main.json): seven example actions, approval, rejection, duplicate
  start, closed approval 409, missing ticket 404, invalid body 422, and dropped
  approval acknowledgement after the server accepted it.
- [Worker restart](restart.json): pending approval survives replacement; approval
  then completes one refund.
- [Retry exhaustion](failed.json): injected Helpdesk failure reports `failed`,
  without claiming delivery or storing an action.
- [Full-stack restart](stack-restart.json): completed refund remains readable with
  the same receipt after Temporal/API/worker restart.
- `18-temporal-list.png`, `19-temporal-completed.png`: actual Temporal UI list and
  completed execution timeline.

20 PNG screenshots are retained alongside the JSON. Each browser run recorded
zero JavaScript page errors. Expected HTTP rejection responses and the deliberately
aborted approval response are part of the scenarios. Independent Temporal history
and SQLite counts are in [the audit](../independent-audit.json).
