from app.core.config import LOCAL_STORAGE_PATH, STORAGE_BACKEND
from app.storage.base import StorageService
from app.storage.local import LocalStorageService


def create_storage_service() -> StorageService:
    if STORAGE_BACKEND == "local":
        return LocalStorageService(LOCAL_STORAGE_PATH)
    raise RuntimeError("Configured storage backend is not available")
