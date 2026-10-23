from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from northdesk.activities import Activities
from northdesk.agent.providers import MalformedResponse, PermanentAgentError, TransientAgentError
from northdesk.domain.models import ActionInput, Decision, Ticket
from northdesk.services.catalog import Catalog
from northdesk.services.helpdesk import FakeHelpdesk, IdempotencyConflict, TemporaryHelpdeskError

TICKET = Ticket("activity", "C-100", "Refund", "Refund")


@pytest.mark.parametrize(
    "error,permanent",
    [
        (PermanentAgentError("bad tool"), True),
        (MalformedResponse("unreadable"), False),
        (TransientAgentError("connection"), False),
    ],
)
async def test_real_decide_activity_preserves_error_retry_classification(
    tmp_path, error, permanent
):
    provider = SimpleNamespace(step=AsyncMock(side_effect=error))
    activities = Activities(provider, Catalog(), FakeHelpdesk(str(tmp_path / "effects.db")))
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(activities.decide_ticket, TICKET)
    assert caught.value.non_retryable is permanent
    assert caught.value.type == type(error).__name__
    provider.step.assert_awaited_once()


async def test_unknown_customer_does_not_consume_model_calls(tmp_path):
    provider = SimpleNamespace(step=AsyncMock())
    activities = Activities(provider, Catalog(), FakeHelpdesk(str(tmp_path / "effects.db")))
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(
            activities.decide_ticket, Ticket("missing", "C-999", "Hello", "Hello")
        )
    assert caught.value.type == "UnknownCustomer"
    assert caught.value.non_retryable
    provider.step.assert_not_called()


@pytest.mark.parametrize(
    "error,permanent",
    [(IdempotencyConflict("conflict"), True), (TemporaryHelpdeskError("offline"), False)],
)
async def test_real_action_activity_preserves_error_retry_classification(error, permanent):
    helpdesk = MagicMock(spec=FakeHelpdesk)
    helpdesk.execute.side_effect = error
    activities = Activities(SimpleNamespace(step=AsyncMock()), Catalog(), helpdesk)
    request = ActionInput(TICKET, Decision("other", "normal", "escalate", reason="test"), "key")
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(activities.act_on_ticket, request)
    assert caught.value.non_retryable is permanent
    assert caught.value.type == type(error).__name__
    helpdesk.execute.assert_called_once_with(request)
