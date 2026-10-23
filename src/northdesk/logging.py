import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("ticket_id", "workflow_id", "step", "attempt"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        # Temporal also attaches identifiers to its own worker error records.
        for key in ("temporal_activity", "temporal_workflow"):
            context = getattr(record, key, {})
            workflow_id = context.get("workflow_id", "")
            if workflow_id:
                entry.setdefault("workflow_id", workflow_id)
                entry.setdefault("ticket_id", workflow_id.removeprefix("ticket/"))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Provider/HTTP transport logs are not ticket events; avoid unnecessary details.
    logging.getLogger("httpx").setLevel(logging.WARNING)
