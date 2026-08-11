"""Installation-selection rules for the GitHub OAuth callback.

Kept as a pure function, separate from the FastAPI route and the HTTP
client, so the selection rules can be unit tested directly without mocking
GitHub's API.
"""

from __future__ import annotations

from collections.abc import Sequence

from .client import GitHubInstallation


class InstallationSelectionError(Exception):
    """Raised when no single installation can be safely selected.

    `code` matches one of the `github_error` query values the OAuth
    callback redirects with.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def select_installation(
    installations: Sequence[GitHubInstallation],
    *,
    app_id: str,
    requested_installation_id: int | None,
) -> GitHubInstallation:
    """Pick the installation to connect, never trusting the caller blindly.

    - If `requested_installation_id` is given, it must exactly match one of
      the authenticated user's own installations *and* belong to our App —
      otherwise it is rejected outright rather than falling back to
      auto-selection.
    - Otherwise, exactly one of the user's installations must belong to our
      App: zero is `app_not_installed`, more than one is
      `ambiguous_installation` (never chosen arbitrarily).
    """
    if requested_installation_id is not None:
        candidate = next(
            (
                installation
                for installation in installations
                if installation.id == requested_installation_id
            ),
            None,
        )
        if candidate is not None and str(candidate.app_id) == app_id:
            return candidate
        raise InstallationSelectionError("app_not_installed")

    matches = [
        installation
        for installation in installations
        if str(installation.app_id) == app_id
    ]

    if not matches:
        raise InstallationSelectionError("app_not_installed")
    if len(matches) > 1:
        raise InstallationSelectionError("ambiguous_installation")

    return matches[0]
