import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import { listViewerAccounts, setViewerAccountStatus, type ViewerAccount } from '../api/viewers'
import { Card, DataState, StatusBadge } from '../components/ui'
import './viewer-accounts.css'

const date = (value: string | null) => value ? new Date(value).toLocaleString() : '—'

export function ViewerAccounts() {
  const [items, setItems] = useState<ViewerAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const refresh = useCallback(() => setReload(value => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(null)
    listViewerAccounts(controller.signal)
      .then(result => { if (!controller.signal.aborted) setItems(result.items) })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.message : 'Viewer accounts could not be loaded.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [reload])

  async function toggle(account: ViewerAccount) {
    setBusyId(account.id); setNotice(null); setError(null)
    try {
      const updated = await setViewerAccountStatus(account.id, !account.is_active)
      setItems(current => current.map(item => item.id === updated.id ? updated : item))
      setNotice(`${updated.full_name || updated.username} is now ${updated.is_active ? 'enabled' : 'disabled'}.`)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Account status could not be changed.')
    } finally { setBusyId(null) }
  }

  return <Card title="Viewer Accounts" detail="Manage read-only workspace accounts">
    {notice && <p className="form-notice" role="status">{notice}</p>}
    {loading ? <DataState kind="loading" title="Loading Viewer accounts…"/> : error ? <DataState kind="error" detail={error} onRetry={refresh}/> : items.length === 0 ? <DataState kind="empty" title="No Viewer accounts registered" detail="Self-registered Viewer accounts will appear here."/> : <div className="table-wrap"><table className="viewer-account-table"><thead><tr><th>Name</th><th>Email / Username</th><th>Role</th><th>Status</th><th>Created</th><th>Last login</th><th>Action</th></tr></thead><tbody>{items.map(account => <tr key={account.id}><td>{account.full_name || '—'}</td><td><strong>{account.email}</strong><small className="viewer-account-username">{account.username}</small></td><td>{account.role}</td><td><StatusBadge status={account.is_active ? 'Active' : 'Disabled'}/></td><td>{date(account.created_at)}</td><td>{date(account.last_login)}</td><td><button type="button" className="button button-secondary button-small" disabled={busyId === account.id} onClick={() => void toggle(account)}>{busyId === account.id ? 'Saving…' : account.is_active ? 'Disable' : 'Enable'}</button></td></tr>)}</tbody></table></div>}
  </Card>
}
