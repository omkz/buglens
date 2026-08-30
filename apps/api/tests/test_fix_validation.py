from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.investigation_agent.fix_validation import (
    _MAX_OUTPUT,
    _available_loopback_port,
    FixValidationCheck,
    FixValidationContext,
    FixValidationResult,
    FixValidationService,
    _run_command,
)
from app.investigation_agent.repository import (
    FixValidationClaim,
    FixValidationClaimState,
)
from app.investigation_agent.schemas import (
    BrowserExecutionResult,
    BrowserTestPlan,
    FixProposal,
)


def _proposal(path: str = "src/checkout.ts", original: str = "old\n") -> FixProposal:
    return FixProposal.model_validate(
        {
            "summary": "Fix checkout navigation.",
            "files": [
                {
                    "path": path,
                    "original_content": original,
                    "updated_content": "new\n",
                    "explanation": "Use the correct navigation path.",
                }
            ],
        }
    )


def _context(
    proposal: FixProposal | None = None,
    *,
    repository_full_name: str = "octo-org/checkout",
    reproduction_before: str | None = "reproduced",
    reproduction_plan: BrowserTestPlan | None = None,
) -> FixValidationContext:
    return FixValidationContext(
        github_installation_id=987654,
        repository_full_name=repository_full_name,
        default_branch="main",
        fix_proposal=proposal or _proposal(),
        reproduction_plan=reproduction_plan,
        reproduction_before=reproduction_before,
    )


def _settings(*, enabled: bool = False, repositories: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        fix_validation_allow_host_execution=enabled,
        fix_validation_trusted_repositories=repositories,
    )


class BrowserRunner:
    async def run(self, *args, **kwargs):
        raise AssertionError("Browser validation should not run without a supported app.")


@pytest.mark.anyio
async def test_valid_proposal_applies_only_in_temp_workspace_and_cleans_up(
    monkeypatch, tmp_path
):
    from app.investigation_agent import fix_validation as module

    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "checkout.ts").write_text("old\n")
    observed: dict[str, Path | str] = {}

    async def token(**kwargs):
        return "short-lived-token"

    async def materialize(value, context, checkout):
        assert value == "short-lived-token"
        shutil.copytree(source, checkout)
        observed["checkout"] = checkout

    async def check(command, cwd, timeout):
        observed["updated"] = (cwd / "src" / "checkout.ts").read_text()
        return FixValidationCheck(name="safe check", status="passed", output="ok")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(
            enabled=True, repositories="omkz/buglens-demo-target"
        ),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=check,
    )
    # Make one deterministic Python check available.
    (source / "tests").mkdir()

    result = await service.validate(
        _context(
            repository_full_name="omkz/buglens-demo-target",
            reproduction_before=None,
        )
    )

    assert result.status == "validated"
    assert observed["updated"] == "new\n"
    assert (source / "src" / "checkout.ts").read_text() == "old\n"
    assert not Path(observed["checkout"]).exists()
    assert list(tmp_path.glob("buglensa-fix-*")) == []


@pytest.mark.anyio
async def test_host_execution_is_disabled_by_default(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def must_not_run(command, cwd, timeout):
        raise AssertionError("Repository code must remain gated by default.")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(), browser_runner=BrowserRunner(),
        workspace_parent=tmp_path, materializer=materialize,
        command_runner=must_not_run,
    )

    result = await service.validate(_context())

    assert result.status == "blocked"
    assert result.checks[-1].name == "Executable validation gate"


@pytest.mark.anyio
async def test_repository_outside_trusted_allowlist_cannot_execute(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def must_not_run(command, cwd, timeout):
        raise AssertionError("An untrusted repository executed code.")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(enabled=True, repositories="octo-org/checkout-extra"),
        browser_runner=BrowserRunner(), workspace_parent=tmp_path,
        materializer=materialize, command_runner=must_not_run,
    )

    result = await service.validate(_context())

    assert result.status == "blocked"


@pytest.mark.anyio
async def test_source_mismatch_is_stale_and_applies_nothing(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("changed upstream\n")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(), browser_runner=BrowserRunner(),
        workspace_parent=tmp_path, materializer=materialize,
    )

    result = await service.validate(_context())

    assert result.status == "stale_proposal"
    assert result.checks[0].status == "failed"
    assert list(tmp_path.glob("buglensa-fix-*")) == []


@pytest.mark.anyio
async def test_path_traversal_is_blocked(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    outside = tmp_path / "outside.ts"
    outside.write_text("old\n")

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        checkout.mkdir(parents=True)

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(), browser_runner=BrowserRunner(),
        workspace_parent=tmp_path, materializer=materialize,
    )

    result = await service.validate(_context(_proposal("../outside.ts")))

    assert result.status == "blocked"
    assert outside.read_text() == "old\n"


@pytest.mark.anyio
async def test_validation_command_timeout_and_failure(tmp_path):
    timed_out = await _run_command(
        [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 0.01
    )
    failed = await _run_command(
        [sys.executable, "-c", "raise SystemExit(3)"], tmp_path, 5
    )

    assert timed_out.status == "timed_out"
    assert failed.status == "failed"


@pytest.mark.anyio
async def test_validation_command_timeout_terminates_child_process_group(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
        "time.sleep(60)"
    )

    result = await _run_command(
        [sys.executable, "-c", script, str(child_pid_file)], tmp_path, 1
    )

    assert result.status == "timed_out"
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(child_pid, 9)
        pytest.fail("Timed-out validation left a child process running.")


@pytest.mark.anyio
async def test_validation_command_output_is_bounded_while_streaming(tmp_path):
    result = await _run_command(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write('x' * {_MAX_OUTPUT * 20})",
        ],
        tmp_path,
        10,
    )

    assert result.status == "passed"
    assert len(result.output.encode()) <= _MAX_OUTPUT
    assert result.output == "x" * _MAX_OUTPUT


@pytest.mark.anyio
async def test_failed_bounded_check_reports_validation_failed(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def fail(command, cwd, timeout):
        return FixValidationCheck(name="Pytest", status="failed", output="bounded failure")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(enabled=True, repositories="octo-org/checkout"), browser_runner=BrowserRunner(),
        workspace_parent=tmp_path, materializer=materialize, command_runner=fail,
    )

    result = await service.validate(_context())

    assert result.status == "validation_failed"
    assert result.checks[0].output == "bounded failure"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reproduction_before", "reproduction_after", "expected_status"),
    [
        ("reproduced", "not_reproduced", "validated"),
        ("reproduced", "reproduced", "validation_failed"),
        ("reproduced", None, "not_run"),
        ("reproduced", "blocked", "blocked"),
        ("not_reproduced", None, "validated"),
    ],
)
async def test_validated_requires_browser_failure_to_be_resolved(
    monkeypatch, tmp_path, reproduction_before, reproduction_after, expected_status
):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")

    class SemanticService(FixValidationService):
        async def _run_known_checks(self, checkout):
            return [FixValidationCheck(name="Static check", status="passed", output="ok")]

        async def _rerun_browser_when_supported(self, checkout, context, checks):
            return reproduction_after

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = SemanticService(
        settings=_settings(enabled=True, repositories="octo-org/checkout"),
        browser_runner=BrowserRunner(), workspace_parent=tmp_path,
        materializer=materialize,
    )

    result = await service.validate(
        _context(reproduction_before=reproduction_before)
    )

    assert result.status == expected_status
    assert result.reproduction_after == reproduction_after
    if reproduction_before == "reproduced" and reproduction_after is None:
        assert "not verified after the patch" in result.summary


@pytest.mark.anyio
async def test_validation_app_uses_server_selected_non_fixed_port(
    monkeypatch, tmp_path
):
    from app.investigation_agent import fix_validation as module

    next_bin = tmp_path / "node_modules" / ".bin" / "next"
    next_bin.parent.mkdir(parents=True)
    next_bin.write_text("placeholder")
    captured: dict[str, object] = {}

    class Process:
        pid = 2_000_000_000
        returncode = None

        async def wait(self):
            self.returncode = 0
            return 0

    async def create_process(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    async def ready(url):
        captured["url"] = url
        return True

    class SuccessfulBrowserRunner:
        async def run(self, plan, *, app_url):
            assert app_url == "http://127.0.0.1:46321"
            return BrowserExecutionResult(
                status="not_reproduced",
                completed_actions=1,
                failed_action_index=None,
                expected=None,
                actual=None,
                summary="The prior failure did not reproduce.",
            )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(module, "_wait_for_app", ready)
    service = FixValidationService(
        settings=_settings(enabled=True, repositories="octo-org/checkout"),
        browser_runner=SuccessfulBrowserRunner(),
        port_selector=lambda: 46321,
    )
    plan = BrowserTestPlan.model_validate(
        {"name": "Checkout", "actions": [{"type": "goto", "path": "/checkout"}]}
    )
    checks: list[FixValidationCheck] = []

    result = await service._rerun_browser_when_supported(
        tmp_path, _context(reproduction_plan=plan), checks
    )

    assert result == "not_reproduced"
    assert captured["url"] == "http://127.0.0.1:46321"
    assert captured["command"][-1] == "46321"
    assert captured["kwargs"]["start_new_session"] is True
    assert "4173" not in captured["command"]


def test_available_validation_ports_are_loopback_ephemeral(monkeypatch):
    from app.investigation_agent import fix_validation as module

    ports = iter((46321, 46322))
    bound_addresses: list[tuple[str, int]] = []

    class Candidate:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def bind(self, address):
            bound_addresses.append(address)

        def getsockname(self):
            return ("127.0.0.1", next(ports))

    monkeypatch.setattr(module.socket, "socket", lambda *args: Candidate())

    assert (_available_loopback_port(), _available_loopback_port()) == (46321, 46322)
    assert bound_addresses == [("127.0.0.1", 0), ("127.0.0.1", 0)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("claim_state", "status_code"),
    [
        (FixValidationClaimState.NOT_FOUND, 404),
        (FixValidationClaimState.NO_FIX_PROPOSAL, 400),
        (FixValidationClaimState.CONFLICT, 409),
    ],
)
async def test_fix_validation_route_is_scoped_and_protects_preconditions(
    monkeypatch, claim_state, status_code
):
    from app.investigations import routes

    installation_id = uuid.uuid4()
    captured = {}

    async def connection(request, db):
        return SimpleNamespace(
            installation_id=installation_id, github_installation_id=987654
        )

    async def claim(db, **kwargs):
        captured.update(kwargs)
        return FixValidationClaim(state=claim_state)

    class Db:
        async def rollback(self):
            return None

    monkeypatch.setattr(routes, "_require_connection", connection)
    monkeypatch.setattr(routes, "claim_fix_validation", claim)

    with pytest.raises(HTTPException) as exc_info:
        await routes.validate_investigation_fix(
            uuid.uuid4(), SimpleNamespace(), SimpleNamespace(), Db()
        )

    assert exc_info.value.status_code == status_code
    assert captured["installation_id"] == installation_id
