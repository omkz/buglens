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
    _run_command,
)
from app.investigation_agent.repository import (
    FixValidationClaim,
    FixValidationClaimState,
)
from app.investigation_agent.schemas import FixProposal


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
        settings=SimpleNamespace(),
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
        settings=SimpleNamespace(), browser_runner=BrowserRunner(),
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
        settings=SimpleNamespace(), browser_runner=BrowserRunner(),
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
        settings=SimpleNamespace(), browser_runner=BrowserRunner(),
        workspace_parent=tmp_path, materializer=materialize, command_runner=fail,
    )

    result = await service.validate(_context())

    assert result.status == "validation_failed"
    assert result.checks[0].output == "bounded failure"


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
