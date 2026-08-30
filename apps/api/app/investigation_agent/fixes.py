"""Safety checks and deterministic rendering for structured fix proposals."""

from __future__ import annotations

from difflib import unified_diff
from pathlib import PurePosixPath

from .schemas import ProposedFileChange

MAX_FIX_CONTENT_BYTES = 50_000

_FORBIDDEN_SEGMENTS = {
    ".buildkite",
    ".circleci",
    ".github",
    ".gitlab",
    ".next",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "vendor",
}
_FORBIDDEN_FILENAMES = {
    ".env",
    "bun.lock",
    "cargo.lock",
    "composer.lock",
    "credentials",
    "credentials.json",
    "gemfile.lock",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "secrets",
    "secrets.json",
    "uv.lock",
    "yarn.lock",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "jenkinsfile",
}
_FORBIDDEN_SUFFIXES = {
    ".cer",
    ".crt",
    ".der",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".key",
    ".lock",
    ".min.css",
    ".min.js",
    ".p12",
    ".pem",
    ".pfx",
    ".png",
    ".pyc",
    ".so",
    ".webp",
    ".zip",
}


def is_forbidden_fix_path(path: str) -> bool:
    """Reject paths unsuitable for an autonomous code-change proposal."""
    if (
        not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or any(character in path for character in ("\r", "\n", "\0"))
    ):
        return True
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return True
    lowered = tuple(part.lower() for part in parts)
    filename = lowered[-1]
    if any(part in _FORBIDDEN_SEGMENTS for part in lowered):
        return True
    if filename in _FORBIDDEN_FILENAMES or filename.startswith(".env."):
        return True
    if ".generated." in filename or filename.endswith(".generated"):
        return True
    if "secret" in filename or "credential" in filename:
        return True
    return any(filename.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES)


def render_unified_diff(change: ProposedFileChange) -> str:
    """Render a stable patch directly from the validated before/after content."""
    lines = unified_diff(
        change.original_content.splitlines(keepends=True),
        change.updated_content.splitlines(keepends=True),
        fromfile=f"a/{change.path}",
        tofile=f"b/{change.path}",
        lineterm="\n",
    )
    rendered: list[str] = []
    for line in lines:
        rendered.append(line)
        if not line.endswith("\n"):
            rendered.append("\n\\ No newline at end of file\n")
    return "".join(rendered)
