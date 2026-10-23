import uuid
from datetime import timedelta

from temporalio import workflow
from temporalio.worker import Worker


@workflow.defn
class SmokeWorkflow:
    def __init__(self):
        self.approved = False

    @workflow.query
    def status(self) -> bool:
        return self.approved

    @workflow.update
    def approve(self) -> bool:
        self.approved = True
        return self.approved

    @workflow.run
    async def run(self) -> bool:
        try:
            await workflow.wait_condition(lambda: self.approved, timeout=timedelta(days=1))
        except TimeoutError:
            pass
        return self.approved


async def test_query_update_and_time_skipping(temporal_env):
    queue = f"smoke-{uuid.uuid4()}"
    async with Worker(temporal_env.client, task_queue=queue, workflows=[SmokeWorkflow]):
        handle = await temporal_env.client.start_workflow(
            SmokeWorkflow.run,
            id=str(uuid.uuid4()),
            task_queue=queue,
        )
        assert await handle.query(SmokeWorkflow.status) is False
        assert await handle.execute_update(SmokeWorkflow.approve) is True
        assert await handle.result() is True
        timed = await temporal_env.client.start_workflow(
            SmokeWorkflow.run,
            id=str(uuid.uuid4()),
            task_queue=queue,
        )
        assert await timed.result() is False
