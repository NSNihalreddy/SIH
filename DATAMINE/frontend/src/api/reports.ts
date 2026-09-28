import { apiRequest } from './client'
import { apiBlobRequest } from './client'
import type { CollectionResponse, ReportRecord } from './types'

export interface ReportArtifact { artifact_type: 'PDF' | 'DOCX' | 'XLSX' | string; filename: string; mime_type: string; size_bytes: number; checksum_sha256?: string }
export interface ReportDetail extends ReportRecord {
  description?: string | null
  validation_status?: string | null
  error_message?: string | null
  parameters?: Record<string, unknown>
  source_document_ids?: string[]
  source_version_ids?: string[]
  sections?: Array<Record<string, unknown>>
  evidence_count?: number
  evidence?: Array<Record<string, unknown>>
  provenance?: Record<string, unknown> | unknown[]
  validation?: Record<string, unknown>
  artifacts?: ReportArtifact[]
}
export interface ReportCreateRequest {
  report_type: 'PRODUCTION' | 'DOCUMENT_INTELLIGENCE' | 'MULTI_DOCUMENT_COMPARISON' | 'EXECUTIVE_SUMMARY'
  title: string
  description?: string
  year?: number
  year_from?: number
  year_to?: number
  state?: string
  commodity?: string
  document_ids?: string[]
}
export interface ReportCreateResponse { report_id: string; status: string; submitted?: boolean; idempotent_reuse?: boolean }

export function listReports(signal?: AbortSignal): Promise<CollectionResponse<ReportDetail>>
export function listReports(page?: number, pageSize?: number, signal?: AbortSignal): Promise<CollectionResponse<ReportDetail>>
export function listReports(pageOrSignal: number | AbortSignal = 1, pageSize = 50, signal?: AbortSignal) {
  const page = typeof pageOrSignal === 'number' ? pageOrSignal : 1
  const requestSignal = typeof pageOrSignal === 'number' ? signal : pageOrSignal
  return apiRequest<CollectionResponse<ReportDetail>>(`/api/v1/reports?page=${page}&page_size=${pageSize}`, {}, requestSignal)
}
export const getReport = (reportId: string, signal?: AbortSignal) =>
  apiRequest<ReportDetail>(`/api/v1/reports/${encodeURIComponent(reportId)}`, {}, signal)
export const getReportEvidence = (reportId: string, signal?: AbortSignal) =>
  apiRequest<{ report_id: string; evidence: Array<Record<string, unknown>>; provenance: Record<string, unknown> }>(`/api/v1/reports/${encodeURIComponent(reportId)}/evidence`, {}, signal)
export const createReport = (body: ReportCreateRequest, idempotencyKey: string, signal?: AbortSignal) =>
  apiRequest<ReportCreateResponse>('/api/v1/reports', { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(body) }, signal)
export const downloadReportArtifact = (reportId: string, format: 'PDF' | 'DOCX' | 'XLSX', signal?: AbortSignal) =>
  apiBlobRequest(`/api/v1/reports/${encodeURIComponent(reportId)}/download?format=${format}`, signal)
