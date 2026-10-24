"""OpenTelemetry tracing setup shared by the API and the worker.

Enable with OTEL_SDK_DISABLED=false (or unset). The default endpoint targets
a local OpenTelemetry Collector; adjust OTEL_EXPORTER_OTLP_ENDPOINT for other
backends. All spans use the ``northdesk`` service name with the process role
as an attribute so API and worker traces correlate in a single view.
"""

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

SERVICE_NAME = "northdesk"


def _enabled() -> bool:
    """OTel SDK is disabled by default to keep offline tests deterministic."""
    return os.environ.get("OTEL_SDK_DISABLED", "true").lower() in ("false", "0", "no")


def configure_tracing(role: str) -> trace.TracerProvider | None:
    """Initialise a TracerProvider with OTLP export; returns None when disabled."""
    if not _enabled():
        return None
    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.role": role,
            "service.version": "0.1.0",
        }
    )
    provider = TracerProvider(resource=resource)
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info("tracing_configured", extra={"role": role, "endpoint": endpoint})
    return provider
