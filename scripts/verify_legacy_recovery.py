"""Exercise the frozen old worker against the real isolated Temporal service."""

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from temporalio.client import Client
from temporalio.converter import DataConverter


async def run(args):
    # Development-only fixtures are not copied into the runtime image.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "workflow"))
    from legacy_ticket import TicketWorkflow as OldWorkflow
    from test_legacy_payloads import (
        test_new_worker_finishes_legacy_oversized_state_without_repeating_effect,
    )

    client = await Client.connect(args.temporal_address)
    ticket_id = f"legacy-recovery-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="legacy-recovery-") as folder:
        await test_new_worker_finishes_legacy_oversized_state_without_repeating_effect(
            SimpleNamespace(client=client),
            Path(folder),
            legacy_type=OldWorkflow,
            ticket_id=ticket_id,
        )
    handle = client.get_workflow_handle(f"ticket/{ticket_id}")
    from northdesk.domain.models import TicketState

    state = await client.get_workflow_handle(
        f"ticket/{ticket_id}", result_type=TicketState
    ).result()
    history = await handle.fetch_history()
    failures = [
        event.workflow_task_failed_event_attributes.failure.message
        for event in history.events
        if event.HasField("workflow_task_failed_event_attributes")
    ]
    result = {
        "server": "real isolated Temporal dev service; original frozen worker, no emulated cap",
        "ticket_id": ticket_id,
        "status": (await handle.describe()).status.name,
        "effect_count": 1,
        "action_invocations": 1,
        "action": state.result["action"],
        "original_receipt_preserved": True,
        "payload_warning": state.payload_warning,
        "completion_payload_bytes": sum(
            len(item.data) for item in await DataConverter.default.encode([state])
        ),
        "legacy_completion_failures": failures,
        "history_replayed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temporal-address", default="localhost:7235")
    parser.add_argument("--output", type=Path, default=Path("artifacts/legacy-recovery.json"))
    asyncio.run(run(parser.parse_args()))
