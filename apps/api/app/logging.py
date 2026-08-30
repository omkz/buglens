"""Centralized structlog + stdlib logging configuration.

Configured once at application startup (see app/main.py) so Buglensa's own
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
import traceback
from collections.abc import Mapping

import structlog

_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")

_REDACTED = "[REDACTED]"

# Keys treated as sensitive wherever they appear in structured log fields,
# including nested dicts/lists (see redact_sensitive_fields below). Matched
# case-insensitively, with "-" and "_" treated the same, so e.g. a
# "Set-Cookie" header key is caught the same as "set_cookie".
_SENSITIVE_FIELD_KEYS = frozenset(
    {
        "code",
        "state",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "authorization",
        "cookie",
        "set_cookie",
        "session_secret",
    }
)

# Third-party stdlib loggers we integrate with (Uvicorn's access log,
# httpx's request log) log the raw request line, including its query
# string. Our own OAuth callback receives the authorization `code` and the
# CSRF `state` as query parameters, so without this they would otherwise
# end up in plain access logs. Redact known-sensitive query values
# wherever they appear.
_SENSITIVE_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)\b(code|state|token|access_token|refresh_token|client_secret|private_key)=([^&\s\"']+)"
)
_QUERY_PARAM_REPLACEMENT = rf"\1={_REDACTED}"


def _redact_sensitive_query_params(text: str) -> str:
    return _SENSITIVE_QUERY_PARAM_PATTERN.sub(_QUERY_PARAM_REPLACEMENT, text)


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
    """Strips known-sensitive query-string values from raw log records.

    Applied at the shared handler so it covers every integrated log
    source (Buglensa, FastAPI, Uvicorn) uniformly, without any of them
    needing to know about redaction themselves. This handles *unstructured*
    text such as Uvicorn's access log line; structured event fields are
    handled separately by redact_sensitive_fields below.
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


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    return key.strip().lower().replace("-", "_") in _SENSITIVE_FIELD_KEYS


def _redact_structured_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _REDACTED
            if _is_sensitive_key(key)
            else _redact_structured_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_structured_value(item) for item in value)
    return value


def redact_sensitive_fields(
    logger: object, method_name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    """Structlog processor: recursively redact sensitive fields by key.

    Covers top-level structured log fields (e.g.
    ``logger.info("x", access_token=token)``) as well as nested
    dicts/lists (e.g. ``{"github": {"headers": {"Authorization": "..."}}}``).
    Safe identifiers -- GitHub username, installation/project/investigation
    IDs, etc. -- are untouched since their key names aren't in the
    sensitive set.
    """
    for key, value in list(event_dict.items()):
        if _is_sensitive_key(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_structured_value(value)
    return event_dict


def format_safe_exception_info(
    logger: object, method_name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    """Render traceback chains without serializing exception messages.

    Provider exceptions may embed response bodies or request data in their
    messages. Stack frames and exception types retain the diagnostic control
    flow while keeping those arbitrary values out of production logs.
    """
    raw_exc_info = event_dict.pop("exc_info", None)
    if not raw_exc_info:
        return event_dict
    if raw_exc_info is True:
        exc_info = sys.exc_info()
    elif isinstance(raw_exc_info, BaseException):
        exc_info = (
            type(raw_exc_info),
            raw_exc_info,
            raw_exc_info.__traceback__,
        )
    elif isinstance(raw_exc_info, tuple) and len(raw_exc_info) == 3:
        exc_info = raw_exc_info
    else:
        return event_dict

    exception = exc_info[1]
    if not isinstance(exception, BaseException):
        return event_dict
    event_dict["exception"] = _format_safe_exception_chain(exception)
    return event_dict


def _format_safe_exception_chain(exception: BaseException) -> str:
    lines: list[str] = []
    seen: set[int] = set()

    def append(current: BaseException) -> None:
        if id(current) in seen:
            return
        seen.add(id(current))
        if current.__cause__ is not None:
            append(current.__cause__)
            lines.extend(
                [
                    "\nThe above exception was the direct cause of the following "
                    "exception:\n\n"
                ]
            )
        elif current.__context__ is not None and not current.__suppress_context__:
            append(current.__context__)
            lines.extend(
                [
                    "\nDuring handling of the above exception, another exception "
                    "occurred:\n\n"
                ]
            )
        if current.__traceback__ is not None:
            lines.append("Traceback (most recent call last):\n")
            for frame in traceback.extract_tb(current.__traceback__):
                lines.append(
                    f'  File "{frame.filename}", line {frame.lineno}, '
                    f"in {frame.name}\n"
                )
        exception_type = type(current)
        qualified_name = exception_type.__qualname__
        if exception_type.__module__ not in {"builtins", "__main__"}:
            qualified_name = f"{exception_type.__module__}.{qualified_name}"
        lines.append(f"{qualified_name}\n")

    append(exception)
    return "".join(lines).rstrip()


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
            format_safe_exception_info,
            # Runs for every record, native or foreign, right before
            # rendering -- the single place structured fields are final.
            redact_sensitive_fields,
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
