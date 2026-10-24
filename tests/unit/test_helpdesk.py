import sqlite3
import subprocess
import sys
import threading
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


def test_concurrent_first_writes_create_only_one_effect(tmp_path):
    path = str(tmp_path / "helpdesk.db")
    service = FakeHelpdesk(path)
    barrier = threading.Barrier(32)

    def first_write(_):
        barrier.wait(timeout=10)
        return service.execute(REQUEST)

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(first_write, range(32)))
    assert results == [results[0]] * 32
    assert count(path) == 1


def test_abrupt_process_exit_after_commit_keeps_receipt(tmp_path):
    path = str(tmp_path / "helpdesk.db")
    script = """
import json, os, sys
from northdesk.domain.models import ActionInput, Decision, Ticket
from northdesk.services.helpdesk import FakeHelpdesk
request = json.loads(sys.argv[2])
action = ActionInput(Ticket(**request['ticket']), Decision(**request['decision']),
                     request['idempotency_key'])
FakeHelpdesk(sys.argv[1], after_commit=lambda: os._exit(73)).execute(action)
"""
    import json
    from dataclasses import asdict

    child = subprocess.run(
        [sys.executable, "-c", script, path, json.dumps(asdict(REQUEST))],
        capture_output=True,
        timeout=15,
    )
    assert child.returncode == 73, child.stderr.decode()
    receipt = FakeHelpdesk(path, failure_rate=1).execute(REQUEST)
    assert receipt["action"] == "refund"
    assert count(path) == 1


def test_database_connections_are_closed_on_success_duplicate_and_failure(tmp_path, monkeypatch):
    opened = []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(service):
        connection = sqlite3.connect(service.path, factory=TrackedConnection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(FakeHelpdesk, "_connect", connect)
    try:
        path = str(tmp_path / "connections.db")
        service = FakeHelpdesk(path)
        service.execute(REQUEST)
        service.execute(REQUEST)
        with pytest.raises(IdempotencyConflict):
            service.execute(replace(REQUEST, decision=replace(REQUEST.decision, amount_eur="1.00")))
        failure = FakeHelpdesk(path, failure_rate=1)
        with pytest.raises(TemporaryHelpdeskError):
            failure.execute(replace(REQUEST, idempotency_key="new-key"))
        assert opened
        assert all(connection.closed for connection in opened)
    finally:
        for connection in opened:
            connection.close()
