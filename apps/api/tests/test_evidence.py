from __future__ import annotations

import json
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import itsdangerous
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.integrations.github.repository import PersistedGitHubConnection
from app.investigations.evidence_storage import (
    EvidenceContent,
    EvidenceStorageError,
    EmptyRecordingError,
    LocalEvidenceStorage,
    RecordingTooLargeError,
    StoredRecording,
)
from app.investigations.repository import PersistedEvidence, PersistedInvestigation


def _signed_session_cookie(connection_id: str) -> str:
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    payload = b64encode(json.dumps({"github_connection_id": connection_id}).encode())
    return signer.sign(payload).decode()


def _connection() -> PersistedGitHubConnection:
    return PersistedGitHubConnection(
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_installation_id=987654,
        account_login="octo-org",
    )


def _investigation(investigation_id: uuid.UUID | None = None) -> PersistedInvestigation:
    return PersistedInvestigation(
        id=investigation_id or uuid.uuid4(),
        project_id=uuid.uuid4(),
        project_name="Checkout Demo",
        github_repository_full_name="octo-org/checkout",
        title="Checkout button does nothing",
        description=None,
        status="pending",
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )


def _persisted_from_drafts(investigation_id, drafts):
    return [
        PersistedEvidence(
            id=draft.id,
            investigation_id=investigation_id,
            kind=draft.kind,
            mime_type=draft.mime_type,
            filename=draft.filename,
            storage_key=draft.storage_key,
            size_bytes=draft.size_bytes,
            text_content=draft.text_content,
            created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )
        for draft in drafts
    ]


def _connected_client(monkeypatch, tmp_path: Path, *, max_bytes: int = 1024):
    from app.investigations import routes
    from app.main import app

    connection = _connection()
    investigation = _investigation()
    storage = LocalEvidenceStorage(tmp_path)
    settings = get_settings().model_copy(
        update={
            "evidence_storage_dir": tmp_path,
            "max_evidence_upload_bytes": max_bytes,
        }
    )

    async def lookup_connection(*args, **kwargs):
        return connection

    async def lookup_investigation(*args, **kwargs):
        return investigation

    monkeypatch.setattr(routes, "get_connection_by_id", lookup_connection)
    monkeypatch.setattr(routes, "load_investigation", lookup_investigation)
    app.dependency_overrides[routes.get_evidence_storage] = lambda: storage
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    client.cookies.set(
        "buglens_session", _signed_session_cookie(str(connection.connection_id))
    )
    return client, app, routes, connection, investigation, storage


class FakeEvidenceStorage:
    def __init__(self, content: bytes = b""):
        self.content = content
        self.saved = []
        self.opened = []
        self.deleted = []

    async def save_recording(self, upload, **kwargs):
        self.saved.append((upload, kwargs))
        extension = ".mp4" if kwargs["mime_type"] == "video/mp4" else ".webm"
        return StoredRecording(
            storage_key=(
                f'{kwargs["investigation_id"]}/{kwargs["evidence_id"]}{extension}'
            ),
            size_bytes=len(self.content),
        )

    async def open_content(self, storage_key):
        self.opened.append(storage_key)

        async def chunks():
            midpoint = len(self.content) // 2
            yield self.content[:midpoint]
            yield self.content[midpoint:]

        return EvidenceContent(chunks=chunks())

    async def delete(self, storage_key):
        self.deleted.append(storage_key)


def test_authenticated_recording_and_logs_are_persisted_together(
    monkeypatch, tmp_path
):
    client, app, routes, connection, investigation, _storage = _connected_client(
        monkeypatch, tmp_path
    )
    captured = {}

    async def persist(db, **kwargs):
        captured.update(kwargs)
        return _persisted_from_drafts(investigation.id, kwargs["items"])

    monkeypatch.setattr(routes, "create_evidence_items", persist)
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            files={"recording": ("recording.webm", b"webm-data", "video/webm")},
            data={"logs": "Console error at checkout"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert captured["installation_id"] == connection.installation_id
    assert captured["investigation_id"] == investigation.id
    assert [item["kind"] for item in response.json()["evidence"]] == [
        "recording",
        "logs",
    ]
    assert response.json()["evidence"][1]["text_content"] == (
        "Console error at checkout"
    )
    assert "storage_key" not in response.text
    recording = captured["items"][0]
    assert recording.size_bytes == len(b"webm-data")
    assert recording.storage_key.startswith(f"{investigation.id}/")
    assert (tmp_path / recording.storage_key).read_bytes() == b"webm-data"


def test_recording_route_uses_backend_neutral_storage(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )
    storage = FakeEvidenceStorage(b"fake-video")
    app.dependency_overrides[routes.get_evidence_storage] = lambda: storage

    async def persist(db, **kwargs):
        return _persisted_from_drafts(investigation.id, kwargs["items"])

    monkeypatch.setattr(routes, "create_evidence_items", persist)
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            files={"recording": ("recording.mp4", b"ignored", "video/mp4")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert len(storage.saved) == 1
    assert storage.saved[0][1]["investigation_id"] == investigation.id
    assert response.json()["evidence"][0]["size_bytes"] == len(b"fake-video")
    assert list(tmp_path.rglob("*")) == []


def test_logs_can_be_saved_without_recording(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )

    async def persist(db, **kwargs):
        return _persisted_from_drafts(investigation.id, kwargs["items"])

    monkeypatch.setattr(routes, "create_evidence_items", persist)
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            data={"logs": "plain text only"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["evidence"][0]["kind"] == "logs"
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    ("files", "expected_status", "expected_detail"),
    [
        ({}, 400, "Provide a recording or logs."),
        (
            {"recording": ("recording.txt", b"nope", "text/plain")},
            415,
            "Recording must be a WebM or MP4 video.",
        ),
        (
            {"recording": ("recording.webm", b"", "video/webm")},
            400,
            "Recording is empty.",
        ),
        (
            {"recording": ("recording.webm", b"too-large", "video/webm")},
            413,
            "Recording is too large.",
        ),
    ],
)
def test_invalid_evidence_inputs_are_rejected(
    monkeypatch, tmp_path, files, expected_status, expected_detail
):
    client, app, _routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path, max_bytes=3)
    )
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence", files=files
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.webm"))


def test_storage_filename_is_generated_and_path_traversal_is_ignored(
    monkeypatch, tmp_path
):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )
    captured = {}

    async def persist(db, **kwargs):
        captured.update(kwargs)
        return _persisted_from_drafts(investigation.id, kwargs["items"])

    monkeypatch.setattr(routes, "create_evidence_items", persist)
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            files={
                "recording": (
                    "../../private/recording.webm",
                    b"safe",
                    "video/webm",
                )
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    draft = captured["items"][0]
    assert draft.filename == "recording.webm"
    stored_path = (tmp_path / draft.storage_key).resolve()
    assert stored_path.is_relative_to(tmp_path.resolve())
    assert stored_path.name != draft.filename


def test_inaccessible_investigation_returns_404_before_storage(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )

    async def inaccessible(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "load_investigation", inaccessible)
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            files={"recording": ("recording.webm", b"safe", "video/webm")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Investigation not found."}
    assert list(tmp_path.rglob("*")) == []


def test_database_failure_returns_503_and_removes_recording(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("private database detail")

    monkeypatch.setattr(routes, "create_evidence_items", fail)
    monkeypatch.setattr(routes, "logger", Mock())
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            files={"recording": ("recording.webm", b"safe", "video/webm")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Evidence is temporarily unavailable."}
    assert "private database detail" not in response.text
    assert not list(tmp_path.rglob("*.webm"))


def test_storage_failure_returns_safe_503(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, storage = (
        _connected_client(monkeypatch, tmp_path)
    )

    async def fail(*args, **kwargs):
        raise EvidenceStorageError("private filesystem path")

    monkeypatch.setattr(storage, "save_recording", fail)
    monkeypatch.setattr(routes, "logger", Mock())
    try:
        response = client.post(
            f"/api/investigations/{investigation.id}/evidence",
            files={"recording": ("recording.webm", b"safe", "video/webm")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Evidence storage is temporarily unavailable."
    }
    assert "private filesystem path" not in response.text


def test_evidence_list_is_scoped_and_does_not_expose_storage_key(
    monkeypatch, tmp_path
):
    client, app, routes, connection, investigation, _storage = _connected_client(
        monkeypatch, tmp_path
    )
    item = PersistedEvidence(
        id=uuid.uuid4(),
        investigation_id=investigation.id,
        kind="logs",
        mime_type="text/plain",
        filename=None,
        storage_key="internal/private/key",
        size_bytes=None,
        text_content="visible log",
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    captured = {}

    async def scoped_list(db, **kwargs):
        captured.update(kwargs)
        return [item]

    monkeypatch.setattr(routes, "list_evidence_items", scoped_list)
    try:
        response = client.get(f"/api/investigations/{investigation.id}/evidence")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured == {
        "installation_id": connection.installation_id,
        "investigation_id": investigation.id,
    }
    assert response.json()["evidence"][0]["text_content"] == "visible log"
    assert "storage_key" not in response.text
    assert "internal/private/key" not in response.text


def test_other_installation_cannot_list_evidence(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )

    async def inaccessible(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "list_evidence_items", inaccessible)
    try:
        response = client.get(f"/api/investigations/{investigation.id}/evidence")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Investigation not found."}


def test_recording_content_is_scoped_before_file_resolution(monkeypatch, tmp_path):
    client, app, routes, connection, investigation, storage = (
        _connected_client(monkeypatch, tmp_path)
    )
    open_content = Mock()
    captured = {}
    monkeypatch.setattr(storage, "open_content", open_content)

    async def inaccessible(db, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(routes, "get_recording_evidence", inaccessible)
    try:
        response = client.get(
            f"/api/investigations/{investigation.id}/evidence/{uuid.uuid4()}/content"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Evidence not found."}
    assert captured["installation_id"] == connection.installation_id
    assert captured["investigation_id"] == investigation.id
    open_content.assert_not_called()


def test_recording_content_streams_with_persisted_mime_type(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, _storage = (
        _connected_client(monkeypatch, tmp_path)
    )
    evidence_id = uuid.uuid4()
    storage_key = f"{investigation.id}/{evidence_id}.webm"
    fake_storage = FakeEvidenceStorage(b"video bytes")
    app.dependency_overrides[routes.get_evidence_storage] = lambda: fake_storage
    item = PersistedEvidence(
        id=evidence_id,
        investigation_id=investigation.id,
        kind="recording",
        mime_type="video/webm",
        filename="recording.webm",
        storage_key=storage_key,
        size_bytes=11,
        text_content=None,
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    async def scoped_recording(*args, **kwargs):
        return item

    monkeypatch.setattr(routes, "get_recording_evidence", scoped_recording)
    try:
        response = client.get(
            f"/api/investigations/{investigation.id}/evidence/{evidence_id}/content"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/webm"
    assert response.headers["content-length"] == "11"
    assert "content-disposition" not in response.headers
    assert response.content == b"video bytes"
    assert fake_storage.opened == [storage_key]


def test_missing_recording_content_returns_safe_storage_error(monkeypatch, tmp_path):
    client, app, routes, _connection_value, investigation, storage = (
        _connected_client(monkeypatch, tmp_path)
    )
    evidence_id = uuid.uuid4()
    item = PersistedEvidence(
        id=evidence_id,
        investigation_id=investigation.id,
        kind="recording",
        mime_type="video/webm",
        filename="recording.webm",
        storage_key=f"{investigation.id}/{evidence_id}.webm",
        size_bytes=10,
        text_content=None,
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    async def scoped_recording(*args, **kwargs):
        return item

    async def unavailable(_storage_key):
        raise EvidenceStorageError("Evidence content is unavailable.")

    monkeypatch.setattr(routes, "get_recording_evidence", scoped_recording)
    monkeypatch.setattr(storage, "open_content", unavailable)
    monkeypatch.setattr(routes, "logger", Mock())
    try:
        response = client.get(
            f"/api/investigations/{investigation.id}/evidence/{evidence_id}/content"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Evidence storage is temporarily unavailable."
    }
    assert str(tmp_path) not in response.text


def test_evidence_creation_requires_a_signed_connection():
    from app.main import app

    response = TestClient(app).post(
        f"/api/investigations/{uuid.uuid4()}/evidence", data={"logs": "hello"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "GitHub is not connected."}


@pytest.mark.anyio
async def test_storage_adapter_rejects_empty_and_oversized_uploads(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    investigation_id = uuid.uuid4()

    with pytest.raises(EmptyRecordingError):
        await storage.save_recording(
            UploadFile(filename="empty.webm", file=BytesIO(b"")),
            investigation_id=investigation_id,
            evidence_id=uuid.uuid4(),
            mime_type="video/webm",
            max_bytes=10,
        )
    with pytest.raises(RecordingTooLargeError):
        await storage.save_recording(
            UploadFile(filename="large.webm", file=BytesIO(b"123")),
            investigation_id=investigation_id,
            evidence_id=uuid.uuid4(),
            mime_type="video/webm",
            max_bytes=2,
        )
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.webm"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mime_type", "extension"),
    [("video/webm", ".webm"), ("video/mp4", ".mp4")],
)
async def test_local_storage_saves_recording_with_unchanged_key_format(
    tmp_path, mime_type, extension
):
    storage = LocalEvidenceStorage(tmp_path)
    investigation_id = uuid.uuid4()
    evidence_id = uuid.uuid4()

    stored = await storage.save_recording(
        UploadFile(filename=f"recording{extension}", file=BytesIO(b"video-data")),
        investigation_id=investigation_id,
        evidence_id=evidence_id,
        mime_type=mime_type,
        max_bytes=100,
    )

    assert stored == StoredRecording(
        storage_key=f"{investigation_id}/{evidence_id}{extension}",
        size_bytes=10,
    )
    assert (tmp_path / stored.storage_key).read_bytes() == b"video-data"


@pytest.mark.anyio
async def test_local_storage_cleans_partial_file_after_upload_failure(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)

    class FailingUpload:
        calls = 0

        async def read(self, _size):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise RuntimeError("upload interrupted")

    with pytest.raises(EvidenceStorageError, match="Unable to store evidence"):
        await storage.save_recording(
            FailingUpload(),
            investigation_id=uuid.uuid4(),
            evidence_id=uuid.uuid4(),
            mime_type="video/webm",
            max_bytes=100,
        )

    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.webm"))


@pytest.mark.anyio
@pytest.mark.parametrize("storage_key", ["../secret.webm", "/tmp/secret.webm"])
async def test_local_storage_rejects_traversal(storage_key, tmp_path):
    storage = LocalEvidenceStorage(tmp_path)

    with pytest.raises(EvidenceStorageError, match="Invalid evidence storage key"):
        await storage.open_content(storage_key)
    with pytest.raises(EvidenceStorageError, match="Invalid evidence storage key"):
        await storage.delete(storage_key)


@pytest.mark.anyio
async def test_local_storage_deletes_content_and_reports_missing_content(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    path = tmp_path / "investigation" / "recording.webm"
    path.parent.mkdir()
    path.write_bytes(b"video")

    await storage.delete("investigation/recording.webm")

    assert not path.exists()
    with pytest.raises(
        EvidenceStorageError, match="Evidence content is unavailable"
    ):
        await storage.open_content("investigation/recording.webm")


@pytest.mark.anyio
async def test_local_storage_streams_content_in_chunks(monkeypatch, tmp_path):
    from app.investigations import evidence_storage

    monkeypatch.setattr(evidence_storage, "_CHUNK_SIZE", 3)
    storage = LocalEvidenceStorage(tmp_path)
    path = tmp_path / "investigation" / "recording.webm"
    path.parent.mkdir()
    path.write_bytes(b"abcdefgh")

    content = await storage.open_content("investigation/recording.webm")

    assert [chunk async for chunk in content.chunks] == [b"abc", b"def", b"gh"]
