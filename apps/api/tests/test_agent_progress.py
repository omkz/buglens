from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.investigation_agent.repository import (
    AgentRunSnapshot,
    PersistedAgentRun,
)
from app.investigations import routes


def _run(
    investigation_id: uuid.UUID,
    *,
    stage: str,
    message: str,
    offset: int,
) -> PersistedAgentRun:
    now = datetime.now(UTC) + timedelta(seconds=offset)
    terminal = stage in {"completed", "failed"}
    return PersistedAgentRun(
        id=uuid.uuid4(),
        investigation_id=investigation_id,
        status=stage if terminal else "running",
        agent_model="gemini-test-model",
        repository_summary=[] if stage == "completed" else None,
        duplicate_candidates=[],
        reproduction_plan=None,
        generated_test=None,
        reproduction_status=None,
        execution_result=None,
        execution_summary=None,
        started_at=now,
        completed_at=now if terminal else None,
        progress_stage=stage,
        progress_message=message,
        progress_updated_at=now,
    )


class _Request:
    async def is_disconnected(self) -> bool:
        return False


class _Session:
    def __init__(self):
        self.rolled_back = False

    async def rollback(self):
        self.rolled_back = True


class _SessionContext:
    def __init__(self, sessions):
        self.sessions = sessions
        self.session = _Session()

    async def __aenter__(self):
        self.sessions.append(self.session)
        return self.session

    async def __aexit__(self, *args):
        return None


class _SessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        return _SessionContext(self.sessions)


@pytest.mark.anyio
async def test_sse_waits_for_run_emits_changes_and_complete_then_closes(monkeypatch):
    investigation_id = uuid.uuid4()
    starting = _run(
        investigation_id,
        stage="starting",
        message="Starting investigation…",
        offset=1,
    )
    completed = _run(
        investigation_id,
        stage="completed",
        message="Investigation completed.",
        offset=2,
    )
    snapshots = iter(
        [
            AgentRunSnapshot(accessible=True, run=None),
            AgentRunSnapshot(accessible=True, run=starting),
            AgentRunSnapshot(accessible=True, run=starting),
            AgentRunSnapshot(accessible=True, run=completed),
        ]
    )

    async def load(*args, **kwargs):
        return next(snapshots)

    factory = _SessionFactory()
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(routes, "load_agent_run", load)
    stream = routes._agent_run_event_stream(
        _Request(),
        installation_id=uuid.uuid4(),
        investigation_id=investigation_id,
        poll_interval_seconds=0,
        initial_terminal_grace_seconds=0,
    )

    first = await anext(stream)
    second = await anext(stream)
    assert "event: progress" in first
    assert '"stage":"starting"' in first
    assert "event: complete" in second
    assert '"message":"Investigation completed."' in second
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert len(factory.sessions) == 4
    assert all(session.rolled_back for session in factory.sessions)


@pytest.mark.anyio
async def test_sse_failed_event_is_safe_and_closes(monkeypatch):
    investigation_id = uuid.uuid4()
    failed = _run(
        investigation_id,
        stage="failed",
        message="Investigation failed.",
        offset=1,
    )

    async def load(*args, **kwargs):
        return AgentRunSnapshot(accessible=True, run=failed)

    factory = _SessionFactory()
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(routes, "load_agent_run", load)
    stream = routes._agent_run_event_stream(
        _Request(),
        installation_id=uuid.uuid4(),
        investigation_id=investigation_id,
        poll_interval_seconds=0,
        initial_terminal_grace_seconds=0,
    )

    event = await anext(stream)
    assert event == (
        'event: failed\ndata: {"stage":"failed",'
        '"message":"Investigation failed."}\n\n'
    )
    assert "token" not in event
    assert "traceback" not in event.lower()
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.anyio
async def test_sse_retry_does_not_close_on_stale_failed_snapshot(monkeypatch):
    investigation_id = uuid.uuid4()
    stale_failed = _run(
        investigation_id,
        stage="failed",
        message="Investigation failed.",
        offset=-30,
    )
    starting = _run(
        investigation_id,
        stage="starting",
        message="Starting investigation…",
        offset=1,
    )
    snapshots = iter(
        [
            AgentRunSnapshot(accessible=True, run=stale_failed),
            AgentRunSnapshot(accessible=True, run=starting),
        ]
    )

    async def load(*args, **kwargs):
        return next(snapshots)

    monkeypatch.setattr(routes, "SessionLocal", _SessionFactory())
    monkeypatch.setattr(routes, "load_agent_run", load)
    stream = routes._agent_run_event_stream(
        _Request(),
        installation_id=uuid.uuid4(),
        investigation_id=investigation_id,
        poll_interval_seconds=0,
        initial_terminal_grace_seconds=10,
    )

    event = await anext(stream)
    assert "event: progress" in event
    assert '"stage":"starting"' in event
