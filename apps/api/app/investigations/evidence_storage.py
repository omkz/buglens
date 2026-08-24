"""Backend-neutral storage for investigation recordings."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncContextManager, BinaryIO, Protocol

import anyio
from fastapi import UploadFile

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

    def materialize(self, storage_key: str) -> AsyncContextManager[Path]: ...


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
        extension = _EXTENSIONS[mime_type.split(";", 1)[0]]
        storage_key = f"{investigation_id}/{evidence_id}{extension}"
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

    @asynccontextmanager
    async def materialize(self, storage_key: str) -> AsyncIterator[Path]:
        yield await self._content_path(storage_key)

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
                await anyio.to_thread.run_sync(reader.close)

    def _resolve_key(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise EvidenceStorageError("Invalid evidence storage key.")
        return candidate

    async def _delete_path(self, path: Path) -> None:
        try:
            await anyio.to_thread.run_sync(path.unlink, True)
        except OSError as exc:
            raise EvidenceStorageError("Unable to remove evidence.") from exc
