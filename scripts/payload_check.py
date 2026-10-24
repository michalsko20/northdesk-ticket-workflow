"""Finite payload-boundary experiment against the isolated real Temporal dev server."""

import argparse
import asyncio
import json
import logging
import sqlite3
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.converter import DataConverter
from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.providers import Turn
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.workflows.ticket import TicketWorkflow


class SizedProvider:
    def __init__(self, size):
        self.size = size

    async def step(self, messages, tools):
        return Turn(
            decision={
                "category": "account",
                "priority": "normal",
                "action": "reply",
                "message": "x" * self.size,
                "articles": ["KB-1"],
            }
        )


async def encoded_bytes(value):
    return sum(len(payload.data) for payload in await DataConverter.default.encode([value]))


async def run(args):
    logging.basicConfig(level=logging.CRITICAL)
    client = await Client.connect(args.temporal_address)
    suffix = str(time.time_ns())[-12:]
    results = []
    with tempfile.TemporaryDirectory(prefix="northdesk-payload-") as folder:
        for size in args.sizes:
            path = str(Path(folder) / f"{size}.db")
            queue = f"payload-{suffix}-{size}"
            activities = Activities(SizedProvider(size), Catalog(), FakeHelpdesk(path))
            ticket = Ticket(f"payload-{suffix}-{size}", "C-200", "Password", "Forgot password")
            result = {"message_characters": size}
            async with Worker(
                client,
                task_queue=queue,
                workflows=[TicketWorkflow],
                activities=[
                    activities.decide_ticket,
                    activities.validate_ticket,
                    activities.act_on_ticket,
                ],
                max_cached_workflows=0,
            ):
                handle = await client.start_workflow(
                    TicketWorkflow.run,
                    ticket,
                    id=f"ticket/{ticket.ticket_id}",
                    task_queue=queue,
                )
                deadline = time.monotonic() + 20
                description = await handle.describe()
                while time.monotonic() < deadline:
                    description = await handle.describe(rpc_timeout=timedelta(seconds=3))
                    if description.status != WorkflowExecutionStatus.RUNNING:
                        break
                    await asyncio.sleep(0.25)
                result["temporal_status"] = description.status.name
                try:
                    state = await handle.query(
                        TicketWorkflow.status, rpc_timeout=timedelta(seconds=3)
                    )
                    result["query_step"] = state.step
                    result["action"] = (state.result or {}).get("action")
                    result["serialized_state_bytes"] = await encoded_bytes(state)
                except Exception as exc:
                    result["query_error"] = type(exc).__name__
                history = await handle.fetch_history()
                failures = []
                for event in history.events:
                    if event.HasField("workflow_task_failed_event_attributes"):
                        attributes = event.workflow_task_failed_event_attributes
                        failures.append(
                            {"cause": attributes.cause, "message": attributes.failure.message}
                        )
                    if event.HasField("workflow_execution_failed_event_attributes"):
                        result["workflow_failure"] = (
                            event.workflow_execution_failed_event_attributes.failure.message
                        )
                result["workflow_task_failures"] = failures
                with sqlite3.connect(path) as db:
                    result["effect_count"] = db.execute("SELECT count(*) FROM actions").fetchone()[
                        0
                    ]
                if description.status == WorkflowExecutionStatus.RUNNING:
                    await handle.terminate("Bounded synthetic payload experiment completed")
                    result["cleanup"] = "synthetic running execution terminated"
            results.append(result)
            print(json.dumps(result), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"injection": "synthetic oversized provider, no live model calls", "results": results},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temporal-address", default="localhost:7235")
    parser.add_argument("--sizes", type=int, nargs="+", default=[100000, 800000, 2200000])
    parser.add_argument("--output", type=Path, default=Path("artifacts/payloads.json"))
    args = parser.parse_args()
    if any(not 1 <= size <= 3000000 for size in args.sizes):
        parser.error("Sizes must be between 1 and 3000000 characters")
    asyncio.run(run(args))
