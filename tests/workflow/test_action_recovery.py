import sqlite3
import uuid

from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.providers import FakeProvider
from northdesk.domain.models import Approval, Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk, TemporaryHelpdeskError
from northdesk.workflows.ticket import TicketWorkflow


async def test_temporal_retry_after_committed_refund_does_not_repeat_effect(temporal_env, tmp_path):
    import asyncio

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
