# Frozen workflow histories

Synthetic `TicketWorkflow` executions recorded on 2026-10-23 against the pinned
Temporal test server, using the production workflow from handoff commit `cfb50f7`.
Worker identities were replaced with `review-worker`; business events and payloads
are unchanged. No live customer data or credentials are included.

Cases: reply, approved refund, rejected refund, approval expiry, exhausted action
retries. Activity implementations came from `FakeActivities` in
`tests/workflow/test_ticket.py`; all inputs are packaged demonstration data.

These files are compatibility fixtures, not test output to regenerate on every run.
`tests/workflow/test_replay.py` must keep replaying them after workflow changes.
Changing or replacing a fixture requires an explicit compatibility decision.
