import { apiRequest } from './client'
import type { TokenResponse, User } from './types'

export async function login(username: string, password: string): Promise<TokenResponse> {
  const body = new URLSearchParams({ username, password })
  return apiRequest<TokenResponse>('/api/v1/auth/token', { method: 'POST', body, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
}
export const currentUser = (signal?: AbortSignal) => apiRequest<User>('/api/v1/auth/me', {}, signal)

export interface ViewerRegistrationRequest {
  username: string
  email: string
  full_name: string
  password: string
  confirm_password: string
}

export const registerViewer = (body: ViewerRegistrationRequest) =>
  apiRequest<{ id: string; username: string; role: 'VIEWER'; is_active: boolean; created_at: string }>(
    '/api/v1/auth/register',
    { method: 'POST', body: JSON.stringify(body) },
  )
