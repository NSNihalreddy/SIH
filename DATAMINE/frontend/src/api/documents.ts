import { apiBlobRequest, apiRequest } from './client'
import type { DocumentPage, DocumentRecord, DocumentVersion, ExtractionCandidate, ProcessingJob, ProcessingStatus } from './types'
export const listDocuments = (page = 1, pageSize = 25, signal?: AbortSignal) => apiRequest<DocumentRecord[]>(`/api/v1/documents?offset=${(page - 1) * pageSize}&limit=${pageSize}`, {}, signal)
export const getDocument = (documentId: string, signal?: AbortSignal) => apiRequest<DocumentRecord>(`/api/v1/documents/${encodeURIComponent(documentId)}`, {}, signal)
export const listDocumentVersions = (documentId: string, signal?: AbortSignal) => apiRequest<DocumentVersion[]>(`/api/v1/documents/${encodeURIComponent(documentId)}/versions`, {}, signal)
export const uploadDocument = (file: File, signal?: AbortSignal) => { const body = new FormData(); body.append('file', file); return apiRequest<DocumentRecord>('/api/v1/documents/upload', { method: 'POST', body }, signal) }
export const getProcessingStatus = (documentId: string, signal?: AbortSignal) => apiRequest<ProcessingStatus>(`/api/v1/processing/${encodeURIComponent(documentId)}/processing-status`, {}, signal)
export const startDocumentProcessing = (documentId: string, signal?: AbortSignal) => apiRequest<ProcessingJob>(`/api/v1/processing/${encodeURIComponent(documentId)}/process`, { method: 'POST' }, signal)
export const getDocumentPages = (documentId: string, signal?: AbortSignal) => apiRequest<DocumentPage[]>(`/api/v1/processing/${encodeURIComponent(documentId)}/pages`, {}, signal)
export const listDocumentExtractions = (documentId: string, signal?: AbortSignal) => apiRequest<ExtractionCandidate[]>(`/api/v1/documents/${encodeURIComponent(documentId)}/extractions`, {}, signal)
export const runDocumentExtraction = (documentId: string, signal?: AbortSignal) => apiRequest<{ document_id: string; document_version_id: string; candidate_count: number; candidate_counts: Record<string, number>; unresolved_count: number; validation_failure_count: number; duration_seconds: number }>(`/api/v1/documents/${encodeURIComponent(documentId)}/extract`, { method: 'POST' }, signal)
export const getDocumentDownload = (documentId: string, signal?: AbortSignal) => apiBlobRequest(`/api/v1/documents/${encodeURIComponent(documentId)}/download`, signal)
