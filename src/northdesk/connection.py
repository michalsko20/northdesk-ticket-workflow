import asyncio
import logging

from temporalio.client import Client, Interceptor

from northdesk.settings import Settings
from northdesk.tracing import _enabled


async def connect(settings: Settings) -> Client:
    interceptors: list[Interceptor] = []
    if _enabled():
        from temporalio.contrib.opentelemetry import TracingInterceptor

        interceptors.append(TracingInterceptor())
    for attempt in range(12):
        try:
            return await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                interceptors=interceptors,
            )
        except (RuntimeError, OSError):
            if attempt == 11:
                raise
            logging.getLogger(__name__).warning("temporal_connection_retry")
            await asyncio.sleep(min(0.5 * 2**attempt, 3))
    raise AssertionError("unreachable")
