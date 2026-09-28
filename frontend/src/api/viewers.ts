import { apiRequest } from './client'

export interface ViewerAccount {
  id: string
  username: string
  email: string
  full_name: string | null
  role: 'VIEWER'
  is_active: boolean
  created_at: string
  last_login: string | null
}

export interface ViewerAccountList {
  items: ViewerAccount[]
  total: number
}

export const listViewerAccounts = (signal?: AbortSignal) =>
  apiRequest<ViewerAccountList>('/api/v1/auth/users/viewers', {}, signal)

export const setViewerAccountStatus = (id: string, is_active: boolean) =>
  apiRequest<ViewerAccount>(`/api/v1/auth/users/viewers/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active }),
  })
