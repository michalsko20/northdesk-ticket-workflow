# Fake versus live GLM

Observed on 2026-10-23 with `glm-5.3-flash` using the existing Z.ai profile, the seven packaged example tickets, the shared tool loop and the same validation/approval workflow. Keys and personal profile files are excluded from the repository. This is one development smoke run, not an accuracy or reliability benchmark; future responses may differ.

The service ran through Docker Compose in `LLM_MODE=glm`. Tickets used suffix `-glm-v2`; `scripts/demo.py --approve-refunds` acted as the human approver for the fake refund. Every ticket reached `completed` through the HTTP API. Actions were sent to the fake Helpdesk, not to a real billing system.

| Ticket | Fake decision | Live GLM decision | Difference |
| --- | --- | --- | --- |
| T-1 | account / normal / reply, KB-1 | account / normal / reply, KB-1 | GLM personalized the reply; both use the trusted password instructions. |
| T-2 | billing / normal / refund, INV-1002, EUR 49.00 | billing / normal / refund, INV-1002, EUR 49.00 | Same validated refund; both require the workflow's human approval before acting. |
| T-3 | technical / urgent / escalate | technical / urgent / escalate | Both escalate the enterprise outage; reasons differ in wording. |
| T-4 | account / normal / reply, KB-4 | billing / normal / reply, KB-4 | GLM classifies suspension by its overdue-invoice cause; fake classifies by the account-access symptom. Both categories are permitted and both replies cite KB-4. |
| T-5 | other / low / close | other / low / close | Same outcome for the unrelated restaurant request. |
| T-6 | other / normal / escalate | billing / high / escalate | Fake deliberately proposes an excessive refund and code blocks it. GLM itself recognizes the instruction bypass attempt and proposes escalation. This does not replace the code-level safety tests. |
| T-7 | other / normal / escalate | other / normal / escalate | The trusted customer lookup rejects C-999 before either provider can make a decision; this row does not measure model behavior. |

The initial adapter forced JSON-object response mode together with read tools. Live results exposed a problem not visible in mocked protocol tests: some responses contained JSON planning/logging objects instead of native tool calls. Policy safely escalated those malformed proposals, but the integration was not useful for ordinary tickets.

The corrected adapter uses native tool calling and a `submit_decision` function for the final proposal. It preserves tool-call IDs and GLM continuation fields, and rejects batches mixing final submission with read calls. The successful run above followed that correction. Offline tests cover this protocol and malformed/mixed submissions; live calls are deliberately excluded from the default test suite.

Reproduce with a loaded profile (exported `ZAI_API_KEY`, optionally `GLM_MODEL` and `GLM_BASE_URL`):

```bash
LLM_MODE=glm docker compose up --build -d --wait
python3 scripts/demo.py --suffix=-glm-another-run --approve-refunds
```

Use a fresh suffix so retained workflows do not return the previous decisions. The recorded live run required no weakening of invoice validation, approval, retry limits or action idempotency.
