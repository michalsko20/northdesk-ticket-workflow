import asyncio
import logging

from temporalio.client import Client

from northdesk.settings import Settings


async def connect(settings: Settings) -> Client:
    for attempt in range(12):
        try:
            return await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
        except (RuntimeError, OSError):
            if attempt == 11:
                raise
            logging.getLogger(__name__).warning("temporal_connection_retry")
            await asyncio.sleep(min(0.5 * 2**attempt, 3))
    raise AssertionError("unreachable")
