"""Apply and validate a persisted fix proposal in a disposable workspace."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.integrations.github.access import create_scoped_installation_token

from .fixes import is_forbidden_fix_path
from .schemas import BrowserTestPlan, FixProposal
from .tools.playwright import PlaywrightPlanRunner

_MAX_OUTPUT = 20_000
_COMMAND_TIMEOUT = 120
_APP_START_TIMEOUT = 30
_DEPENDENCY_INSTALL_CHECK = "Install dependencies without lifecycle scripts"


class FixValidationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    status: Literal["passed", "failed", "timed_out", "blocked"]
    output: str = Field(max_length=_MAX_OUTPUT)


class FixValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "running", "validated", "validation_failed", "stale_proposal", "blocked", "not_run"
    ]
    summary: str = Field(min_length=1, max_length=2_000)
    checks: list[FixValidationCheck] = Field(max_length=10)
    reproduction_before: Literal["reproduced", "not_reproduced", "blocked"] | None
    reproduction_after: Literal["reproduced", "not_reproduced", "blocked"] | None


class FixValidationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    github_installation_id: int
    repository_full_name: str
    default_branch: str
    fix_proposal: FixProposal
    reproduction_plan: BrowserTestPlan | None
    reproduction_before: Literal["reproduced", "not_reproduced", "blocked"] | None


CommandRunner = Callable[[list[str], Path, int], Awaitable[FixValidationCheck]]
Materializer = Callable[[str, FixValidationContext, Path], Awaitable[None]]


class FixValidationService:
    def __init__(
        self,
        *,
        settings: Settings,
        browser_runner: PlaywrightPlanRunner,
        workspace_parent: Path | None = None,
        command_runner: CommandRunner | None = None,
        materializer: Materializer | None = None,
    ):
        self.settings = settings
        self.browser_runner = browser_runner
        self.workspace_parent = workspace_parent
        self.command_runner = command_runner or _run_command
        self.materializer = materializer or _clone_repository

    async def validate(self, context: FixValidationContext) -> FixValidationResult:
        workspace_root = Path(
            tempfile.mkdtemp(prefix="buglensa-fix-", dir=self.workspace_parent)
        )
        checkout = workspace_root / "checkout"
        checks: list[FixValidationCheck] = []
        try:
            token = await create_scoped_installation_token(
                settings=self.settings,
                github_installation_id=context.github_installation_id,
            )
            try:
                await self.materializer(token, context, checkout)
            except Exception:
                return _result(
                    "blocked", "The isolated repository checkout could not be prepared.", checks, context
                )
            finally:
                del token

            apply_result = _apply_proposal(checkout, context.fix_proposal)
            if apply_result is not None:
                checks.append(apply_result)
                status = (
                    "stale_proposal"
                    if apply_result.output == "The proposal baseline is stale."
                    else "blocked"
                )
                return _result(status, apply_result.output, checks, context)

            if not self.settings.fix_validation_allow_host_execution:
                return _result(
                    "blocked",
                    "Runtime fix validation is disabled in this environment.",
                    checks,
                    context,
                )

            checks.extend(await self._run_known_checks(checkout))
            dependency_install = next(
                (
                    check
                    for check in checks
                    if check.name == _DEPENDENCY_INSTALL_CHECK
                ),
                None,
            )
            if dependency_install is not None and dependency_install.status != "passed":
                return _result(
                    "blocked",
                    "Dependencies could not be prepared in the isolated validation environment.",
                    checks,
                    context,
                )
            if any(check.status in {"failed", "timed_out"} for check in checks):
                return _result(
                    "validation_failed", "One or more bounded validation checks failed.", checks, context
                )

            after = await self._rerun_browser_when_supported(checkout, context, checks)
            if after == "not_reproduced" and context.reproduction_before == "reproduced":
                return _result(
                    "validated",
                    "The bounded checks passed and the prior browser failure was not reproduced.",
                    checks,
                    context,
                    after,
                )
            if after == "reproduced":
                return _result(
                    "validation_failed", "The browser reproduction still reproduces the bug.", checks, context, after
                )
            if checks and all(check.status == "passed" for check in checks):
                return _result(
                    "validated",
                    "All available bounded validation checks passed; browser reproduction was unavailable.",
                    checks,
                    context,
                    after,
                )
            return _result(
                "not_run", "No supported bounded validation checks were available.", checks, context, after
            )
        except Exception:
            return _result(
                "blocked",
                "Fix validation could not complete safely in the isolated workspace.",
                checks,
                context,
            )
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    async def _run_known_checks(self, checkout: Path) -> list[FixValidationCheck]:
        checks: list[FixValidationCheck] = []
        package_json = checkout / "package.json"
        if package_json.is_file():
            manager: list[str] | None = None
            if (checkout / "pnpm-lock.yaml").is_file() and shutil.which("pnpm"):
                manager = ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]
            elif (checkout / "package-lock.json").is_file() and shutil.which("npm"):
                manager = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
            if manager is not None:
                if not self.settings.fix_validation_allow_network_installs:
                    manager.append("--offline")
                install = await self.command_runner(manager, checkout, _COMMAND_TIMEOUT)
                install.name = _DEPENDENCY_INSTALL_CHECK
                checks.append(install)
                if install.status != "passed":
                    return checks
            bin_dir = checkout / "node_modules" / ".bin"
            if (checkout / "tsconfig.json").is_file() and (bin_dir / "tsc").is_file():
                checks.append(await self.command_runner([str(bin_dir / "tsc"), "--noEmit"], checkout, _COMMAND_TIMEOUT))
                checks[-1].name = "TypeScript typecheck"
            if any(checkout.glob("eslint.config.*")) and (bin_dir / "eslint").is_file():
                checks.append(await self.command_runner([str(bin_dir / "eslint"), "."], checkout, _COMMAND_TIMEOUT))
                checks[-1].name = "ESLint"
        elif _python_tests_configured(checkout):
            check = await self.command_runner([sys.executable, "-m", "pytest", "-q"], checkout, _COMMAND_TIMEOUT)
            check.name = "Pytest"
            checks.append(check)
        return checks

    async def _rerun_browser_when_supported(
        self,
        checkout: Path,
        context: FixValidationContext,
        checks: list[FixValidationCheck],
    ) -> str | None:
        plan = context.reproduction_plan
        next_bin = checkout / "node_modules" / ".bin" / "next"
        if plan is None or not next_bin.is_file():
            return None
        port = 4173
        process = await asyncio.create_subprocess_exec(
            str(next_bin), "dev", "--hostname", "127.0.0.1", "--port", str(port),
            cwd=checkout, env=_safe_environment(checkout),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            ready = await _wait_for_app(f"http://127.0.0.1:{port}")
            if not ready:
                checks.append(FixValidationCheck(name="Start isolated application", status="blocked", output="The application did not become ready within the bounded startup window."))
                return None
            checks.append(FixValidationCheck(name="Start isolated application", status="passed", output="The isolated application became ready."))
            execution = await self.browser_runner.run(plan, app_url=f"http://127.0.0.1:{port}")
            checks.append(FixValidationCheck(name="Browser reproduction", status="passed" if execution.status == "not_reproduced" else "failed", output=execution.summary[:_MAX_OUTPUT]))
            return execution.status
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()


def _apply_proposal(checkout: Path, proposal: FixProposal) -> FixValidationCheck | None:
    root = checkout.resolve()
    resolved: list[tuple[Path, str]] = []
    for change in proposal.files:
        if is_forbidden_fix_path(change.path):
            return FixValidationCheck(name="Apply proposal", status="blocked", output="The proposal contains an unsafe path.")
        target = (checkout / change.path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return FixValidationCheck(name="Apply proposal", status="blocked", output="A proposed file is outside the isolated checkout or missing.")
        try:
            current = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return FixValidationCheck(name="Apply proposal", status="blocked", output="A proposed file is not readable UTF-8 text.")
        if current != change.original_content:
            return FixValidationCheck(name="Apply proposal", status="failed", output="The proposal baseline is stale.")
        resolved.append((target, change.updated_content))
    for target, content in resolved:
        target.write_text(content, encoding="utf-8")
    return None


async def _clone_repository(token: str, context: FixValidationContext, checkout: Path) -> None:
    owner, separator, repository = context.repository_full_name.partition("/")
    if not separator or not owner or not repository or "/" in repository:
        raise ValueError("Invalid persisted repository.")
    checkout.parent.mkdir(parents=True, exist_ok=True)
    askpass = checkout.parent / "askpass.py"
    askpass.write_text(
        "#!/usr/bin/env python3\n"
        "import os,sys\n"
        "print('x-access-token' if 'Username' in sys.argv[1] "
        "else os.environ['BUGLENSA_GITHUB_TOKEN'])\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    env = _safe_environment(checkout.parent)
    env.update(
        {
            "GIT_ASKPASS": str(askpass),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "BUGLENSA_GITHUB_TOKEN": token,
        }
    )
    process = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--branch",
        context.default_branch, "--", f"https://github.com/{owner}/{repository}.git", str(checkout),
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_COMMAND_TIMEOUT)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Clone timed out.") from None
    finally:
        askpass.unlink(missing_ok=True)
    if process.returncode != 0:
        raise RuntimeError("Clone failed.")
    del stdout, stderr


async def _run_command(command: list[str], cwd: Path, timeout: int) -> FixValidationCheck:
    name = Path(command[0]).name
    process = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, env=_safe_environment(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return FixValidationCheck(name=name, status="timed_out", output=_bounded_output(stdout, stderr))
    return FixValidationCheck(name=name, status="passed" if process.returncode == 0 else "failed", output=_bounded_output(stdout, stderr))


def _safe_environment(workspace: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(workspace), "CI": "1", "NO_COLOR": "1", "LANG": "C.UTF-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def _bounded_output(stdout: bytes, stderr: bytes) -> str:
    return (stdout + stderr).decode("utf-8", errors="replace")[-_MAX_OUTPUT:]


def _python_tests_configured(checkout: Path) -> bool:
    return (checkout / "pytest.ini").is_file() or (checkout / "tests").is_dir()


async def _wait_for_app(url: str) -> bool:
    deadline = asyncio.get_running_loop().time() + _APP_START_TIMEOUT
    async with httpx.AsyncClient(timeout=1) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                await client.get(url)
                return True
            except httpx.HTTPError:
                await asyncio.sleep(0.25)
    return False


def _result(status: str, summary: str, checks: list[FixValidationCheck], context: FixValidationContext, after: str | None = None) -> FixValidationResult:
    if status == "stale_proposal":
        summary = "The repository file no longer matches the persisted proposal baseline."
    return FixValidationResult(status=status, summary=summary, checks=checks, reproduction_before=context.reproduction_before, reproduction_after=after)
