from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO


class StorageError(Exception):
    """Base class for storage provider errors."""


class StorageNotFoundError(StorageError):
    """Raised when a referenced storage object does not exist."""


class StorageObjectExistsError(StorageError):
    """Raised rather than overwriting an existing storage object."""


@dataclass(frozen=True)
class StorageMetadata:
    size: int
    last_modified: datetime


class StorageService(ABC):
    """Provider-neutral object storage operations used by document services."""

    @abstractmethod
    def upload_file(self, source: BinaryIO, storage_key: str, content_type: str) -> StorageMetadata:
        raise NotImplementedError

    @abstractmethod
    def download_file(self, storage_key: str) -> BinaryIO:
        raise NotImplementedError

    @abstractmethod
    def delete_file(self, storage_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_metadata(self, storage_key: str) -> StorageMetadata:
        raise NotImplementedError
