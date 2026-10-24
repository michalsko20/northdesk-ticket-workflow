"""Durable fake: its committed action row IS the simulated external side effect."""

import hashlib
import json
import os
import random
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from northdesk.domain.models import ActionInput


class TemporaryHelpdeskError(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class FakeHelpdesk:
    def __init__(
        self,
        path: str,
        failure_rate: float = 0,
        random_value: Callable[[], float] = random.random,
        after_commit: Callable[[], None] | None = None,
    ) -> None:
        if not 0 <= failure_rate <= 1:
            raise ValueError("failure_rate must be between 0 and 1")
        self.path = path
        self.failure_rate = failure_rate
        self.random_value = random_value
        self.after_commit = after_commit
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        # Restrict the state directory to owner-only access.
        os.chmod(parent, 0o700)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS actions (
                idempotency_key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                result TEXT NOT NULL
            )""")
        # Restrict the database file to owner-only access (best effort on
        # filesystems that do not support POSIX modes).
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def execute(self, request: ActionInput) -> dict:
        payload = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            # Serialize concurrent callers so receipt and fake effect are atomic.
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT fingerprint, result FROM actions WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise IdempotencyConflict("idempotency_payload_conflict")
                return json.loads(previous[1])
            if self.random_value() < self.failure_rate:
                raise TemporaryHelpdeskError("helpdesk_temporarily_unavailable")
            result = {
                "action_id": hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:20],
                "ticket_id": request.ticket.ticket_id,
                "action": request.decision.action,
                "details": asdict(request.decision),
            }
            db.execute(
                "INSERT INTO actions VALUES (?, ?, ?)",
                (request.idempotency_key, fingerprint, json.dumps(result, sort_keys=True)),
            )
        if self.after_commit:
            self.after_commit()
        return result
