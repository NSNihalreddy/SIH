import { apiRequest } from './client'
import type { IntelligenceTopicsResponse } from './types'
export interface KeywordSummary { status: string; terms: { term?: string; keyword?: string; text?: string; score?: number; count?: number }[]; total: number; source_count: number; generated_at: string | null; run_id: string | null }
export const listTopics = (signal?: AbortSignal) => apiRequest<IntelligenceTopicsResponse>('/api/v1/intelligence/topics?page=1&page_size=1', {}, signal)
export const listKeywords = (signal?: AbortSignal) => apiRequest<KeywordSummary>('/api/v1/intelligence/wordcloud?page=1&page_size=1', {}, signal)
