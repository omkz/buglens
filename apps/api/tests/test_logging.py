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
        'GET /github/oauth/callback?code=super-secret-code&state=super-secret-state'
        '&installation_id=555 HTTP/1.1" 307'
    )

    captured = capsys.readouterr()
    assert "super-secret-code" not in captured.out
    assert "super-secret-state" not in captured.out
    assert "code=[REDACTED]" in captured.out
    assert "state=[REDACTED]" in captured.out
    # Non-sensitive params are left untouched.
    assert "installation_id=555" in captured.out


def test_oauth_state_redacted_in_query_string(capsys):
    configure_logging(level="INFO", log_format="console")

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.info("GET /github/oauth/callback?state=csrf-token-xyz HTTP/1.1\" 307")

    captured = capsys.readouterr()
    assert "csrf-token-xyz" not in captured.out
    assert "state=[REDACTED]" in captured.out


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


def test_top_level_structured_secret_field_is_redacted(capsys):
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("tests.structured")
    logger.info(
        "github_oauth_exchange",
        access_token="fake-user-access-token-value",  # noqa: S106 test fixture, not a real secret
        github_username="octocat",
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["access_token"] == "[REDACTED]"
    # Safe fields are unaffected.
    assert payload["github_username"] == "octocat"


def test_nested_secret_field_is_redacted(capsys):
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("tests.structured")
    logger.info(
        "github_api_response",
        request={
            "headers": {"Authorization": "Bearer fake-nested-secret"},
            "installation_id": 555,
        },
        items=[{"cookie": "fake-session-cookie"}, {"project_id": 42}],
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert payload["request"]["installation_id"] == 555
    assert payload["items"][0]["cookie"] == "[REDACTED]"
    assert payload["items"][1]["project_id"] == 42


def test_safe_identifiers_remain_visible(capsys):
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("tests.structured")
    logger.info(
        "investigation_started",
        github_username="octocat",
        installation_id=555,
        project_id="proj_123",
        investigation_id="inv_456",
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["github_username"] == "octocat"
    assert payload["installation_id"] == 555
    assert payload["project_id"] == "proj_123"
    assert payload["investigation_id"] == "inv_456"


def test_structured_redaction_keeps_json_output_valid(capsys):
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("tests.structured")
    logger.info(
        "github_oauth_exchange",
        state="fake-csrf-state",  # noqa: S106 test fixture, not a real secret
        client_secret="fake-client-secret",  # noqa: S106 test fixture, not a real secret
        nested={"refresh_token": "fake-refresh-token", "installation_id": 1},
    )

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(lines) == 1
    payload = json.loads(lines[0])  # raises if not valid JSON
    assert payload["state"] == "[REDACTED]"
    assert payload["client_secret"] == "[REDACTED]"
    assert payload["nested"]["refresh_token"] == "[REDACTED]"
    assert payload["nested"]["installation_id"] == 1


def test_session_secret_is_treated_as_sensitive(capsys):
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("tests.structured")
    logger.info(
        "app_config_loaded",
        session_secret="fake-session-secret",  # noqa: S106 test fixture, not a real secret
        session_cookie_secure=False,
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["session_secret"] == "[REDACTED]"
    # Unrelated, non-sensitive fields are untouched.
    assert payload["session_cookie_secure"] is False
