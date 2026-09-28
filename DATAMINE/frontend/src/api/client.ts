const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? '' : 'http://127.0.0.1:8000')).replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = 'ApiError' }
}

const safeStatusMessage = (status: number, isLogin: boolean) => {
  if (status === 401) return isLogin ? 'Username or password was not accepted.' : 'Your session has expired. Sign in again.'
  if (status === 403) return 'You do not have permission to access this resource.'
  if (status === 404) return 'The requested resource is not available.'
  if (status === 422) return 'Some submitted information is invalid. Review the fields and try again.'
  if (status === 429) return 'The service is receiving too many requests. Wait a moment and try again.'
  if (status >= 500) return 'The service encountered an error. Try again later.'
  return 'The request could not be completed.'
}

export async function apiRequest<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const token = sessionStorage.getItem('data-mine-token')
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal }) }
  catch (error) { if (error instanceof DOMException && error.name === 'AbortError') throw error; throw new ApiError('The API could not be reached.') }
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem('data-mine-token')
      window.dispatchEvent(new Event('data-mine:unauthorized'))
    }
    let message = safeStatusMessage(response.status, path.endsWith('/auth/token'))
    // HTTP 409 is used for review/business validation conflicts. Preserve the
    // backend's actionable reason so reviewers know what must be resolved.
    if (response.status === 409) {
      try {
        const payload: unknown = await response.json()
        if (payload && typeof payload === 'object' && 'detail' in payload
            && typeof payload.detail === 'string' && payload.detail.trim()) {
          message = payload.detail.trim()
        }
      } catch {
        // Retain the safe status message if the conflict body is not JSON.
      }
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  try { return await response.json() as T }
  catch { throw new ApiError('The service returned an unreadable response.') }
}

export async function apiBlobRequest(path: string, signal?: AbortSignal): Promise<Blob> {
  const token = sessionStorage.getItem('data-mine-token')
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try { response = await fetch(`${API_BASE_URL}${path}`, { headers, signal }) }
  catch (error) { if (error instanceof DOMException && error.name === 'AbortError') throw error; throw new ApiError('The API could not be reached.') }
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem('data-mine-token')
      window.dispatchEvent(new Event('data-mine:unauthorized'))
    }
    throw new ApiError(safeStatusMessage(response.status, false), response.status)
  }
  return response.blob()
}

export const apiUrl = (path: string) => `${API_BASE_URL}${path}`
