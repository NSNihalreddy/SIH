import { apiRequest } from './client'

export interface Provenance {
  id?: string
  source_candidate_id?: string
  verification_id?: string
  verifier_id?: string
  source_document_id?: string
  document_version_id?: string
  page_id?: string
  page_number?: number
  content_type?: string
  table_id?: string
  cell_reference?: string
  [key: string]: unknown
}

export interface ProductionGroup {
  group_by: string
  key: string
  total: string
  unit: string | null
  record_count: number
  source_record_ids: string[]
  provenance: Provenance[]
}

export interface ProductionResponse {
  status: string
  items: ProductionGroup[]
  issues: unknown[]
  issue_count: number
  record_count: number
  total: number
  page: number
  page_size: number
  group_by: string
  filters: Record<string, unknown>
}

export interface TrendObservation {
  year: number
  value: number | string
  unit: string | null
  record_count: number
  source_record_ids: string[]
  provenance: Provenance[]
}
export interface TrendsResponse {
  status: string
  observations: TrendObservation[]
  period_changes: { from_year: number; to_year: number; change: number | string; change_percent: number | string | null; unit: string | null; source_record_ids: string[] }[]
  statistics: Record<string, unknown>
  cagr_percent: number | null
  cagr_method: string | null
  issues: unknown[]
}
export interface StatisticsResponse {
  status: string
  statistics: {
    count: number | null
    sum: number | string | null
    mean: number | string | null
    median: number | string | null
    minimum: number | string | null
    maximum: number | string | null
    standard_deviation: number | string | null
    percentile: number | string | { requested: string; value: string } | null
    unit: string | null
    method: string | null
  }
  source_record_ids: string[]
  provenance: Provenance[]
  issues: unknown[]
}
export interface AnomalyItem {
  [key: string]: unknown
  record_id?: string
  period?: string
  value?: number | string
  score?: number | string
  method?: string
  provenance?: Provenance[]
}
export interface AnomaliesResponse {
  status: string
  classification: string
  method: string
  threshold: number
  items: AnomalyItem[]
  issues: unknown[]
  interpretation: string
}
export interface ValidationItem {
  [key: string]: unknown
  id?: string
  status?: string
  created_at?: string
  validation_type?: string
  severity?: string
  compared_records?: string[]
  difference?: number | string | null
  detail?: string
  provenance?: unknown
}
export interface ValidationResponse {
  items: ValidationItem[]
  total: number
  page: number
  page_size: number
}
export interface CalculationRequest {
  calculation_type: string
  source_record_ids: string[]
  entity_type?: string
  entity_id?: string
  years?: number
}
export interface CalculationResponse {
  [key: string]: unknown
  status?: string
  result?: number | string | null
  value?: number | string | null
  calculation_id?: string
  formula_id?: string
  formula_version?: string
  provenance?: Provenance[]
}

export const getProductionAnalytics = (signal?: AbortSignal) => apiRequest<ProductionResponse>('/api/v1/analytics/production?group_by=year&page=1&page_size=200', {}, signal)
export const getTrendAnalytics = (signal?: AbortSignal) => apiRequest<TrendsResponse>('/api/v1/analytics/trends', {}, signal)
export const getStatisticsAnalytics = (signal?: AbortSignal) => apiRequest<StatisticsResponse>('/api/v1/analytics/statistics', {}, signal)
export const getAnomalyAnalytics = (signal?: AbortSignal) => apiRequest<AnomaliesResponse>('/api/v1/analytics/anomalies', {}, signal)
export const getValidationAnalytics = (signal?: AbortSignal) => apiRequest<ValidationResponse>('/api/v1/analytics/validations?page=1&page_size=50', {}, signal)
export const calculateDeterministically = (request: CalculationRequest, signal?: AbortSignal) => apiRequest<CalculationResponse>('/api/v1/analytics/calculate', { method: 'POST', body: JSON.stringify(request) }, signal)

// Retained for OverviewDashboard's existing summary request.
export const getProductionSummary = (signal?: AbortSignal) => apiRequest<ProductionResponse>('/api/v1/analytics/production?page=1&page_size=1', {}, signal)
export interface ValidationSummary { items: unknown[]; total: number; page: number; page_size: number }
export const getValidationSummary = (signal?: AbortSignal) => apiRequest<ValidationSummary>('/api/v1/analytics/validations?page=1&page_size=1', {}, signal)
