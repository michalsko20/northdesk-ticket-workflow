import asyncio
import sqlite3
import sys
import uuid

from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.providers import FakeProvider
from northdesk.domain.models import Approval, Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk, TemporaryHelpdeskError
from northdesk.workflows.ticket import TicketWorkflow


async def test_temporal_retry_after_committed_refund_does_not_repeat_effect(temporal_env, tmp_path):
    path = str(tmp_path / "helpdesk.db")
    committed_calls = 0

    def lose_confirmation():
        nonlocal committed_calls
        committed_calls += 1
        raise TemporaryHelpdeskError("lost_acknowledgement")

    activities = Activities(
        FakeProvider(), Catalog(), FakeHelpdesk(path, after_commit=lose_confirmation)
    )
    queue = f"recovery-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide_ticket, activities.validate_ticket, activities.act_on_ticket],
    ):
        handle = await temporal_env.client.start_workflow(
            TicketWorkflow.run,
            Ticket("refund-retry", "C-100", "Charged twice", "Please refund"),
            id=f"ticket/{uuid.uuid4()}",
            task_queue=queue,
        )
        for _ in range(200):
            if (await handle.query(TicketWorkflow.status)).step == "awaiting_approval":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Refund never reached approval")
        await handle.execute_update(TicketWorkflow.approve, Approval(True, "operator"))
        state = await handle.result()
        assert state.step == "completed"
        assert state.result["action"] == "refund"
        assert committed_calls == 1
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 1


async def test_worker_process_dies_after_refund_commit_then_replacement_recovers(
    temporal_env, tmp_path
):
    path = str(tmp_path / "crashed-worker.db")
    queue = f"crashed-worker-{uuid.uuid4()}"
    script = """
import asyncio, os, sys
from temporalio.client import Client
from temporalio.worker import Worker
from northdesk.activities import Activities
from northdesk.agent.providers import FakeProvider
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.workflows.ticket import TicketWorkflow

async def main():
    activities = Activities(FakeProvider(), Catalog(),
        FakeHelpdesk(sys.argv[3], after_commit=lambda: os._exit(73)))
    client = await Client.connect(sys.argv[1])
    async with Worker(client, task_queue=sys.argv[2], workflows=[TicketWorkflow],
            activities=[activities.decide_ticket, activities.validate_ticket,
                        activities.act_on_ticket], max_cached_workflows=0):
        await asyncio.Event().wait()
asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        temporal_env.client.service_client.config.target_host,
        queue,
        path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        handle = await temporal_env.client.start_workflow(
            TicketWorkflow.run,
            Ticket("process-crash", "C-100", "Charged twice", "Please refund"),
            id="ticket/process-crash",
            task_queue=queue,
        )
        for _ in range(200):
            state = await handle.query(TicketWorkflow.status)
            if state.step == "awaiting_approval":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Child worker did not request approval")
        await handle.execute_update(TicketWorkflow.approve, Approval(True, "operator"))
        _, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
        assert child.returncode == 73, stderr.decode()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 1
        activities = Activities(FakeProvider(), Catalog(), FakeHelpdesk(path, failure_rate=1))
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[TicketWorkflow],
            activities=[
                activities.decide_ticket,
                activities.validate_ticket,
                activities.act_on_ticket,
            ],
            max_cached_workflows=0,
        ):
            state = await asyncio.wait_for(handle.result(), timeout=20)
            assert state.step == "completed"
            assert state.approval == Approval(True, "operator")
            assert state.result["action"] == "refund"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 1
    finally:
        if child.returncode is None:
            child.kill()
            await child.communicate()
