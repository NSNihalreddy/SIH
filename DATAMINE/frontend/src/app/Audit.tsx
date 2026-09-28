import { useEffect, useState } from 'react'
import { getAuditEvents, type AuditEventResponse } from '../api/audit'
import { Card, DataState, PageHeader } from '../components/ui'
import './audit.css'

export function AuditPage() {
  const [page, setPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: AuditEventResponse | null }>({ loading: true, error: null, data: null })

  useEffect(() => {
    const controller = new AbortController()
    setState(current => ({ ...current, loading: true, error: null }))
    getAuditEvents(page, 50, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ loading: false, error: null, data })
    }).catch(error => {
      if (!controller.signal.aborted) setState({ loading: false, error: error instanceof Error ? error.message : 'Audit history could not be loaded.', data: null })
    })
    return () => controller.abort()
  }, [page, reload])

  const data = state.data
  const errorKind = state.error?.includes('401') ? 'unauthorized' : state.error?.includes('403') ? 'forbidden' : 'error'
  return <main className="page-content audit-page">
    <PageHeader title="Audit" description="Read-only verification and platform activity history."
      action={<button className="button button-secondary" onClick={() => setReload(value => value + 1)}>Refresh</button>}/>
    <div className="audit-boundary"><span aria-hidden="true">i</span><div><strong>Append-only activity and verification history</strong><p>Verification actions are shown from the verification event stream; platform actions are shown from the audit log. Entries are read-only and retain their source references.</p></div></div>
    <Card title="Audit events" detail={data ? `${data.total} events · page ${data.page} of ${Math.max(1, data.pages)}` : 'Activity returned by the authenticated backend'}>
      {state.loading ? <DataState kind="loading"/> : state.error ? <DataState kind={errorKind} title="Audit history unavailable" detail={state.error} onRetry={() => setReload(value => value + 1)}/> : !data?.items.length ? <DataState kind="empty" title="No audit events returned" detail="The backend returned no activity records for this page."/> : <>
        <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Actor</th><th>Source / details</th></tr></thead><tbody>
          {data.items.map(item => <tr key={`${item.source}-${item.id}`}><td>{formatDate(item.created_at)}</td><td><strong>{item.action.replace(/_/g, ' ')}</strong></td><td><span>{item.entity_type.replace(/_/g, ' ')}</span>{item.entity_id && <code>{item.entity_id}</code>}</td><td><code>{item.actor_id ?? 'System / unavailable'}</code></td><td><span>{item.source ?? 'Source not recorded'}</span>{item.details && <details><summary>Details</summary><pre>{JSON.stringify(item.details, null, 2)}</pre></details>}</td></tr>)}
        </tbody></table></div>
        <div className="audit-pagination"><span>Showing {data.items.length} of {data.total}</span><div><button className="button button-secondary button-small" disabled={data.page <= 1} onClick={() => setPage(value => value - 1)}>Previous</button><button className="button button-secondary button-small" disabled={data.page >= data.pages} onClick={() => setPage(value => value + 1)}>Next</button></div></div>
      </>}
    </Card>
  </main>
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
