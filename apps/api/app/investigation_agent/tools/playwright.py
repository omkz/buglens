"""Deterministic rendering and hard-coded execution of the browser-action DSL."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Page, async_playwright, expect

from ..schemas import (
    BrowserAction,
    BrowserExecutionResult,
    BrowserTestPlan,
    ClickAction,
    ExpectTextAction,
    ExpectUrlAction,
    ExpectVisibleAction,
    FillAction,
    GotoAction,
    PressAction,
    WaitForAction,
)


class UnsafeApplicationUrlError(ValueError):
    """Raised when a persisted application URL is not safe for navigation."""


def validated_app_origin(app_url: str) -> str:
    """Return the fixed HTTP(S) origin, rejecting credential and metadata URLs."""
    parsed = urlsplit(app_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not parsed.netloc:
        raise UnsafeApplicationUrlError("Application URL must be HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeApplicationUrlError("Application URL cannot contain credentials.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"metadata.google.internal", "metadata"}:
        raise UnsafeApplicationUrlError("Application URL host is not allowed.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise UnsafeApplicationUrlError("Application URL host is not allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeApplicationUrlError("Application URL port is invalid.") from exc
    normalized_host = hostname.encode("idna").decode("ascii")
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    netloc = normalized_host if port is None or default_port else f"{normalized_host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def render_playwright_source(plan: BrowserTestPlan, *, app_url: str) -> str:
    """Render reviewable source from known actions; the source is never executed."""
    origin = validated_app_origin(app_url)
    lines = [
        "from urllib.parse import urlsplit, urlunsplit",
        "from playwright.sync_api import expect, sync_playwright",
        "",
        f"BASE_URL = {json.dumps(origin)}",
        "",
        "",
        "def _origin(url):",
        "    parsed = urlsplit(url)",
        '    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", ""))',
        "",
        "",
        "def _guard_navigation(route, request):",
        "    if request.is_navigation_request() and _origin(request.url) != BASE_URL:",
        '        route.abort("blockedbyclient")',
        "    else:",
        "        route.continue_()",
        "",
        "",
        "def test_bug_reproduction():",
        "    with sync_playwright() as playwright:",
        "        browser = playwright.chromium.launch(headless=True)",
        "        context = browser.new_context()",
        '        context.route("**/*", _guard_navigation)',
        "        page = context.new_page()",
        f"        page.goto(BASE_URL + {json.dumps(plan.start_path)})",
    ]
    lines.extend(f"        {line}" for action in plan.actions for line in _render_action(action))
    lines.append("        browser.close()")
    lines.append("")
    return "\n".join(lines)


def _render_action(action: BrowserAction) -> list[str]:
    if isinstance(action, GotoAction):
        return [f"page.goto(BASE_URL + {json.dumps(action.path)})"]
    if isinstance(action, ClickAction):
        return [f"page.locator({json.dumps(action.selector)}).click()"]
    if isinstance(action, FillAction):
        return [
            f"page.locator({json.dumps(action.selector)}).fill({json.dumps(action.value)})"
        ]
    if isinstance(action, PressAction):
        return [
            f"page.locator({json.dumps(action.selector)}).press({json.dumps(action.key)})"
        ]
    if isinstance(action, WaitForAction):
        return [f"page.locator({json.dumps(action.selector)}).wait_for()"]
    if isinstance(action, ExpectTextAction):
        return [
            f"expect(page.locator({json.dumps(action.selector)})).to_contain_text({json.dumps(action.value)})"
        ]
    if isinstance(action, ExpectVisibleAction):
        return [f"expect(page.locator({json.dumps(action.selector)})).to_be_visible()"]
    if isinstance(action, ExpectUrlAction):
        return [f"expect(page).to_have_url(BASE_URL + {json.dumps(action.value)})"]
    raise TypeError("Unsupported browser action.")


class PlaywrightPlanRunner:
    """Execute validated actions directly through Playwright, never via exec/eval."""

    def __init__(
        self,
        *,
        action_timeout_ms: int,
        run_timeout_seconds: float,
        playwright_factory: Callable[[], Any] = async_playwright,
    ):
        self.action_timeout_ms = action_timeout_ms
        self.run_timeout_seconds = run_timeout_seconds
        self.playwright_factory = playwright_factory

    async def run(
        self, plan: BrowserTestPlan, *, app_url: str
    ) -> BrowserExecutionResult:
        try:
            origin = validated_app_origin(app_url)
        except UnsafeApplicationUrlError:
            return _blocked(0, None, "The configured application URL is not safe.")

        browser = None
        completed = 0
        meaningful_actions = 0
        try:
            async with asyncio.timeout(self.run_timeout_seconds):
                async with self.playwright_factory() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    browser_context = await browser.new_context()
                    await browser_context.route(
                        "**/*",
                        lambda route, request: _guard_navigation(
                            route, request, origin=origin
                        ),
                    )
                    page = await browser_context.new_page()
                    page.set_default_timeout(self.action_timeout_ms)
                    await page.goto(origin + plan.start_path)
                    for index, action in enumerate(plan.actions):
                        try:
                            await self.execute_action(page, action, origin=origin)
                            completed += 1
                            if isinstance(
                                action,
                                (GotoAction, ClickAction, FillAction, PressAction),
                            ):
                                meaningful_actions += 1
                        except AssertionError:
                            if not isinstance(
                                action,
                                (ExpectTextAction, ExpectVisibleAction, ExpectUrlAction),
                            ) or meaningful_actions == 0:
                                return _blocked(
                                    completed,
                                    index,
                                    "A browser interaction prevented a meaningful determination.",
                                )
                            return BrowserExecutionResult(
                                status="reproduced",
                                completed_actions=completed,
                                failed_action_index=index,
                                expected=_expected_value(action),
                                actual=_bounded(getattr(page, "url", None)),
                                summary="The reported failure was observed at a browser expectation.",
                            )
                        except Exception:
                            return _blocked(
                                completed,
                                index,
                                "Browser setup or an interaction prevented a meaningful determination.",
                            )
                    return BrowserExecutionResult(
                        status="not_reproduced",
                        completed_actions=completed,
                        failed_action_index=None,
                        expected=None,
                        actual=None,
                        summary="All planned browser expectations passed.",
                    )
        except Exception:
            return _blocked(
                completed,
                None,
                "The browser run could not complete in the configured environment.",
            )
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def execute_action(
        self, page: Page, action: BrowserAction, *, origin: str
    ) -> None:
        """Map one schema-validated action to a fixed Playwright API operation."""
        if isinstance(action, GotoAction):
            await page.goto(origin + action.path)
        elif isinstance(action, ClickAction):
            await page.locator(action.selector).click()
        elif isinstance(action, FillAction):
            await page.locator(action.selector).fill(action.value)
        elif isinstance(action, PressAction):
            await page.locator(action.selector).press(action.key)
        elif isinstance(action, WaitForAction):
            await page.locator(action.selector).wait_for()
        elif isinstance(action, ExpectTextAction):
            await expect(page.locator(action.selector)).to_contain_text(action.value)
        elif isinstance(action, ExpectVisibleAction):
            await expect(page.locator(action.selector)).to_be_visible()
        elif isinstance(action, ExpectUrlAction):
            await expect(page).to_have_url(origin + action.value)
        else:
            raise TypeError("Unsupported browser action.")


def _expected_value(action: BrowserAction) -> str | None:
    if isinstance(action, ExpectUrlAction):
        return action.value
    if isinstance(action, ExpectTextAction):
        return action.value
    if isinstance(action, ExpectVisibleAction):
        return f"visible: {action.selector}"
    return None


async def _guard_navigation(route: Any, request: Any, *, origin: str) -> None:
    if request.is_navigation_request() and _origin(request.url) != origin:
        await route.abort("blockedbyclient")
        return
    await route.continue_()


def _origin(url: str) -> str:
    try:
        return validated_app_origin(url)
    except UnsafeApplicationUrlError:
        return ""


def _bounded(value: object) -> str | None:
    if value is None:
        return None
    return str(value)[:2_000]


def _blocked(
    completed_actions: int, failed_action_index: int | None, summary: str
) -> BrowserExecutionResult:
    return BrowserExecutionResult(
        status="blocked",
        completed_actions=completed_actions,
        failed_action_index=failed_action_index,
        expected=None,
        actual=None,
        summary=summary,
    )
