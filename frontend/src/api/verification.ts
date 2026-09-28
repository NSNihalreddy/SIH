import { apiRequest } from './client'
import type { VerificationCandidateDetail, VerificationConflictResponse, VerificationQueueResponse } from './types'
export type VerificationStatus = 'PENDING' | 'IN_REVIEW' | 'VERIFIED' | 'REJECTED' | 'UNRESOLVED'
export const getVerificationQueue = (
  status: VerificationStatus | undefined,
  signal?: AbortSignal,
  page = 1,
  pageSize = 50,
  search?: string,
) => {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_by: 'created_at',
    sort_order: 'asc',
  })

  if (status) query.set('verification_status', status)
  if (search?.trim()) query.set('search', search.trim())

  return apiRequest<VerificationQueueResponse>(
    `/api/v1/verification/queue?${query.toString()}`,
    {},
    signal,
  )
}
export const getVerificationCandidate = (candidateId: string, signal?: AbortSignal) =>
  apiRequest<VerificationCandidateDetail>(`/api/v1/verification/candidates/${encodeURIComponent(candidateId)}/provenance`, {}, signal)
export const claimCandidate = (candidateId: string, signal?: AbortSignal) =>
  apiRequest<Record<string, unknown>>(`/api/v1/verification/candidates/${encodeURIComponent(candidateId)}/claim`, { method: 'POST' }, signal)
export const approveCandidate = (candidateId: string, body: { reason: string; canonical_entity_id?: string }, signal?: AbortSignal) =>
  apiRequest<Record<string, unknown>>(`/api/v1/verification/candidates/${encodeURIComponent(candidateId)}/approve`, { method: 'POST', body: JSON.stringify(body) }, signal)
export const rejectCandidate = (candidateId: string, body: { reason: string }, signal?: AbortSignal) =>
  apiRequest<Record<string, unknown>>(`/api/v1/verification/candidates/${encodeURIComponent(candidateId)}/reject`, { method: 'POST', body: JSON.stringify(body) }, signal)
export const editAndApproveCandidate = (candidateId: string, body: { edited_value: Record<string, unknown>; reason: string; canonical_entity_id?: string }, signal?: AbortSignal) =>
  apiRequest<Record<string, unknown>>(`/api/v1/verification/candidates/${encodeURIComponent(candidateId)}/edit-and-approve`, { method: 'POST', body: JSON.stringify(body) }, signal)
export const markCandidateUnresolved = (candidateId: string, body: { reason: string }, signal?: AbortSignal) =>
  apiRequest<Record<string, unknown>>(`/api/v1/verification/candidates/${encodeURIComponent(candidateId)}/mark-unresolved`, { method: 'POST', body: JSON.stringify(body) }, signal)
export const listVerificationConflicts = (page = 1, pageSize = 50, signal?: AbortSignal) =>
  apiRequest<VerificationConflictResponse>(`/api/v1/verification/conflicts?page=${page}&page_size=${pageSize}`, {}, signal)
export const getVerificationConflict = (conflictId: string, signal?: AbortSignal) =>
  apiRequest<{ conflict: Record<string, unknown>; candidates: VerificationCandidateDetail[] }>(`/api/v1/verification/conflicts/${encodeURIComponent(conflictId)}`, {}, signal)
export const listCanonicalEntities = (entityType: string, signal?: AbortSignal) =>
  apiRequest<{ items: Array<{ id: string; entity_type: string; canonical_name: string; normalized_key: string }>; total: number; page: number; page_size: number }>(`/api/v1/canonical/entities?page=1&page_size=100&entity_type=${encodeURIComponent(entityType)}`, {}, signal)
export const resolveVerificationConflict = (conflictId: string, body: { status: 'RESOLVED' | 'ACCEPTED_AS_SOURCE_VARIATION'; resolution: string }, signal?: AbortSignal) =>
  apiRequest<Record<string, unknown>>(`/api/v1/verification/conflicts/${encodeURIComponent(conflictId)}/resolve`, { method: 'POST', body: JSON.stringify(body) }, signal)
