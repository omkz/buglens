"""Resolves the GitHub App private key from configuration.

Kept separate from client.py (pure HTTP calls + JWT signing) and routes.py
(HTTP orchestration) so this one file-reading concern lives in exactly one
place, and can be tested without mocking HTTP or touching create_app_jwt.
"""

from __future__ import annotations

from pathlib import Path

from .client import GitHubAPIError


def resolve_private_key(*, private_key_path: str, private_key: str) -> str:
    """Resolve the GitHub App private key PEM contents.

    Prefers a file at `private_key_path` (read as UTF-8) over the inline
    `private_key` value, so local development can point at a real .pem
    file instead of escaping newlines in .env. An inline value with
    escaped ``\\n`` sequences is returned as-is; create_app_jwt already
    normalizes that.

    Never logs the path or key contents. Every failure mode -- missing
    file, unreadable file, invalid path, or nothing configured at all --
    raises GitHubAPIError with a generic, static message: no path, PEM
    contents, or OS error detail is ever included, so it's always safe to
    surface to an API response or a log line.
    """
    if private_key_path:
        try:
            return Path(private_key_path).read_text(encoding="utf-8")
        except (OSError, ValueError):
            raise GitHubAPIError(
                "Unable to read the configured GitHub App private key file."
            ) from None

    if private_key:
        return private_key

    raise GitHubAPIError("GitHub App private key is not configured.")
