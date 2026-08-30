"""Apply and validate a persisted fix proposal in a disposable workspace."""

from __future__ import annotations

import asyncio
import os
import signal
import shutil
import socket
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
PortSelector = Callable[[], int]


class FixValidationService:
    def __init__(
        self,
        *,
        settings: Settings,
        browser_runner: PlaywrightPlanRunner,
        workspace_parent: Path | None = None,
        command_runner: CommandRunner | None = None,
        materializer: Materializer | None = None,
        port_selector: PortSelector | None = None,
    ):
        self.settings = settings
        self.browser_runner = browser_runner
        self.workspace_parent = workspace_parent
        self.command_runner = command_runner or _run_command
        self.materializer = materializer or _clone_repository
        self.port_selector = port_selector or _available_loopback_port

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
                    "blocked",
                    "The isolated repository checkout could not be prepared.",
                    checks,
                    context,
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

            if not self._host_execution_is_allowed(context.repository_full_name):
                checks.append(
                    FixValidationCheck(
                        name="Executable validation gate",
                        status="blocked",
                        output=(
                            "Executable validation requires host execution to be enabled "
                            "and this development repository to be explicitly trusted."
                        ),
                    )
                )
                return _result(
                    "blocked",
                    "Executable validation is disabled for this repository.",
                    checks,
                    context,
                )

            checks.extend(await self._run_known_checks(checkout))
            if any(check.status in {"failed", "timed_out"} for check in checks):
                return _result(
                    "validation_failed",
                    "One or more bounded validation checks failed.",
                    checks,
                    context,
                )
            if any(check.status == "blocked" for check in checks):
                return _result(
                    "blocked",
                    "A bounded validation check could not run safely.",
                    checks,
                    context,
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
                    "validation_failed",
                    "The browser reproduction still reproduces the bug.",
                    checks,
                    context,
                    after,
                )
            if after == "blocked":
                return _result(
                    "blocked",
                    "Browser verification could not run safely.",
                    checks,
                    context,
                    after,
                )
            if context.reproduction_before == "reproduced":
                return _result(
                    "not_run",
                    "Checks passed, but the original browser failure was not verified after the patch.",
                    checks,
                    context,
                    after,
                )
            if checks and all(check.status == "passed" for check in checks):
                return _result(
                    "validated",
                    "All available bounded validation checks passed.",
                    checks,
                    context,
                    after,
                )
            return _result(
                "not_run",
                "No supported bounded validation checks were available.",
                checks,
                context,
                after,
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

    def _host_execution_is_allowed(self, repository_full_name: str) -> bool:
        if not getattr(self.settings, "fix_validation_allow_host_execution", False):
            return False
        trusted = getattr(self.settings, "trusted_fix_validation_repositories", None)
        if trusted is None:
            configured = getattr(
                self.settings, "fix_validation_trusted_repositories", ""
            )
            trusted = {
                item.strip() for item in configured.split(",") if item.strip()
            }
        return repository_full_name in trusted

    async def _run_known_checks(self, checkout: Path) -> list[FixValidationCheck]:
        checks: list[FixValidationCheck] = []
        package_json = checkout / "package.json"
        if package_json.is_file():
            manager: list[str] | None = None
            if (checkout / "pnpm-lock.yaml").is_file() and shutil.which("pnpm"):
                manager = [
                    "pnpm",
                    "install",
                    "--frozen-lockfile",
                    "--ignore-scripts",
                    "--offline",
                ]
            elif (checkout / "package-lock.json").is_file() and shutil.which("npm"):
                manager = [
                    "npm",
                    "ci",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    "--offline",
                ]
            if manager is not None:
                install = await self.command_runner(manager, checkout, _COMMAND_TIMEOUT)
                install.name = "Install dependencies without lifecycle scripts"
                checks.append(install)
                if install.status != "passed":
                    return checks
            bin_dir = checkout / "node_modules" / ".bin"
            if (checkout / "tsconfig.json").is_file() and (bin_dir / "tsc").is_file():
                checks.append(
                    await self.command_runner(
                        [str(bin_dir / "tsc"), "--noEmit"],
                        checkout,
                        _COMMAND_TIMEOUT,
                    )
                )
                checks[-1].name = "TypeScript typecheck"
            if any(checkout.glob("eslint.config.*")) and (bin_dir / "eslint").is_file():
                checks.append(
                    await self.command_runner(
                        [str(bin_dir / "eslint"), "."], checkout, _COMMAND_TIMEOUT
                    )
                )
                checks[-1].name = "ESLint"
        elif _python_tests_configured(checkout):
            check = await self.command_runner(
                [sys.executable, "-m", "pytest", "-q"],
                checkout,
                _COMMAND_TIMEOUT,
            )
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
        port = self.port_selector()
        process = await asyncio.create_subprocess_exec(
            str(next_bin),
            "dev",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=checkout,
            env=_safe_environment(checkout),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            ready = await _wait_for_app(f"http://127.0.0.1:{port}")
            if not ready:
                checks.append(
                    FixValidationCheck(
                        name="Start isolated application",
                        status="blocked",
                        output=(
                            "The application did not become ready within the "
                            "bounded startup window."
                        ),
                    )
                )
                return None
            checks.append(
                FixValidationCheck(
                    name="Start isolated application",
                    status="passed",
                    output="The isolated application became ready.",
                )
            )
            execution = await self.browser_runner.run(
                plan, app_url=f"http://127.0.0.1:{port}"
            )
            checks.append(
                FixValidationCheck(
                    name="Browser reproduction",
                    status=(
                        "passed"
                        if execution.status == "not_reproduced"
                        else "failed"
                    ),
                    output=execution.summary[:_MAX_OUTPUT],
                )
            )
            return execution.status
        finally:
            await _terminate_process_group(process)


def _apply_proposal(checkout: Path, proposal: FixProposal) -> FixValidationCheck | None:
    root = checkout.resolve()
    resolved: list[tuple[Path, str]] = []
    for change in proposal.files:
        if is_forbidden_fix_path(change.path):
            return FixValidationCheck(
                name="Apply proposal",
                status="blocked",
                output="The proposal contains an unsafe path.",
            )
        target = (checkout / change.path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return FixValidationCheck(
                name="Apply proposal",
                status="blocked",
                output="A proposed file is outside the isolated checkout or missing.",
            )
        try:
            current = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return FixValidationCheck(
                name="Apply proposal",
                status="blocked",
                output="A proposed file is not readable UTF-8 text.",
            )
        if current != change.original_content:
            return FixValidationCheck(
                name="Apply proposal",
                status="failed",
                output="The proposal baseline is stale.",
            )
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
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--branch",
        context.default_branch,
        "--",
        f"https://github.com/{owner}/{repository}.git",
        str(checkout),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=_COMMAND_TIMEOUT)
    except TimeoutError:
        await _terminate_process_group(process)
        raise RuntimeError("Clone timed out.") from None
    finally:
        askpass.unlink(missing_ok=True)
    if process.returncode != 0:
        raise RuntimeError("Clone failed.")


async def _run_command(command: list[str], cwd: Path, timeout: int) -> FixValidationCheck:
    name = Path(command[0]).name
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=_safe_environment(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    output = bytearray()
    drainers = [
        asyncio.create_task(_drain_bounded(process.stdout, output)),
        asyncio.create_task(_drain_bounded(process.stderr, output)),
    ]
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        await _terminate_process_group(process)
    finally:
        # A command can exit while leaving children holding the pipes open.
        # Stop any remaining members of its isolated process group before draining.
        await _terminate_process_group(process)
        try:
            await asyncio.wait_for(asyncio.gather(*drainers), timeout=2)
        except TimeoutError:
            _signal_process_group(process.pid, signal.SIGKILL)
            for drainer in drainers:
                drainer.cancel()
            await asyncio.gather(*drainers, return_exceptions=True)
    status = (
        "timed_out"
        if timed_out
        else ("passed" if process.returncode == 0 else "failed")
    )
    return FixValidationCheck(
        name=name,
        status=status,
        output=output.decode("utf-8", errors="replace"),
    )


def _safe_environment(workspace: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(workspace),
        "CI": "1",
        "NO_COLOR": "1",
        "LANG": "C.UTF-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


async def _drain_bounded(
    stream: asyncio.StreamReader | None, output: bytearray
) -> None:
    if stream is None:
        return
    while chunk := await stream.read(8_192):
        if len(chunk) >= _MAX_OUTPUT:
            output[:] = chunk[-_MAX_OUTPUT:]
            continue
        overflow = len(output) + len(chunk) - _MAX_OUTPUT
        if overflow > 0:
            del output[:overflow]
        output.extend(chunk)


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    _signal_process_group(process.pid, signal.SIGTERM)
    deadline = asyncio.get_running_loop().time() + 2
    while (
        _process_group_exists(process.pid)
        and asyncio.get_running_loop().time() < deadline
    ):
        await asyncio.sleep(0.05)
    if _process_group_exists(process.pid):
        _signal_process_group(process.pid, signal.SIGKILL)
    if process.returncode is None:
        await process.wait()


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


def _result(
    status: str,
    summary: str,
    checks: list[FixValidationCheck],
    context: FixValidationContext,
    after: str | None = None,
) -> FixValidationResult:
    if status == "stale_proposal":
        summary = "The repository file no longer matches the persisted proposal baseline."
    return FixValidationResult(
        status=status,
        summary=summary,
        checks=checks,
        reproduction_before=context.reproduction_before,
        reproduction_after=after,
    )
