from __future__ import annotations

import os
import math
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")


def _database_url(driver: str) -> str:
    direct_url = os.getenv("DATABASE_URL", "").strip()
    if direct_url:
        url = make_url(direct_url)
        if url.get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must use PostgreSQL; SQLite is not supported.")
        return url.set(drivername=driver).render_as_string(hide_password=False)

    url = URL.create(
        drivername=driver,
        username=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD") or None,
        host=os.getenv("POSTGRES_SERVER", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "data_mine"),
    )
    return url.render_as_string(hide_password=False)


DATABASE_URL = _database_url("postgresql+asyncpg")
SYNC_DATABASE_URL = os.getenv("SYNC_DATABASE_URL", "").strip()
if SYNC_DATABASE_URL:
    _sync_url = make_url(SYNC_DATABASE_URL)
    if _sync_url.get_backend_name() != "postgresql":
        raise ValueError("SYNC_DATABASE_URL must use PostgreSQL; SQLite is not supported.")
    SYNC_DATABASE_URL = _sync_url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
else:
    SYNC_DATABASE_URL = _database_url("postgresql+psycopg")

LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
LOCAL_EMBEDDING_DIMENSION = 768
VECTOR_DIMENSION = int(os.getenv("VECTOR_DIMENSION", str(LOCAL_EMBEDDING_DIMENSION)))
if VECTOR_DIMENSION <= 0:
    raise ValueError("VECTOR_DIMENSION must be a positive integer.")
VECTOR_EMBEDDING_DIMENSION = int(os.getenv("VECTOR_EMBEDDING_DIMENSION", str(VECTOR_DIMENSION)))
if VECTOR_EMBEDDING_DIMENSION <= 0 or VECTOR_EMBEDDING_DIMENSION != VECTOR_DIMENSION:
    raise ValueError("VECTOR_EMBEDDING_DIMENSION must match the migrated VECTOR_DIMENSION; re-embedding requires an explicit schema migration.")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local_onnx").strip().lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL).strip()
if EMBEDDING_PROVIDER == "local" and (EMBEDDING_MODEL != LOCAL_EMBEDDING_MODEL
        or VECTOR_EMBEDDING_DIMENSION != LOCAL_EMBEDDING_DIMENSION):
    raise ValueError("The configured local embedding model requires its native 768-dimensional vector schema.")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "").strip()
LOCAL_ONNX_MODEL_DIR = Path(os.getenv(
    "LOCAL_ONNX_MODEL_DIR",
    str(_PROJECT_ROOT / "storage" / "models" / "intfloat" / "multilingual-e5-base"
        / "cea84a7cfcd78e04bc1ef6c7182a06fc72a22fbb"),
))
if not LOCAL_ONNX_MODEL_DIR.is_absolute():
    LOCAL_ONNX_MODEL_DIR = (_PROJECT_ROOT / LOCAL_ONNX_MODEL_DIR).resolve()
EMBEDDING_API_BASE_URL = os.getenv("EMBEDDING_API_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
GEMINI_EMBEDDING_BATCH_SIZE = int(os.getenv("GEMINI_EMBEDDING_BATCH_SIZE", "1"))
GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS = float(os.getenv("GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS", "1"))
GEMINI_BATCH_CHUNK_SIZE = int(os.getenv("GEMINI_BATCH_CHUNK_SIZE", "500"))
GEMINI_BATCH_POLL_SECONDS = int(os.getenv("GEMINI_BATCH_POLL_SECONDS", "60"))
EMBEDDING_MAX_RETRIES = int(os.getenv("EMBEDDING_MAX_RETRIES", "8"))
EMBEDDING_RETRY_BASE_SECONDS = float(os.getenv("EMBEDDING_RETRY_BASE_SECONDS", "5"))
EMBEDDING_RETRY_MAX_SECONDS = float(os.getenv("EMBEDDING_RETRY_MAX_SECONDS", "60"))
MAX_CHUNK_CHARACTERS = int(os.getenv("MAX_CHUNK_CHARACTERS", "3000"))
CHUNK_OVERLAP_CHARACTERS = int(os.getenv("CHUNK_OVERLAP_CHARACTERS", "300"))
MAX_CONTEXT_CHARACTERS = int(os.getenv("MAX_CONTEXT_CHARACTERS", "24000"))
SEMANTIC_WEIGHT = float(os.getenv("SEMANTIC_WEIGHT", "0.7"))
LEXICAL_WEIGHT = float(os.getenv("LEXICAL_WEIGHT", "0.3"))
if (EMBEDDING_BATCH_SIZE < 1 or GEMINI_EMBEDDING_BATCH_SIZE < 1 or GEMINI_BATCH_CHUNK_SIZE < 1
        or GEMINI_BATCH_POLL_SECONDS < 5 or not 1 <= EMBEDDING_MAX_RETRIES <= 10
        or not math.isfinite(GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS) or GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS < 0
        or not math.isfinite(EMBEDDING_RETRY_BASE_SECONDS) or EMBEDDING_RETRY_BASE_SECONDS <= 0
        or not math.isfinite(EMBEDDING_RETRY_MAX_SECONDS) or EMBEDDING_RETRY_MAX_SECONDS <= 0
        or MAX_CHUNK_CHARACTERS < 256 or not 0 <= CHUNK_OVERLAP_CHARACTERS < MAX_CHUNK_CHARACTERS):
    raise ValueError("Invalid embedding batch or chunk size configuration.")
if SEMANTIC_WEIGHT < 0 or LEXICAL_WEIGHT < 0 or SEMANTIC_WEIGHT + LEXICAL_WEIGHT <= 0:
    raise ValueError("Search weights must be nonnegative and have a positive sum.")
if EMBEDDING_TIMEOUT_SECONDS <= 0:
    raise ValueError("EMBEDDING_TIMEOUT_SECONDS must be positive.")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1200"))

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()
LOCAL_STORAGE_PATH = Path(os.getenv("LOCAL_STORAGE_PATH", str(_PROJECT_ROOT / "storage" / "documents")))
if not LOCAL_STORAGE_PATH.is_absolute():
    LOCAL_STORAGE_PATH = (_PROJECT_ROOT / LOCAL_STORAGE_PATH).resolve()
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(50 * 1024 * 1024)))
if MAX_UPLOAD_SIZE_BYTES <= 0:
    raise ValueError("MAX_UPLOAD_SIZE_BYTES must be a positive integer.")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
if not REDIS_URL.startswith(("redis://", "rediss://")):
    raise ValueError("REDIS_URL must use the redis:// or rediss:// scheme.")

MAX_SPREADSHEET_CELLS = int(os.getenv("MAX_SPREADSHEET_CELLS", "1000000"))
if MAX_SPREADSHEET_CELLS <= 0:
    raise ValueError("MAX_SPREADSHEET_CELLS must be a positive integer.")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256").strip()
JWT_ACCESS_TOKEN_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "30"))
