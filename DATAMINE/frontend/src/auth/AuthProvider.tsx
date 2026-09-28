import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import { currentUser, login as requestLogin } from '../api/auth'
import type { User } from '../api/types'

interface AuthValue { user: User | null; loading: boolean; signIn(username: string, password: string): Promise<void>; signOut(): void }
const AuthContext = createContext<AuthValue | null>(null)
export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const signOut = useCallback(() => { sessionStorage.removeItem('data-mine-token'); setUser(null) }, [])
  useEffect(() => {
    const token = sessionStorage.getItem('data-mine-token')
    if (!token) { setLoading(false); return }
    const controller = new AbortController()
    currentUser(controller.signal).then(value => { if (!controller.signal.aborted) setUser(value) }).catch(() => { if (!controller.signal.aborted) signOut() }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [signOut])
  useEffect(() => { window.addEventListener('data-mine:unauthorized', signOut); return () => window.removeEventListener('data-mine:unauthorized', signOut) }, [signOut])
  const signIn = useCallback(async (username: string, password: string) => {
    const response = await requestLogin(username, password)
    if (typeof response.access_token !== 'string' || !response.access_token || typeof response.token_type !== 'string' || response.token_type.toLowerCase() !== 'bearer') throw new Error('The sign-in response was not valid. Please try again.')
    sessionStorage.setItem('data-mine-token', response.access_token)
    try { setUser(await currentUser()) } catch (error) { signOut(); throw error }
  }, [signOut])
  const value = useMemo(() => ({ user, loading, signIn, signOut }), [user, loading, signIn, signOut])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error('useAuth must be used inside AuthProvider'); return value }
