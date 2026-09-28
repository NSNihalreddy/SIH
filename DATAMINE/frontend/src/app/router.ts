import { useSyncExternalStore } from 'react'
const subscribe = (callback: () => void) => { window.addEventListener('popstate', callback); return () => window.removeEventListener('popstate', callback) }
export function usePath() { return useSyncExternalStore(subscribe, () => window.location.pathname + window.location.search, () => '/') }
export function navigate(path: string) { const current = window.location.pathname + window.location.search; if (current !== path) { window.history.pushState({}, '', path); window.dispatchEvent(new PopStateEvent('popstate')) } }
