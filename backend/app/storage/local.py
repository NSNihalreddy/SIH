from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.storage.base import (
    StorageError,
    StorageMetadata,
    StorageNotFoundError,
    StorageObjectExistsError,
    StorageService,
)


class LocalStorageService(StorageService):
    def __init__(self, root: Path | str):
        self._root = Path(root).expanduser().resolve()

    def _resolve_key(self, storage_key: str) -> Path:
        if not storage_key or "\\" in storage_key or "\x00" in storage_key:
            raise StorageError("Invalid storage key")
        key = PurePosixPath(storage_key)
        if key.is_absolute() or any(part in ("", ".", "..") for part in key.parts):
            raise StorageError("Invalid storage key")
        destination = (self._root / Path(*key.parts)).resolve()
        try:
            destination.relative_to(self._root)
        except ValueError as exc:
            raise StorageError("Invalid storage key") from exc
        return destination

    def upload_file(self, source: BinaryIO, storage_key: str, content_type: str) -> StorageMetadata:
        del content_type  # Kept in the provider contract for future object stores.
        destination = self._resolve_key(storage_key)
        created = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as target:
                created = True
                size = 0
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    size += len(chunk)
                target.flush()
            stat = destination.stat()
            return StorageMetadata(size=size, last_modified=datetime.fromtimestamp(stat.st_mtime, timezone.utc))
        except FileExistsError as exc:
            raise StorageObjectExistsError("Storage object already exists") from exc
        except Exception as exc:
            if created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, StorageError):
                raise
            raise StorageError("Could not store the uploaded file") from exc

    def download_file(self, storage_key: str) -> BinaryIO:
        path = self._resolve_key(storage_key)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise StorageNotFoundError("Stored file was not found") from exc
        except OSError as exc:
            raise StorageError("Could not read the stored file") from exc

    def delete_file(self, storage_key: str) -> None:
        path = self._resolve_key(storage_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Could not remove the stored file") from exc

    def exists(self, storage_key: str) -> bool:
        path = self._resolve_key(storage_key)
        return path.is_file()

    def get_metadata(self, storage_key: str) -> StorageMetadata:
        path = self._resolve_key(storage_key)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise StorageNotFoundError("Stored file was not found") from exc
        except OSError as exc:
            raise StorageError("Could not read stored file metadata") from exc
        if not path.is_file():
            raise StorageNotFoundError("Stored file was not found")
        return StorageMetadata(size=stat.st_size, last_modified=datetime.fromtimestamp(stat.st_mtime, timezone.utc))
