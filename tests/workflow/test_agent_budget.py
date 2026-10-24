import uuid

from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.providers import MalformedResponse, ToolCall, Turn
from northdesk.domain.models import Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.workflows.ticket import TicketWorkflow


async def test_retry_budget_bounds_total_provider_rounds_and_read_tools(temporal_env, tmp_path):
    class CountingCatalog(Catalog):
        customer_reads = 0

        def get_customer(self, customer_id):
            self.customer_reads += 1
            return super().get_customer(customer_id)

    class FinalRoundFails:
        steps = 0

        async def step(self, messages, tools):
            self.steps += 1
            round_number = (self.steps - 1) % 6
            if round_number == 5:
                raise MalformedResponse("unreadable final response")
            count = (3, 3, 2, 2, 2)[round_number]
            return Turn(
                calls=[ToolCall(f"{self.steps}-{i}", "get_customer", "{}") for i in range(count)]
            )

    provider, catalog = FinalRoundFails(), CountingCatalog()
    activities = Activities(provider, catalog, FakeHelpdesk(str(tmp_path / "effects.db")))
    queue = f"total-budget-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[TicketWorkflow],
        activities=[activities.decide_ticket, activities.validate_ticket, activities.act_on_ticket],
    ):
        state = await temporal_env.client.execute_workflow(
            TicketWorkflow.run,
            Ticket("budget", "C-200", "Password", "Forgot password"),
            id="ticket/total-budget",
            task_queue=queue,
        )
        assert state.step == "completed"
        assert state.decision.reason == "decision_failed:MalformedResponse"
        assert state.result["action"] == "escalate"
        assert provider.steps == 18  # Six rounds for each of three activity attempts.
        assert catalog.customer_reads == 39  # Twelve tools + existence check per attempt.
