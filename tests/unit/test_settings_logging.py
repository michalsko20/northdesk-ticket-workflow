import json
import logging

import pytest
from pydantic import ValidationError

from northdesk.logging import JsonFormatter
from northdesk.settings import Settings


def test_fake_needs_no_key_and_real_requires_one():
    assert Settings(_env_file=None, llm_mode="fake", openai_api_key="").llm_mode == "fake"
    with pytest.raises(ValidationError, match="OPENAI_API_KEY is required"):
        Settings(_env_file=None, llm_mode="openai", openai_api_key="")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, helpdesk_failure_rate=1.1)


def test_json_logs_preserve_ticket_and_workflow_context():
    record = logging.LogRecord("northdesk", logging.INFO, "test", 1, "ticket_step", (), None)
    record.ticket_id = "T-1"
    record.workflow_id = "ticket/T-1"
    entry = json.loads(JsonFormatter().format(record))
    assert entry["ticket_id"] == "T-1"
    assert entry["workflow_id"] == "ticket/T-1"
    assert entry["event"] == "ticket_step"
