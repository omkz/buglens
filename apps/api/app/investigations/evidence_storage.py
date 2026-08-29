"""Backend-neutral storage for investigation recordings."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

import anyio
from fastapi import UploadFile
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import storage

_CHUNK_SIZE = 1024 * 1024
_EXTENSIONS = {
    "video/webm": ".webm",
    "video/mp4": ".mp4",
}


class EvidenceStorageError(RuntimeError):
    """Raised when evidence storage cannot complete an operation."""


class EmptyRecordingError(EvidenceStorageError):
    """Raised when an uploaded recording has no bytes."""


class RecordingTooLargeError(EvidenceStorageError):
    """Raised when a recording exceeds the configured upload limit."""


@dataclass(frozen=True)
class StoredRecording:
    storage_key: str
    size_bytes: int


@dataclass(frozen=True)
class EvidenceContent:
    chunks: AsyncIterator[bytes]


@dataclass(frozen=True)
class RecordingSource:
    """A validated recording location, represented by exactly one backend."""

    local_path: Path | None = None
    file_uri: str | None = None

    def __post_init__(self) -> None:
        if (self.local_path is None) == (self.file_uri is None):
            raise ValueError(
                "Recording source must have exactly one local path or file URI."
            )


class EvidenceStorage(Protocol):
    async def save_recording(
        self,
        upload: UploadFile,
        *,
        investigation_id: uuid.UUID,
        evidence_id: uuid.UUID,
        mime_type: str,
        max_bytes: int,
    ) -> StoredRecording: ...

    async def delete(self, storage_key: str) -> None: ...

    async def open_content(self, storage_key: str) -> EvidenceContent: ...

    async def resolve_recording(self, storage_key: str) -> RecordingSource: ...

def _validate_storage_key(storage_key: str) -> None:
    parts = storage_key.split("/")
    if (
        not storage_key
        or storage_key.startswith("/")
        or "\\" in storage_key
        or any(part in {"", ".", ".."} for part in parts)
        or any(not character.isprintable() for character in storage_key)
    ):
        raise EvidenceStorageError("Invalid evidence storage key.")


def _recording_storage_key(
    investigation_id: uuid.UUID,
    evidence_id: uuid.UUID,
    mime_type: str,
) -> str:
    extension = _EXTENSIONS[mime_type.split(";", 1)[0]]
    return f"{investigation_id}/{evidence_id}{extension}"


class LocalEvidenceStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    async def save_recording(
        self,
        upload: UploadFile,
        *,
        investigation_id: uuid.UUID,
        evidence_id: uuid.UUID,
        mime_type: str,
        max_bytes: int,
    ) -> StoredRecording:
        storage_key = _recording_storage_key(
            investigation_id, evidence_id, mime_type
        )
        target = await anyio.to_thread.run_sync(self._resolve_key, storage_key)
        partial = target.with_suffix(f"{target.suffix}.part")
        writer: BinaryIO | None = None
        size_bytes = 0

        try:
            await anyio.to_thread.run_sync(
                lambda: target.parent.mkdir(parents=True, exist_ok=True)
            )
            writer = await anyio.to_thread.run_sync(lambda: partial.open("xb"))
            while chunk := await upload.read(_CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise RecordingTooLargeError
                await anyio.to_thread.run_sync(writer.write, chunk)
            if size_bytes == 0:
                raise EmptyRecordingError
            await anyio.to_thread.run_sync(writer.close)
            writer = None
            await anyio.to_thread.run_sync(lambda: partial.replace(target))
        except (EmptyRecordingError, RecordingTooLargeError):
            if writer is not None:
                await anyio.to_thread.run_sync(writer.close)
            await self._delete_path(partial)
            raise
        except (OSError, RuntimeError) as exc:
            if writer is not None:
                await anyio.to_thread.run_sync(writer.close)
            await self._delete_path(partial)
            raise EvidenceStorageError("Unable to store evidence.") from exc

        return StoredRecording(storage_key=storage_key, size_bytes=size_bytes)

    async def delete(self, storage_key: str) -> None:
        path = await anyio.to_thread.run_sync(self._resolve_key, storage_key)
        await self._delete_path(path)

    async def open_content(self, storage_key: str) -> EvidenceContent:
        path = await self._content_path(storage_key)
        return EvidenceContent(chunks=self._stream_path(path))

    async def resolve_recording(self, storage_key: str) -> RecordingSource:
        return RecordingSource(local_path=await self._content_path(storage_key))

    async def _content_path(self, storage_key: str) -> Path:
        path = await anyio.to_thread.run_sync(self._resolve_key, storage_key)
        if not await anyio.to_thread.run_sync(path.is_file):
            raise EvidenceStorageError("Evidence content is unavailable.")
        return path

    async def _stream_path(self, path: Path) -> AsyncIterator[bytes]:
        reader: BinaryIO | None = None
        try:
            reader = await anyio.to_thread.run_sync(lambda: path.open("rb"))
            while chunk := await anyio.to_thread.run_sync(reader.read, _CHUNK_SIZE):
                yield chunk
        except OSError as exc:
            raise EvidenceStorageError("Unable to read evidence.") from exc
        finally:
            if reader is not None:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(reader.close)

    def _resolve_key(self, storage_key: str) -> Path:
        _validate_storage_key(storage_key)
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise EvidenceStorageError("Invalid evidence storage key.")
        return candidate

    async def _delete_path(self, path: Path) -> None:
        try:
            await anyio.to_thread.run_sync(path.unlink, True)
        except OSError as exc:
            raise EvidenceStorageError("Unable to remove evidence.") from exc


class GCSEvidenceStorage:
    def __init__(
        self,
        bucket_name: str,
        client: storage.Client | None = None,
    ):
        self.client = client if client is not None else storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    async def save_recording(
        self,
        upload: UploadFile,
        *,
        investigation_id: uuid.UUID,
        evidence_id: uuid.UUID,
        mime_type: str,
        max_bytes: int,
    ) -> StoredRecording:
        storage_key = _recording_storage_key(
            investigation_id, evidence_id, mime_type
        )
        _validate_storage_key(storage_key)
        size_bytes = 0
        while chunk := await upload.read(_CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                raise RecordingTooLargeError
        if size_bytes == 0:
            raise EmptyRecordingError

        blob = self.bucket.blob(storage_key)
        content_type = mime_type.split(";", 1)[0]
        try:
            await upload.seek(0)
            await anyio.to_thread.run_sync(
                lambda: blob.upload_from_file(
                    upload.file,
                    size=size_bytes,
                    content_type=content_type,
                    if_generation_match=0,
                )
            )
        except (GoogleAPIError, OSError, RuntimeError) as exc:
            raise EvidenceStorageError("Unable to store evidence.") from exc

        return StoredRecording(storage_key=storage_key, size_bytes=size_bytes)

    async def delete(self, storage_key: str) -> None:
        _validate_storage_key(storage_key)
        blob = self.bucket.blob(storage_key)
        try:
            await anyio.to_thread.run_sync(blob.delete)
        except NotFound:
            return
        except (GoogleAPIError, OSError) as exc:
            raise EvidenceStorageError("Unable to remove evidence.") from exc

    async def open_content(self, storage_key: str) -> EvidenceContent:
        _validate_storage_key(storage_key)
        blob = await self._get_blob(storage_key)
        return EvidenceContent(chunks=self._stream_blob(blob))

    async def resolve_recording(self, storage_key: str) -> RecordingSource:
        _validate_storage_key(storage_key)
        await self._get_blob(storage_key)
        return RecordingSource(file_uri=f"gs://{self.bucket.name}/{storage_key}")

    async def _get_blob(self, storage_key: str) -> storage.Blob:
        try:
            blob = await anyio.to_thread.run_sync(
                self.bucket.get_blob, storage_key
            )
        except NotFound as exc:
            raise EvidenceStorageError("Evidence content is unavailable.") from exc
        except (GoogleAPIError, OSError) as exc:
            raise EvidenceStorageError("Unable to read evidence.") from exc
        if blob is None:
            raise EvidenceStorageError("Evidence content is unavailable.")
        return blob

    async def _stream_blob(self, blob: storage.Blob) -> AsyncIterator[bytes]:
        reader: BinaryIO | None = None
        try:
            reader = await anyio.to_thread.run_sync(
                lambda: blob.open("rb", chunk_size=_CHUNK_SIZE)
            )
            while chunk := await anyio.to_thread.run_sync(reader.read, _CHUNK_SIZE):
                yield chunk
        except NotFound as exc:
            raise EvidenceStorageError("Evidence content is unavailable.") from exc
        except (GoogleAPIError, OSError) as exc:
            raise EvidenceStorageError("Unable to read evidence.") from exc
        finally:
            if reader is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await anyio.to_thread.run_sync(reader.close)
                    except (GoogleAPIError, OSError) as exc:
                        raise EvidenceStorageError("Unable to read evidence.") from exc


@lru_cache(maxsize=4)
def create_evidence_storage(
    backend: str,
    evidence_storage_dir: str,
    gcs_bucket: str,
) -> EvidenceStorage:
    if backend == "local":
        return LocalEvidenceStorage(Path(evidence_storage_dir))
    if backend == "gcs":
        return GCSEvidenceStorage(gcs_bucket)
    raise ValueError(f"Unsupported evidence storage backend: {backend}")
