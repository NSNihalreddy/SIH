export type UserRole = 'ADMIN' | 'VERIFIER' | 'ANALYST' | 'VIEWER'
export interface User { id: string | number; username: string; role: UserRole; is_active: boolean }
export interface TokenResponse { access_token: string; token_type: string; expires_in?: number }
export interface HealthResponse { status?: string; message?: string }
export interface CollectionResponse<T> { items: T[]; total?: number; status?: string; record_count?: number; page?: number; page_size?: number }
export interface DocumentRecord { id: string; original_filename: string; document_type: string | null; mime_type: string | null; file_size: number; sha256_checksum: string | null; status: string; created_at: string; updated_at: string }
export interface DocumentVersion { id: string; document_id: string; version_number: number; sha256_checksum: string | null; file_size: number; created_at: string; created_by: string | null }
export interface ProcessingJob { id: string; document_version_id: string; status: string; processor: string | null; created_at: string; updated_at: string; started_at: string | null; completed_at: string | null; error_message: string | null }
export interface ProcessingStage { id: string; stage: string; status: string; created_at: string; started_at: string | null; completed_at: string | null; error_message: string | null }
export interface ProcessingStatus { job: ProcessingJob; stages: ProcessingStage[] }
export interface ExtractedCell { id: string; row_index: number; column_index: number; raw_value: string | null; normalized_value: string | null; confidence: number | string | null; cell_metadata: Record<string, unknown> | null }
export interface ExtractedTable { id: string; table_order: number; extraction_method: string | null; confidence: number | string | null; table_metadata: Record<string, unknown> | null; cells: ExtractedCell[] }
export interface ExtractedContent { id: string; content_type: string; text: string; bounding_box: Record<string, unknown> | null; confidence: number | string | null; extraction_metadata: Record<string, unknown> | null }
export interface DocumentPage { id: string; document_version_id: string; page_number: number | null; text: string | null; page_metadata: Record<string, unknown> | null; created_at: string; contents: ExtractedContent[]; tables: ExtractedTable[] }
export interface ExtractionCandidate { id: string; document_id: string; document_version_id: string; source_page_id: string | null; source_content_id: string | null; source_table_id: string | null; source_cell_id: string | null; candidate_type: string; raw_text: string; raw_value: string | null; normalized_value: string | null; normalized_numeric_value: number | string | null; unit: string | null; extraction_confidence: number | string | null; verification_status: string; match_status: string; validation_results: unknown[]; candidate_metadata: Record<string, unknown>; created_at: string }
export interface IntelligenceEvidence {
  document_id: string
  document_version_id: string
  page_id: string | null
  page_number: number | null
  source_unit_id: string | null
  chunk_id: string
  evidence_type: string
}
export interface IntelligenceTopic {
  topic_id: string
  name: string
  frequency: number
  confidence: number
  document_count: number
  evidence: IntelligenceEvidence[]
  distribution: Array<{ document_id: string; frequency: number }>
}
export interface IntelligenceTopicsResponse extends CollectionResponse<IntelligenceTopic> { run_id?: string }
export interface GisLayer { entity_type: string; count: number }
export interface GisLayerSummary { status: string; layers: GisLayer[]; features?: unknown[] }
export interface ReportRecord { report_id: string; title: string; report_type: string; description?: string | null; status: string; validation_status?: string | null; evidence_count?: number; source_document_ids?: string[]; source_version_ids?: string[]; parameters?: Record<string, unknown>; sections?: Array<Record<string, unknown>>; provenance?: Record<string, unknown> | unknown[]; validation?: Record<string, unknown>; error_message?: string | null; artifacts?: Array<{artifact_type:string;filename:string;mime_type:string;size_bytes:number;checksum_sha256?:string}>; created_at: string; completed_at?: string | null }
export interface VerificationQueueResponse { items: VerificationRecord[]; total: number; page: number; page_size: number }
export interface VerificationCandidate { id: string; document_id: string; document_version_id: string; candidate_type: string; raw_text: string; raw_value: string | null; normalized_value: string | null; normalized_numeric_value: number | string | null; unit: string | null; extraction_confidence: number | string | null; verification_status: string; classification?: string; classification_status?: string; classification_confidence?: number | string | null; classification_rule?: string | null; match_status: string; mapping_status?: string; source_page_id: string | null; source_content_id: string | null; source_table_id: string | null; source_cell_id: string | null; candidate_metadata: Record<string, unknown>; validation_results: unknown[]; created_at?: string; claimed_by?: string | null; claimed_at?: string | null }
export interface VerificationSource { document_id?: string; document_name?: string | null; document_version_id?: string; version_number?: number | null; page_id?: string | null; page_number?: number | null; page_text?: string | null; source_content_id?: string | null; source_text?: string | null; source_table_id?: string | null; source_cell_id?: string | null; source_cell?: {row_index?:number;column_index?:number;raw_value?:string|null}|null; raw_extracted_text?: string | null; source_unit_id?: string | null; [key: string]: unknown }
export interface VerificationCandidateDetail { candidate: VerificationCandidate; source: VerificationSource; mapping_proposal?: Record<string, unknown> | null; conflicts?: Array<Record<string, unknown>>; verification_events?: Array<Record<string, unknown>>; canonical_record?: Record<string, unknown> | null; classification?: string; classification_status?: string; mapping_status?: string; verification_status?: string; validation_results?: unknown[] }
export interface VerificationRecord extends VerificationCandidateDetail { verification_status: 'PENDING' | 'IN_REVIEW' | 'VERIFIED' | 'REJECTED' | 'UNRESOLVED'; classification?: string; classification_status?: string; mapping_status?: string }
export interface VerificationConflictResponse { items: Array<{ conflict: Record<string, unknown>; candidate_ids: string[] }>; total: number; page: number; page_size: number }
