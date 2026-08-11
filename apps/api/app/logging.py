"""Centralized structlog + stdlib logging configuration.

Configured once at application startup (see app/main.py) so BugLens' own
structlog loggers and the stdlib loggers used by FastAPI/Uvicorn share one
consistent rendering pipeline: human-readable locally, single-line JSON on
Cloud Run. Always writes to stdout — no log files.

Kept isolated from business logic: this module only wires up rendering, it
does not know anything about GitHub, projects, or any other domain code.
"""

from __future__ import annotations

import logging
import re
import sys

import structlog

_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")

# Third-party stdlib loggers we integrate with (Uvicorn's access log,
# httpx's request log) log the raw request line, including its query
# string. Our own OAuth callback receives the authorization `code` as a
# query parameter, so without this it would otherwise end up in plain
# access logs. Redact known-sensitive query values wherever they appear.
_SENSITIVE_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)\b(code|token|access_token|client_secret|private_key)=([^&\s\"']+)"
)


def _redact_sensitive_query_params(text: str) -> str:
    return _SENSITIVE_QUERY_PARAM_PATTERN.sub(r"\1=[REDACTED]", text)


def _maybe_redact(value: object) -> object:
    # Third-party loggers (httpx, Uvicorn) may pass non-str objects like
    # httpx.URL as message args, so compare against their string form
    # rather than relying on isinstance(value, str). Non-matching values
    # (e.g. an int used for %d formatting) are returned unchanged.
    text = str(value)
    if not _SENSITIVE_QUERY_PARAM_PATTERN.search(text):
        return value
    return _redact_sensitive_query_params(text)


class _RedactSensitiveDataFilter(logging.Filter):
    """Strips known-sensitive query-string values from log records.

    Applied at the shared handler so it covers every integrated log
    source (BugLens, FastAPI, Uvicorn) uniformly, without any of them
    needing to know about redaction themselves.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg is not None:
            record.msg = _maybe_redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {
                key: _maybe_redact(value) for key, value in record.args.items()
            }
        elif record.args:
            record.args = tuple(_maybe_redact(arg) for arg in record.args)
        return True


def configure_logging(*, level: str = "INFO", log_format: str = "console") -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied only to records that did *not* originate from structlog
        # (e.g. Uvicorn's own logger calls), so they get the same
        # timestamp/level/logger-name fields before rendering.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_RedactSensitiveDataFilter())

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())

    # Uvicorn installs its own handlers (with propagate=False) before this
    # module is imported. Reset them so Uvicorn's logs flow through the same
    # handler/formatter as everything else instead of bypassing it.
    for name in _UVICORN_LOGGER_NAMES:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
