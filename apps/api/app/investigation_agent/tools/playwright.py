"""Deterministic rendering and hard-coded execution of the browser-action DSL."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog
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

logger = structlog.get_logger(__name__)


class UnsafeApplicationUrlError(ValueError):
    """Raised when a persisted application URL is not safe for navigation."""


def validated_app_origin(
    app_url: str, *, allow_private_network: bool = False
) -> str:
    """Normalize an HTTP(S) origin and reject unsafe literal addresses."""
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
    if (
        address is not None
        and not allow_private_network
        and _is_non_public_address(address)
    ):
        raise UnsafeApplicationUrlError("Application URL host is not allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeApplicationUrlError("Application URL port is invalid.") from exc
    try:
        normalized_host = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeApplicationUrlError("Application URL host is invalid.") from exc
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    netloc = normalized_host if port is None or default_port else f"{normalized_host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve_hostname(hostname: str, port: int) -> Sequence[str]:
    results = await asyncio.to_thread(
        socket.getaddrinfo,
        hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    return tuple(result[4][0] for result in results)


async def validate_public_application_origin(
    app_url: str,
    *,
    allow_private_network: bool = False,
    resolver: AddressResolver = _resolve_hostname,
    dns_timeout_seconds: float = 5.0,
) -> str:
    """Validate syntax, then fail closed unless every resolved address is allowed.

    Resolution happens immediately before browser launch. The request guard also
    locks Chromium to the exact hostname/origin, but it cannot pin Chromium's DNS
    result without introducing a browser-level proxy or network sandbox.
    """
    origin = validated_app_origin(
        app_url, allow_private_network=allow_private_network
    )
    parsed = urlsplit(origin)
    hostname = parsed.hostname
    if hostname is None:
        raise UnsafeApplicationUrlError("Application URL host is invalid.")

    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        return origin

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved = await asyncio.wait_for(
            resolver(hostname, port), timeout=dns_timeout_seconds
        )
        addresses = tuple(ipaddress.ip_address(value) for value in resolved)
    except Exception as exc:
        raise UnsafeApplicationUrlError(
            "Application URL host could not be safely resolved."
        ) from exc
    if not addresses:
        raise UnsafeApplicationUrlError(
            "Application URL host could not be safely resolved."
        )
    if not allow_private_network and any(
        _is_non_public_address(address) for address in addresses
    ):
        raise UnsafeApplicationUrlError("Application URL host is not allowed.")
    return origin


def _is_non_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def render_playwright_source(
    plan: BrowserTestPlan,
    *,
    app_url: str,
    allow_private_network: bool = False,
) -> str:
    """Render reviewable source from known actions; the source is never executed."""
    origin = validated_app_origin(app_url, allow_private_network=allow_private_network)
    lines = [
        "from urllib.parse import urlsplit, urlunsplit",
        "from playwright.sync_api import expect, sync_playwright",
        "",
        f"BASE_URL = {json.dumps(origin)}",
        "",
        "",
        "def _origin(url):",
        "    parsed = urlsplit(url)",
        '    if parsed.scheme not in ("http", "https") or not parsed.hostname:',
        '        return ""',
        "    if parsed.username is not None or parsed.password is not None:",
        '        return ""',
        "    try:",
        "        port = parsed.port",
        "    except ValueError:",
        '        return ""',
        '    hostname = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")',
        '    if ":" in hostname:',
        '        hostname = f"[{hostname}]"',
        '    default_port = (parsed.scheme == "http" and port == 80) or (',
        '        parsed.scheme == "https" and port == 443',
        "    )",
        '    netloc = hostname if port is None or default_port else f"{hostname}:{port}"',
        '    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))',
        "",
        "",
        "def _guard_request(route, request):",
        "    if _origin(request.url) != BASE_URL:",
        '        route.abort("blockedbyclient")',
        "    else:",
        "        route.continue_()",
        "",
        "",
        "def test_bug_reproduction():",
        "    with sync_playwright() as playwright:",
        "        browser = playwright.chromium.launch(headless=True)",
        "        context = browser.new_context(",
        '            service_workers="block",',
        "        )",
        '        context.route("**/*", _guard_request)',
        '        context.route_web_socket("**/*", lambda ws: ws.close())',
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
        allow_private_network: bool = False,
        playwright_factory: Callable[[], Any] = async_playwright,
        resolver: AddressResolver = _resolve_hostname,
    ):
        self.action_timeout_ms = action_timeout_ms
        self.run_timeout_seconds = run_timeout_seconds
        self.allow_private_network = allow_private_network
        self.playwright_factory = playwright_factory
        self.resolver = resolver

    async def run(
        self, plan: BrowserTestPlan, *, app_url: str
    ) -> BrowserExecutionResult:
        try:
            origin = await validate_public_application_origin(
                app_url,
                allow_private_network=self.allow_private_network,
                resolver=self.resolver,
            )
        except UnsafeApplicationUrlError as exc:
            _log_browser_failure("validate_origin", exc)
            return _blocked(0, None, "The configured application URL is not safe.")

        browser = None
        completed = 0
        meaningful_actions = 0
        failure_stage = "launch_browser"
        try:
            async with asyncio.timeout(self.run_timeout_seconds):
                async with self.playwright_factory() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    failure_stage = "create_context"
                    browser_context = await browser.new_context(
                        service_workers="block"
                    )
                    await browser_context.route(
                        "**/*",
                        lambda route, request: _guard_request(
                            route, request, origin=origin
                        ),
                    )
                    await browser_context.route_web_socket("**/*", _block_web_socket)
                    page = await browser_context.new_page()
                    page.set_default_timeout(self.action_timeout_ms)
                    failure_stage = "initial_navigation"
                    await page.goto(origin + plan.start_path)
                    for index, action in enumerate(plan.actions):
                        failure_stage = "execute_action"
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
                        except Exception as exc:
                            _log_browser_failure(
                                "execute_action",
                                exc,
                                action_index=index,
                            )
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
        except TimeoutError as exc:
            _log_browser_failure("run_timeout", exc)
            return _blocked(
                completed,
                None,
                "The browser run timed out in the configured environment.",
            )
        except Exception as exc:
            _log_browser_failure(failure_stage, exc)
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


async def _guard_request(route: Any, request: Any, *, origin: str) -> None:
    if _origin(request.url) != origin:
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def _block_web_socket(web_socket: Any) -> None:
    await web_socket.close()


def _origin(url: str) -> str:
    try:
        return validated_app_origin(url, allow_private_network=True)
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


def _log_browser_failure(
    failure_stage: str,
    exc: BaseException,
    *,
    action_index: int | None = None,
) -> None:
    fields: dict[str, object] = {
        "failure_stage": failure_stage,
        "exception_type": type(exc).__name__,
        "exc_info": True,
        "safe_exc_info": True,
    }
    if action_index is not None:
        fields["action_index"] = action_index
    logger.warning("browser_run_blocked", **fields)
