from __future__ import annotations

import asyncio
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.investigation_agent.fix_validation import (
    FixValidationCheck,
    FixValidationContext,
    FixValidationResult,
    FixValidationService,
    _browser_execution_check,
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


def _context(proposal: FixProposal | None = None) -> FixValidationContext:
    return FixValidationContext(
        github_installation_id=987654,
        repository_full_name="octo-org/checkout",
        default_branch="main",
        fix_proposal=proposal or _proposal(),
        reproduction_plan=None,
        reproduction_before="reproduced",
    )


def _browser_plan() -> BrowserTestPlan:
    return BrowserTestPlan.model_validate(
        {
            "name": "Checkout navigation",
            "actions": [{"type": "expect_url", "value": "/checkout"}],
        }
    )


class BrowserRunner:
    async def run(self, *args, **kwargs):
        raise AssertionError("Browser validation should not run without a supported app.")


@pytest.mark.parametrize(
    ("browser_status", "check_status"),
    [
        ("blocked", "blocked"),
        ("reproduced", "failed"),
        ("not_reproduced", "passed"),
    ],
)
def test_browser_execution_check_preserves_status_semantics(
    browser_status, check_status
):
    execution = BrowserExecutionResult(
        status=browser_status,
        completed_actions=1,
        failed_action_index=None,
        expected=None,
        actual=None,
        summary="Bounded browser result.",
    )

    check = _browser_execution_check(execution)

    assert check.status == check_status


def _settings(
    *, host_execution: bool = True, network_installs: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        fix_validation_allow_host_execution=host_execution,
        fix_validation_allow_network_installs=network_installs,
    )


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
        settings=_settings(),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=check,
    )
    # Make one deterministic Python check available.
    (source / "tests").mkdir()

    result = await service.validate(_context())

    assert result.status == "validated"
    assert observed["updated"] == "new\n"
    assert (source / "src" / "checkout.ts").read_text() == "old\n"
    assert not Path(observed["checkout"]).exists()
    assert list(tmp_path.glob("buglensa-fix-*")) == []


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
async def test_host_execution_disabled_blocks_runtime_validation(
    monkeypatch, tmp_path
):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def must_not_execute(command, cwd, timeout):
        raise AssertionError("Repository code must not run when host execution is off.")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(host_execution=False),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=must_not_execute,
    )

    result = await service.validate(_context())

    assert result.status == "blocked"
    assert result.summary == "Runtime fix validation is disabled in this environment."
    assert result.checks == []


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
        settings=_settings(), browser_runner=BrowserRunner(),
        workspace_parent=tmp_path, materializer=materialize, command_runner=fail,
    )

    result = await service.validate(_context())

    assert result.status == "validation_failed"
    assert result.checks[0].output == "bounded failure"


@pytest.mark.anyio
async def test_dependency_install_failure_reports_blocked(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "package.json").write_text("{}\n")
        (checkout / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    async def unavailable(command, cwd, timeout):
        assert command == [
            "pnpm",
            "install",
            "--frozen-lockfile",
            "--ignore-scripts",
            "--offline",
        ]
        return FixValidationCheck(
            name="pnpm",
            status="failed",
            output="Package-manager metadata is unavailable offline.",
        )

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    monkeypatch.setattr(module.shutil, "which", lambda command: f"/usr/bin/{command}")
    service = FixValidationService(
        settings=_settings(),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=unavailable,
    )

    result = await service.validate(_context())

    assert result.status == "blocked"
    assert result.checks[0].name == "Install dependencies without lifecycle scripts"
    assert result.checks[0].status == "failed"


@pytest.mark.anyio
async def test_network_enabled_install_omits_offline_flag(monkeypatch, tmp_path):
    from app.investigation_agent import fix_validation as module

    captured: list[str] = []

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "package.json").write_text("{}\n")
        (checkout / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    async def install(command, cwd, timeout):
        captured.extend(command)
        return FixValidationCheck(name="pnpm", status="passed", output="ok")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    monkeypatch.setattr(module.shutil, "which", lambda command: f"/usr/bin/{command}")
    service = FixValidationService(
        settings=_settings(network_installs=True),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=install,
    )

    await service.validate(_context())

    assert captured == [
        "pnpm",
        "install",
        "--frozen-lockfile",
        "--ignore-scripts",
    ]


@pytest.mark.anyio
async def test_successful_install_then_failing_typecheck_reports_validation_failed(
    monkeypatch, tmp_path
):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "package.json").write_text("{}\n")
        (checkout / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        (checkout / "tsconfig.json").write_text("{}\n")
        bin_dir = checkout / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "tsc").write_text("")

    async def run_check(command, cwd, timeout):
        if command[0] == "pnpm":
            return FixValidationCheck(name="pnpm", status="passed", output="ok")
        return FixValidationCheck(
            name="tsc", status="failed", output="TypeScript validation failed."
        )

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    monkeypatch.setattr(module.shutil, "which", lambda command: f"/usr/bin/{command}")
    service = FixValidationService(
        settings=_settings(),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=run_check,
    )

    result = await service.validate(_context())

    assert result.status == "validation_failed"
    assert [check.status for check in result.checks] == ["passed", "failed"]
    assert result.checks[1].name == "TypeScript typecheck"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("browser_status", "check_status", "result_status"),
    [
        ("blocked", "blocked", "blocked"),
        ("reproduced", "failed", "validation_failed"),
        ("not_reproduced", "passed", "validated"),
    ],
)
async def test_browser_result_preserves_overall_validation_semantics(
    monkeypatch,
    tmp_path,
    browser_status,
    check_status,
    result_status,
):
    from app.investigation_agent import fix_validation as module

    replay_calls = 0

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def passing_check(command, cwd, timeout):
        return FixValidationCheck(name="Pytest", status="passed", output="ok")

    async def browser_result(checkout, context, checks):
        nonlocal replay_calls
        replay_calls += 1
        checks.append(
            FixValidationCheck(
                name="Browser reproduction",
                status=check_status,
                output="Bounded browser result.",
            )
        )
        return browser_status

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=passing_check,
    )
    monkeypatch.setattr(service, "_rerun_browser_when_supported", browser_result)

    context = _context().model_copy(
        update={"reproduction_plan": _browser_plan()}
    )
    result = await service.validate(context)

    assert result.status == result_status
    assert result.reproduction_after == browser_status
    assert result.checks[-1].status == check_status
    assert replay_calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("reproduction_before", ["blocked", "not_reproduced", None])
async def test_browser_replay_is_skipped_without_reproduced_baseline(
    monkeypatch,
    tmp_path,
    reproduction_before,
):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def passing_check(command, cwd, timeout):
        return FixValidationCheck(name="Pytest", status="passed", output="ok")

    async def must_not_replay(checkout, context, checks):
        raise AssertionError("Browser replay requires a reproduced baseline.")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=passing_check,
    )
    monkeypatch.setattr(
        service,
        "_rerun_browser_when_supported",
        must_not_replay,
    )
    context = _context().model_copy(
        update={
            "reproduction_before": reproduction_before,
            "reproduction_plan": _browser_plan(),
        }
    )

    result = await service.validate(context)

    assert result.status == "validated"
    assert result.reproduction_after is None
    assert result.summary == (
        "All available bounded validation checks passed; browser fix verification "
        "was skipped because the original bug was not reproduced."
    )
    assert all(check.name != "Browser reproduction" for check in result.checks)


@pytest.mark.anyio
async def test_skipped_browser_replay_keeps_bounded_check_failure(
    monkeypatch,
    tmp_path,
):
    from app.investigation_agent import fix_validation as module

    async def token(**kwargs):
        return "token"

    async def materialize(value, context, checkout):
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "checkout.ts").write_text("old\n")
        (checkout / "tests").mkdir()

    async def failing_check(command, cwd, timeout):
        return FixValidationCheck(
            name="Pytest",
            status="failed",
            output="Bounded test failure.",
        )

    async def must_not_replay(checkout, context, checks):
        raise AssertionError("Browser replay requires a reproduced baseline.")

    monkeypatch.setattr(module, "create_scoped_installation_token", token)
    service = FixValidationService(
        settings=_settings(),
        browser_runner=BrowserRunner(),
        workspace_parent=tmp_path,
        materializer=materialize,
        command_runner=failing_check,
    )
    monkeypatch.setattr(
        service,
        "_rerun_browser_when_supported",
        must_not_replay,
    )
    context = _context().model_copy(
        update={
            "reproduction_before": "blocked",
            "reproduction_plan": _browser_plan(),
        }
    )

    result = await service.validate(context)

    assert result.status == "validation_failed"
    assert result.reproduction_after is None
    assert result.checks[-1].status == "failed"


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


@pytest.mark.anyio
async def test_unexpected_post_claim_failure_persists_terminal_blocked_result(
    monkeypatch,
):
    from app.investigations import routes

    investigation_id = uuid.uuid4()
    installation_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def connection(request, db):
        return SimpleNamespace(
            installation_id=installation_id,
            github_installation_id=987654,
        )

    async def claim(db, **kwargs):
        return FixValidationClaim(
            state=FixValidationClaimState.READY,
            context=_context(),
        )

    async def complete(db, **kwargs):
        captured["persisted_result"] = kwargs["result"]
        return SimpleNamespace()

    class ValidationService:
        async def validate(self, context):
            raise RuntimeError("unexpected local validation failure")

    class Db:
        commits = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            return None

    db = Db()
    monkeypatch.setattr(routes, "_require_connection", connection)
    monkeypatch.setattr(routes, "claim_fix_validation", claim)
    monkeypatch.setattr(routes, "complete_fix_validation", complete)
    monkeypatch.setattr(
        routes,
        "_agent_run_response",
        lambda investigation_id, run: captured["persisted_result"],
    )

    response = await routes.validate_investigation_fix(
        investigation_id,
        SimpleNamespace(),
        ValidationService(),
        db,
    )

    assert db.commits == 2
    assert isinstance(response, FixValidationResult)
    assert response.status == "blocked"
    assert response.summary == "Fix validation could not complete safely."
    assert response.reproduction_before == "reproduced"
    assert captured["persisted_result"] == response
