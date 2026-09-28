import { apiRequest } from './client'
import type { GisLayerSummary } from './types'

export interface GisGeometry {
  type: string
  coordinates: unknown
}
export interface GisFeature {
  type?: 'Feature'
  id: string
  entity_type?: string
  name?: string
  geometry: GisGeometry
  properties?: Record<string, unknown>
  provenance?: Record<string, unknown>
  distance_m?: number
  distance_unit?: string
}
export interface GisLayerResponse extends GisLayerSummary {
  type: 'FeatureCollection'
  features: GisFeature[]
  layers: { entity_type: string; count: number }[]
  limit: number
}
export interface GisEntityResponse {
  status: string
  items: GisFeature[]
  total: number
  page: number
  page_size: number
}
export interface GisSearchResponse {
  status: string
  items: GisFeature[]
  total?: number
  page?: number
  page_size?: number
  distance_unit?: string
  srid?: number
}
export interface GisRelationship {
  relation_type: string
  established_relationship: boolean
  borehole: { id: string; name: string | null; provenance: Record<string, unknown> }
  mine: { id: string; name: string | null; provenance: Record<string, unknown> }
  distance_m: number
  unit: string
}
export interface GisRelationshipResponse {
  status: string
  items: GisRelationship[]
  page: number
  page_size: number
  relationship_semantics: string
}
export interface GisDistanceResponse {
  status: string
  calculation_id?: string
  distance_m?: number
  unit?: string
  function?: string
  left?: { id: string; entity_type: string; name: string | null; geometry: GisGeometry; provenance: Record<string, unknown> }
  right?: { id: string; entity_type: string; name: string | null; geometry: GisGeometry; provenance: Record<string, unknown> }
}

export const listGisLayers = (signal?: AbortSignal) => apiRequest<GisLayerResponse>('/api/v1/gis/layers', {}, signal)
export const listGisEntities = (signal?: AbortSignal) => apiRequest<GisEntityResponse>('/api/v1/gis/entities?page=1&page_size=200', {}, signal)

export function findNearbyEntities(input: { latitude: number; longitude: number; radius_m: number; entity_type?: string }, signal?: AbortSignal) {
  const params = new URLSearchParams({ latitude: String(input.latitude), longitude: String(input.longitude), radius_m: String(input.radius_m), page: '1', page_size: '100' })
  if (input.entity_type) params.set('entity_type', input.entity_type)
  return apiRequest<GisSearchResponse>(`/api/v1/gis/nearby?${params}`, {}, signal)
}
export function findEntitiesInBounds(input: { west: number; south: number; east: number; north: number; entity_type?: string }, signal?: AbortSignal) {
  const params = new URLSearchParams({ west: String(input.west), south: String(input.south), east: String(input.east), north: String(input.north), page: '1', page_size: '100' })
  if (input.entity_type) params.set('entity_type', input.entity_type)
  return apiRequest<GisSearchResponse>(`/api/v1/gis/bbox?${params}`, {}, signal)
}
export function calculateEntityDistance(input: { left_type: string; left_id: string; right_type: string; right_id: string }, signal?: AbortSignal) {
  const params = new URLSearchParams(input)
  return apiRequest<GisDistanceResponse>(`/api/v1/gis/distance?${params}`, {}, signal)
}
export const findBoreholeMineCandidates = (radius_m: number, signal?: AbortSignal) => apiRequest<GisRelationshipResponse>(`/api/v1/gis/relationships?${new URLSearchParams({radius_m:String(radius_m),page:'1',page_size:'100'})}`, {}, signal)
