import os
from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment


@pytest.fixture
async def temporal_env():
    binary = Path(os.environ.get("TEMPORAL_TEST_SERVER", ".tools/temporal-test-server")).resolve()
    if not binary.is_file():
        pytest.fail("Build the tests image or run: uv run scripts/download_test_server.py")
    async with await WorkflowEnvironment.start_time_skipping(
        test_server_existing_path=str(binary),
    ) as env:
        yield env
