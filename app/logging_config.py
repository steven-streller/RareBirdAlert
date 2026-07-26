import json
import logging
import os
from datetime import UTC, datetime

# Attributes every LogRecord has regardless of what was logged - anything
# else on the record came from logger.xxx(msg, extra={...}) and is surfaced
# as its own JSON field, which is the actual point of "structured" logging:
# facetable key/value context, not just a JSON-wrapped free-text message.
_STANDARD_LOG_RECORD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    # uvicorn's own default formatter uses this internally for its colored
    # console output - it's unresolved template junk (raw ANSI codes, "%d"
    # placeholders) here, not meaningful structured data, so it's excluded
    # alongside the genuinely standard attributes above.
    "color_message",
}


class JSONFormatter(logging.Formatter):
    """One JSON object per line, so log aggregators (Loki, CloudWatch, ELK,
    ...) can filter/facet on level, logger, and any extra fields instead of
    grep'ing free-text messages. Pairs with the /metrics endpoint - metrics
    answer "how much/how often", structured logs answer "what exactly
    happened".
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # ensure_ascii=False keeps German log messages (umlauts etc.)
        # human-readable in raw output instead of \uXXXX-escaped - still
        # valid JSON either way, this is purely a readability choice.
        # default=str covers any extra value that isn't JSON-serializable
        # on its own (e.g. an object) - logging must never itself raise.
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """LOG_FORMAT=json switches every log line (the app's own loggers, plus
    uvicorn's access/error logs and APScheduler's) to structured JSON.
    Defaults to a plain-text format, unchanged in spirit from before.
    """
    log_format = os.environ.get("LOG_FORMAT", "text").strip().lower()
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Uvicorn configures its own "uvicorn"/"uvicorn.access"/"uvicorn.error"
    # loggers - with their own handlers and propagate=False - before this
    # module is even imported (it sets that up while building its Config,
    # which happens before loading the ASGI app string). Clearing their
    # handlers and re-enabling propagation defers to the root handler above,
    # so uvicorn's access/error logs end up in the same format as everything
    # else instead of uvicorn's own colored console formatter.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
