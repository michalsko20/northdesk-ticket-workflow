import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from northdesk.domain.models import ActionInput, Decision, Ticket
from northdesk.services.helpdesk import FakeHelpdesk, IdempotencyConflict, TemporaryHelpdeskError

REQUEST = ActionInput(
    Ticket("test", "C-100", "Refund", "Duplicate payment"),
    Decision("billing", "normal", "refund", invoice_id="INV-1002", amount_eur="49.00"),
    "ticket/test/action",
)


def count(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT count(*) FROM actions").fetchone()[0]


def test_refund_once_across_restarts_and_concurrent_calls(tmp_path):
    path = str(tmp_path / "helpdesk.db")
    first = FakeHelpdesk(path).execute(REQUEST)
    restarted = FakeHelpdesk(path, failure_rate=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: restarted.execute(REQUEST), range(8)))
    assert results == [first] * 8
    assert count(path) == 1


def test_lost_confirmation_after_commit_does_not_repeat_refund(tmp_path):
    path = str(tmp_path / "helpdesk.db")

    def lose_ack():
        raise TemporaryHelpdeskError("connection_lost_after_commit")

    with pytest.raises(TemporaryHelpdeskError):
        FakeHelpdesk(path, after_commit=lose_ack).execute(REQUEST)
    result = FakeHelpdesk(path).execute(REQUEST)
    assert result["action"] == "refund"
    assert count(path) == 1


def test_same_key_different_payload_conflicts(tmp_path):
    path = str(tmp_path / "helpdesk.db")
    service = FakeHelpdesk(path)
    service.execute(REQUEST)
    altered = replace(REQUEST, decision=replace(REQUEST.decision, amount_eur="1.00"))
    with pytest.raises(IdempotencyConflict):
        service.execute(altered)
    assert count(path) == 1


def test_transient_failure_before_commit_has_no_effect(tmp_path):
    path = str(tmp_path / "helpdesk.db")
    service = FakeHelpdesk(path, failure_rate=0.5, random_value=lambda: 0.1)
    with pytest.raises(TemporaryHelpdeskError):
        service.execute(REQUEST)
    assert count(path) == 0
    service.random_value = lambda: 0.9
    assert service.execute(REQUEST)["action"] == "refund"
    assert count(path) == 1
