from functools import lru_cache

from fastapi import HTTPException

from app.storage.base import StorageService
from app.storage.factory import create_storage_service


@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    try:
        return create_storage_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Configured storage backend is not available") from exc
