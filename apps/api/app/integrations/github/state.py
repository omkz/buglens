"""Temporary storage for the GitHub App connection/session state.

This is intentionally process-local (no database yet). The store is kept
behind a small class so it can be swapped for a PostgreSQL-backed
implementation later without touching the routes or client code.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class GitHubConnection:
    installation_id: int
    account_login: str | None
    access_token: str
    connected_at: float = field(default_factory=time.time)


class GitHubConnectionStore:
    """Single-slot, in-memory store for the current dev/demo session."""

    def __init__(self) -> None:
        self._pending_states: set[str] = set()
        self._connection: GitHubConnection | None = None

    def create_pending_state(self) -> str:
        state = secrets.token_urlsafe(24)
        self._pending_states.add(state)
        return state

    def consume_pending_state(self, state: str | None) -> bool:
        if state is None or state not in self._pending_states:
            return False
        self._pending_states.discard(state)
        return True

    def save_connection(self, connection: GitHubConnection) -> None:
        self._connection = connection

    def get_connection(self) -> GitHubConnection | None:
        return self._connection


_store = GitHubConnectionStore()


def get_connection_store() -> GitHubConnectionStore:
    return _store
