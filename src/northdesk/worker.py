import asyncio
import signal
from datetime import timedelta

from temporalio.worker import Worker

from northdesk.activities import Activities
from northdesk.agent.glm import GLMProvider
from northdesk.agent.providers import FakeProvider, OpenAIProvider, Provider
from northdesk.connection import connect
from northdesk.logging import configure_logging
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk
from northdesk.settings import Settings
from northdesk.workflows.ticket import TicketWorkflow


async def main() -> None:
    configure_logging()
    settings = Settings()
    provider: Provider
    if settings.llm_mode == "fake":
        provider = FakeProvider()
    elif settings.llm_mode == "glm":
        provider = GLMProvider(
            settings.zai_api_key.get_secret_value(), settings.glm_model, settings.glm_base_url
        )
    else:
        provider = OpenAIProvider(settings.openai_api_key.get_secret_value(), settings.llm_model)
    activities = Activities(
        provider, Catalog(), FakeHelpdesk(settings.helpdesk_db_path, settings.helpdesk_failure_rate)
    )
    client = await connect(settings)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        async with Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[TicketWorkflow],
            activities=[
                activities.decide_ticket,
                activities.validate_ticket,
                activities.act_on_ticket,
            ],
            max_concurrent_activities=settings.worker_activity_concurrency,
            max_concurrent_workflow_tasks=settings.worker_workflow_concurrency,
            max_cached_workflows=settings.worker_max_cached_workflows,
            graceful_shutdown_timeout=timedelta(seconds=20),
        ):
            await stop.wait()
    finally:
        if isinstance(provider, OpenAIProvider):
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
