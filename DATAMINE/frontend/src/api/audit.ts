import { apiRequest } from './client'

export interface AuditEvent {
  id: string
  actor_id: string | null
  action: string
  entity_type: string
  entity_id: string | null
  details: Record<string, unknown> | null
  source: string | null
  created_at: string
}

export interface AuditEventResponse {
  items: AuditEvent[]
  total: number
  page: number
  page_size: number
  pages: number
}

export const getAuditEvents = (page = 1, pageSize = 50, signal?: AbortSignal) =>
  apiRequest<AuditEventResponse>(`/api/v1/audit?page=${page}&page_size=${pageSize}`, {}, signal)
