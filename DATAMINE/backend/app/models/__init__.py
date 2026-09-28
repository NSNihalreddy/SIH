from app.models.base import Base
from app.models.identity import AuditLog, Permission, Role, User, role_permissions, user_roles
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.processing import PROCESSING_STAGES, ProcessingJob, ProcessingStage
from app.models.intelligence import ExtractedContent, ExtractedTable, ExtractedTableCell
from app.models.geology import Borehole, BoreholeInterval, GeologicalMeasurement
from app.models.mining import Mine, MiningProject, ProductionRecord
from app.models.review import ValidationResult, VerificationRecord
from app.models.extraction import ExtractionCandidate
from app.models.canonical import CanonicalEntity, CanonicalRecord, MappingProposal, VerificationEvent, ExtractionConflict, ExtractionConflictCandidate
from app.models.rag import DocumentChunk, IndexingJob, SearchAudit
from app.models.analytics import AnalyticsCalculationResult, AnalyticsValidationResult, IntelligenceRun
from app.models.reports import Report, ReportArtifact
from app.models.trusted import (CoalBlock, GeologicalFormation, Seam, TrustedMine, TrustedProject, TrustedBorehole, TrustedGeologicalMeasurement, TrustedProductionRecord, TrustedCoordinate, CanonicalValueHistory, CanonicalDataConflict)
from app.models import audit_guard as _audit_guard

__all__ = [
    "Base", "User", "Role", "Permission", "AuditLog", "user_roles", "role_permissions",
    "Document", "DocumentVersion", "DocumentPage", "ProcessingJob", "ProcessingStage",
    "PROCESSING_STAGES", "ExtractedContent", "ExtractedTable", "ExtractedTableCell",
    "Borehole", "BoreholeInterval", "GeologicalMeasurement", "Mine", "MiningProject",
    "ProductionRecord", "ValidationResult", "VerificationRecord", "DocumentChunk", "IndexingJob", "SearchAudit", "ExtractionCandidate",
    "CanonicalEntity", "CanonicalRecord", "MappingProposal", "VerificationEvent", "ExtractionConflict", "ExtractionConflictCandidate",
    "CoalBlock", "GeologicalFormation", "Seam", "TrustedMine", "TrustedProject", "TrustedBorehole",
    "TrustedGeologicalMeasurement", "TrustedProductionRecord", "TrustedCoordinate", "CanonicalValueHistory", "CanonicalDataConflict",
    "AnalyticsCalculationResult", "AnalyticsValidationResult", "IntelligenceRun",
    "Report", "ReportArtifact",
]
