from __future__ import annotations

import threading
import uuid
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from google.api_core.exceptions import GoogleAPIError, NotFound
from pydantic import ValidationError

from app.config import Settings
from app.investigations.evidence_storage import (
    GCSEvidenceStorage,
    EvidenceStorageError,
    EmptyRecordingError,
    LocalEvidenceStorage,
    RecordingTooLargeError,
    create_evidence_storage,
)


class FakeReader:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.closed = False
        self.read_threads: list[int] = []
        self.close_thread: int | None = None

    def read(self, _size: int) -> bytes:
        self.read_threads.append(threading.get_ident())
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.close_thread = threading.get_ident()
        self.closed = True


class FakeBlob:
    def __init__(self, name: str, *, generation: int | None = None):
        self.name = name
        self.generation = generation
        self.upload_calls = []
        self.upload_error: Exception | None = None
        self.exists_result = True
        self.exists_error: Exception | None = None
        self.exists_calls = 0
        self.delete_calls = 0
        self.delete_error: Exception | None = None
        self.reader = FakeReader([])
        self.open_calls = []
        self.download_content = b""
        self.download_error: Exception | None = None
        self.download_paths: list[Path] = []
        self.operation_threads: list[int] = []

    def upload_from_file(self, file, **kwargs) -> None:
        self.operation_threads.append(threading.get_ident())
        if self.upload_error is not None:
            raise self.upload_error
        self.upload_calls.append((file.read(), kwargs))

    def exists(self) -> bool:
        self.operation_threads.append(threading.get_ident())
        self.exists_calls += 1
        if self.exists_error is not None:
            raise self.exists_error
        return self.exists_result

    def open(self, mode: str, **kwargs) -> FakeReader:
        self.operation_threads.append(threading.get_ident())
        self.open_calls.append((mode, kwargs))
        return self.reader

    def delete(self) -> None:
        self.operation_threads.append(threading.get_ident())
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error

    def download_to_filename(self, filename: str) -> None:
        self.operation_threads.append(threading.get_ident())
        path = Path(filename)
        self.download_paths.append(path)
        if self.download_error is not None:
            raise self.download_error
        path.write_bytes(self.download_content)


class FakeBucket:
    def __init__(self, name: str):
        self.name = name
        self.blobs: dict[str, FakeBlob] = {}
        self.requested_names: list[str] = []
        self.get_blob_results: dict[str, FakeBlob | None] = {}
        self.get_blob_calls: list[str] = []
        self.get_blob_threads: list[int] = []
        self.get_blob_error: Exception | None = None

    def blob(self, name: str) -> FakeBlob:
        self.requested_names.append(name)
        return self.blobs.setdefault(name, FakeBlob(name))

    def get_blob(self, name: str) -> FakeBlob | None:
        self.get_blob_calls.append(name)
        self.get_blob_threads.append(threading.get_ident())
        if self.get_blob_error is not None:
            raise self.get_blob_error
        if name in self.get_blob_results:
            return self.get_blob_results[name]
        blob = self.blobs.setdefault(name, FakeBlob(name))
        blob.generation = blob.generation or 1
        return blob


class FakeClient:
    def __init__(self):
        self.buckets: dict[str, FakeBucket] = {}
        self.requested_buckets: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.requested_buckets.append(name)
        return self.buckets.setdefault(name, FakeBucket(name))


def _settings(**updates) -> Settings:
    return Settings(session_secret="test-secret", _env_file=None, **updates)


def _gcs_storage() -> tuple[GCSEvidenceStorage, FakeBucket]:
    client = FakeClient()
    storage = GCSEvidenceStorage("test-bucket", client=client)
    return storage, client.buckets["test-bucket"]


def test_settings_default_to_local_storage(monkeypatch):
    monkeypatch.delenv("EVIDENCE_STORAGE_BACKEND", raising=False)

    settings = _settings()

    assert settings.evidence_storage_backend == "local"
    assert settings.gcs_bucket == ""


def test_gcs_settings_require_bucket_and_unknown_backend_is_rejected():
    with pytest.raises(ValidationError, match="GCS_BUCKET is required"):
        _settings(evidence_storage_backend="gcs", gcs_bucket="   ")

    with pytest.raises(ValidationError, match="evidence_storage_backend"):
        _settings(evidence_storage_backend="s3")


def test_storage_factory_selects_local_and_cached_gcs(monkeypatch, tmp_path):
    from app.investigations import evidence_storage
    from app.investigations.routes import get_evidence_storage

    fake_client = FakeClient()
    clients_created = []

    def create_client():
        clients_created.append(fake_client)
        return fake_client

    monkeypatch.setattr(evidence_storage.storage, "Client", create_client)
    create_evidence_storage.cache_clear()
    try:
        local = get_evidence_storage(
            _settings(evidence_storage_backend="local", evidence_storage_dir=tmp_path)
        )
        gcs_settings = _settings(
            evidence_storage_backend="gcs",
            evidence_storage_dir=tmp_path,
            gcs_bucket="test-bucket",
        )
        gcs = get_evidence_storage(gcs_settings)
        cached_gcs = get_evidence_storage(gcs_settings)
    finally:
        create_evidence_storage.cache_clear()

    assert isinstance(local, LocalEvidenceStorage)
    assert isinstance(gcs, GCSEvidenceStorage)
    assert cached_gcs is gcs
    assert clients_created == [fake_client]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mime_type", "extension"),
    [("video/webm", ".webm"), ("video/mp4; codecs=h264", ".mp4")],
)
async def test_gcs_save_preserves_key_content_type_size_and_precondition(
    mime_type, extension
):
    storage, bucket = _gcs_storage()
    investigation_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    event_loop_thread = threading.get_ident()

    stored = await storage.save_recording(
        UploadFile(filename=f"recording{extension}", file=BytesIO(b"video-data")),
        investigation_id=investigation_id,
        evidence_id=evidence_id,
        mime_type=mime_type,
        max_bytes=100,
    )

    expected_key = f"{investigation_id}/{evidence_id}{extension}"
    blob = bucket.blobs[expected_key]
    assert stored.storage_key == expected_key
    assert stored.size_bytes == 10
    assert blob.upload_calls == [
        (
            b"video-data",
            {
                "size": 10,
                "content_type": mime_type.split(";", 1)[0],
                "if_generation_match": 0,
            },
        )
    ]
    assert blob.operation_threads[0] != event_loop_thread


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "max_bytes", "error"),
    [(b"", 10, EmptyRecordingError), (b"too-large", 3, RecordingTooLargeError)],
)
async def test_invalid_gcs_upload_never_calls_provider(content, max_bytes, error):
    storage, bucket = _gcs_storage()

    with pytest.raises(error):
        await storage.save_recording(
            UploadFile(filename="recording.webm", file=BytesIO(content)),
            investigation_id=uuid.uuid4(),
            evidence_id=uuid.uuid4(),
            mime_type="video/webm",
            max_bytes=max_bytes,
        )

    assert bucket.requested_names == []


@pytest.mark.anyio
async def test_gcs_upload_failure_is_backend_neutral():
    storage, bucket = _gcs_storage()
    investigation_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    key = f"{investigation_id}/{evidence_id}.webm"
    bucket.blob(key).upload_error = GoogleAPIError("private provider detail")

    with pytest.raises(EvidenceStorageError, match="^Unable to store evidence\\.$"):
        await storage.save_recording(
            UploadFile(filename="recording.webm", file=BytesIO(b"video")),
            investigation_id=investigation_id,
            evidence_id=evidence_id,
            mime_type="video/webm",
            max_bytes=100,
        )


@pytest.mark.anyio
async def test_gcs_open_content_streams_chunks_and_closes_reader():
    storage, bucket = _gcs_storage()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.webm"
    blob = FakeBlob(key, generation=42)
    blob.reader = FakeReader([b"first", b"second"])
    bucket.get_blob_results[key] = blob
    event_loop_thread = threading.get_ident()

    content = await storage.open_content(key)
    chunks = [chunk async for chunk in content.chunks]

    assert chunks == [b"first", b"second"]
    assert bucket.get_blob_calls == [key]
    assert bucket.requested_names == []
    assert blob.generation == 42
    assert blob.open_calls == [("rb", {"chunk_size": 1024 * 1024})]
    assert blob.exists_calls == 0
    assert blob.reader.closed is True
    assert bucket.get_blob_threads[0] != event_loop_thread
    assert all(thread != event_loop_thread for thread in blob.reader.read_threads)
    assert blob.reader.close_thread != event_loop_thread


@pytest.mark.anyio
async def test_missing_gcs_content_is_unavailable_before_streaming():
    storage, bucket = _gcs_storage()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.webm"
    bucket.get_blob_results[key] = None

    with pytest.raises(
        EvidenceStorageError, match="^Evidence content is unavailable\\.$"
    ):
        await storage.open_content(key)

    assert bucket.get_blob_calls == [key]
    assert bucket.requested_names == []


@pytest.mark.anyio
async def test_gcs_delete_is_correct_and_missing_is_idempotent():
    storage, bucket = _gcs_storage()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.mp4"
    blob = bucket.blob(key)

    await storage.delete(key)
    blob.delete_error = NotFound("already absent")
    await storage.delete(key)

    assert bucket.requested_names[-2:] == [key, key]
    assert blob.delete_calls == 2


@pytest.mark.anyio
async def test_gcs_delete_failure_is_backend_neutral():
    storage, bucket = _gcs_storage()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.webm"
    bucket.blob(key).delete_error = GoogleAPIError("private provider detail")

    with pytest.raises(EvidenceStorageError, match="^Unable to remove evidence\\.$"):
        await storage.delete(key)


@pytest.mark.anyio
async def test_gcs_recording_source_is_validated_without_download():
    storage, bucket = _gcs_storage()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.webm"
    blob = FakeBlob(key, generation=84)
    bucket.get_blob_results[key] = blob

    source = await storage.resolve_recording(key)

    assert source.file_uri == f"gs://test-bucket/{key}"
    assert source.local_path is None
    assert bucket.get_blob_calls == [key]
    assert blob.download_paths == []
    assert blob.open_calls == []


@pytest.mark.anyio
async def test_missing_gcs_recording_source_is_safe_and_not_downloaded():
    storage, bucket = _gcs_storage()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.webm"
    bucket.get_blob_results[key] = None

    with pytest.raises(
        EvidenceStorageError, match="^Evidence content is unavailable\\.$"
    ):
        await storage.resolve_recording(key)

    assert bucket.get_blob_calls == [key]
    assert bucket.requested_names == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "storage_key",
    [
        "",
        "/absolute.webm",
        "../escape.webm",
        "dir/../escape.webm",
        "bad\\key",
        "bad\nkey",
    ],
)
async def test_gcs_rejects_malformed_storage_keys(storage_key):
    storage, bucket = _gcs_storage()

    with pytest.raises(EvidenceStorageError, match="Invalid evidence storage key"):
        await storage.open_content(storage_key)
    with pytest.raises(EvidenceStorageError, match="Invalid evidence storage key"):
        await storage.delete(storage_key)
    with pytest.raises(EvidenceStorageError, match="Invalid evidence storage key"):
        await storage.resolve_recording(storage_key)

    assert bucket.requested_names == []
