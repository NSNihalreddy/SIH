import { useEffect, useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { navigate } from './router'
import { apiRequest } from '../api/client'
import { registerViewer } from '../api/auth'
import { Icon } from '../components/ui'

export function LoginPage() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [apiState, setApiState] = useState<'checking'|'ready'|'offline'>('checking')
  useEffect(() => { const controller = new AbortController(); apiRequest('/health', {}, controller.signal).then(() => setApiState('ready')).catch(() => setApiState('offline')); return () => controller.abort() }, [])
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      if (registering) {
        if (password !== confirmPassword) throw new Error('Passwords do not match.')
        if (password.length < 12) throw new Error('Password must be at least 12 characters.')
        const account = await registerViewer({ username, email, full_name: fullName, password, confirm_password: confirmPassword })
        if (account.role !== 'VIEWER') throw new Error('Registration did not create a Viewer account.')
        setRegistering(false); setNotice('Viewer account created. Sign in with your new credentials.')
        setPassword(''); setConfirmPassword('')
      } else {
        await signIn(username, password)
        const next = new URLSearchParams(window.location.search).get('next')
        navigate(next?.startsWith('/') && !next.startsWith('//') ? next : '/')
      }
    } catch (e) { setError(e instanceof Error ? e.message : registering ? 'Registration failed.' : 'Sign in failed.') }
    finally { setBusy(false) }
  }
  return <main className="login-screen"><section className="login-panel"><div className="brand-lockup"><div className="brand-mark"><span/><span/><span/></div><div><strong>DATA MINE</strong><small>INTELLIGENCE WORKSPACE</small></div></div><div className="login-copy"><div className="eyebrow">{registering ? 'VIEWER REGISTRATION' : 'SECURE ACCESS'}</div><h1>Geological &amp; Mining<br/>Intelligence Platform</h1><p>{registering ? 'Create a read-only Viewer account for evidence-led workspace access.' : 'Sign in with your DATA MINE account to access the workspace.'}</p></div><form onSubmit={submit} className="login-form">{registering && <><label>Full name<input autoComplete="name" value={fullName} onChange={e => setFullName(e.target.value)} required maxLength={200}/></label><label>Email<input type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required maxLength={320}/></label></>}<label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required minLength={3} maxLength={100}/></label><label>Password<input type="password" autoComplete={registering ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} required minLength={registering ? 12 : undefined} maxLength={256}/></label>{registering && <label>Confirm password<input type="password" autoComplete="new-password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} required minLength={12} maxLength={256}/></label>}{error && <p className="form-error" role="alert">{error}</p>}{notice && <p className="form-notice" role="status">{notice}</p>}<button className="button button-primary login-submit" disabled={busy}>{busy ? registering ? 'Creating account…' : 'Signing in…' : registering ? 'Create Viewer account' : 'Sign in'}{!registering && <Icon name="arrow"/>}</button></form><button className="text-link auth-mode-toggle" type="button" onClick={() => { setRegistering(value => !value); setError(''); setNotice('') }}>{registering ? 'Back to sign in' : 'Register as Viewer'}</button><div className="login-footer"><span className={`status-dot ${apiState}`}/><span>API {apiState === 'checking' ? 'status checking' : apiState === 'ready' ? 'available' : 'unavailable'}</span><span className="footer-separator">·</span><span>Access is managed by your organization</span></div></section><aside className="login-aside"><div className="contour contour-one"/><div className="contour contour-two"/><div className="aside-label">DATA INTELLIGENCE / 2026</div><div className="aside-content"><div className="geology-symbol"><i/><i/><i/><i/></div><p>One workspace for<br/>evidence-led decisions.</p></div><div className="aside-bottom">SOURCE-TRACEABLE &nbsp;·&nbsp; VERIFIED &nbsp;·&nbsp; AUDITABLE</div></aside></main>
}
