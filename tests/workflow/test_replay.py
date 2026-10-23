from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from northdesk.workflows.ticket import TicketWorkflow

HISTORIES = Path(__file__).resolve().parents[1] / "fixtures" / "histories"


@pytest.mark.parametrize("scenario", ["reply", "approved", "rejected", "expired", "failed"])
async def test_current_workflow_replays_recorded_history(scenario):
    history = WorkflowHistory.from_json(
        f"ticket/replay-{scenario}", (HISTORIES / f"{scenario}.json").read_text()
    )
    await Replayer(workflows=[TicketWorkflow]).replay_workflow(history)
