from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_documents import router as documents_router
from app.api.routes_processing import router as processing_router
from app.api.routes_extraction import router as extraction_router
from app.api.routes_verification import router as verification_router
from app.api.routes_auth import router as auth_router
from app.api.routes_canonicalization import router as canonicalization_router
from app.api.routes_search import router as search_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_gis import router as gis_router
from app.api.routes_intelligence import router as intelligence_router
from app.api.routes_reports import router as reports_router
from app.api.routes_audit import router as audit_router
from app.db.session import get_db

app = FastAPI(title="DATA MINE API")
app.include_router(documents_router)
app.include_router(processing_router)
app.include_router(extraction_router)
app.include_router(verification_router)
app.include_router(auth_router)
app.include_router(canonicalization_router)
app.include_router(search_router)
app.include_router(analytics_router)
app.include_router(gis_router)
app.include_router(intelligence_router)
app.include_router(reports_router)
app.include_router(audit_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
async def database_health(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database connection failed") from exc
    return {"status": "ok", "database": "connected"}
