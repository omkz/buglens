import json
import logging

import structlog

from app.logging import configure_logging


def test_console_format_respects_log_level(capsys):
    configure_logging(level="WARNING", log_format="console")
    assert logging.getLogger().level == logging.WARNING

    logger = structlog.get_logger("tests.console")
    logger.info("should_not_appear")
    logger.warning("should_appear")

    captured = capsys.readouterr()
    assert "should_not_appear" not in captured.out
    assert "should_appear" in captured.out


def test_json_format_emits_valid_single_line_json(capsys):
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("tests.json")
    logger.info("sample_event", foo="bar")

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["event"] == "sample_event"
    assert payload["foo"] == "bar"
    assert payload["level"] == "info"


def test_uvicorn_loggers_route_through_the_shared_handler(capsys):
    configure_logging(level="INFO", log_format="json")

    uvicorn_logger = logging.getLogger("uvicorn.error")
    assert uvicorn_logger.handlers == []
    assert uvicorn_logger.propagate is True

    uvicorn_logger.info("uvicorn_test_event")

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    payload = json.loads(lines[-1])
    assert payload["event"] == "uvicorn_test_event"
    assert payload["logger"] == "uvicorn.error"


def test_sensitive_query_params_are_redacted_from_third_party_logs(capsys):
    # Mirrors how Uvicorn's access log / httpx's request log embed the raw
    # request line (e.g. our OAuth callback URL) as the log message.
    configure_logging(level="INFO", log_format="console")

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.info(
        'GET /github/oauth/callback?code=super-secret-code&state=abc123 HTTP/1.1" 307'
    )

    captured = capsys.readouterr()
    assert "super-secret-code" not in captured.out
    assert "code=[REDACTED]" in captured.out
    # Non-sensitive params are left untouched.
    assert "state=abc123" in captured.out


def test_sensitive_query_params_are_redacted_from_non_string_args(capsys):
    # httpx logs the request URL as an httpx.URL object (via %s-style
    # args), not a plain str -- make sure redaction still applies.
    class _FakeUrl:
        def __str__(self) -> str:
            return "https://github.com/login/oauth/access_token?client_secret=topsecret"

    configure_logging(level="INFO", log_format="console")

    logger = logging.getLogger("httpx")
    logger.info("HTTP Request: GET %s", _FakeUrl())

    captured = capsys.readouterr()
    assert "topsecret" not in captured.out
    assert "client_secret=[REDACTED]" in captured.out
