from sqlalchemy import event

from app.models.canonical import VerificationEvent
from app.models.identity import AuditLog
from app.models.trusted import CanonicalValueHistory
from app.models.rag import SearchAudit
from app.models.analytics import AnalyticsCalculationResult, AnalyticsValidationResult


def _append_only(mapper, connection, target):
    raise ValueError(f"{target.__class__.__name__} is append-only")


for _model in (AuditLog, VerificationEvent, CanonicalValueHistory, SearchAudit,
               AnalyticsCalculationResult, AnalyticsValidationResult):
    event.listen(_model, "before_update", _append_only)
    event.listen(_model, "before_delete", _append_only)
