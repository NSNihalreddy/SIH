import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import { getAuditEvents, type AuditEvent } from '../api/audit'
import { getProductionSummary, type ProductionResponse } from '../api/analytics'
import { listDocuments, getProcessingStatus } from '../api/documents'
import { listGisLayers } from '../api/gis'
import type { DocumentRecord, ProcessingStatus, GisLayerSummary } from '../api/types'
import { Card, DataState, PageHeader, StatusBadge } from '../components/ui'
import { navigate } from './router'
import './overview.css'
import './viewer-overview.css'

type Resource<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string }
type ProcessingItem = { document: DocumentRecord; status: ProcessingStatus | null; message: string | null }
type TrustedSummary = { trusted_record_counts: Record<string, number> }

const empty = <T,>(): Resource<T> => ({ state: 'loading' })
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : 'The request could not be completed.'
const formatDate = (value: string) => new Date(value).toLocaleString()
const navigation = [
  { label: 'Query', detail: 'Search source evidence', path: '/query' },
  { label: 'Analytics', detail: 'Verified calculations', path: '/analytics' },
  { label: 'GIS', detail: 'Trusted spatial records', path: '/gis' },
  { label: 'Intelligence', detail: 'Topics and document insights', path: '/intelligence' },
  { label: 'Reports', detail: 'Evidence-backed reports', path: '/reports' },
  { label: 'Audit', detail: 'Provenance and activity', path: '/audit' },
]

export function ViewerOverview() {
  const [reload, setReload] = useState(0)
  const [documents, setDocuments] = useState<Resource<DocumentRecord[]>>(empty())
  const [processing, setProcessing] = useState<Resource<ProcessingItem[]>>(empty())
  const [trusted, setTrusted] = useState<Resource<TrustedSummary>>(empty())
  const [gis, setGis] = useState<Resource<GisLayerSummary>>(empty())
  const [production, setProduction] = useState<Resource<ProductionResponse>>(empty())
  const [activity, setActivity] = useState<Resource<AuditEvent[]>>(empty())

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    setDocuments(empty()); setProcessing(empty()); setTrusted(empty()); setGis(empty()); setProduction(empty()); setActivity(empty())
    void Promise.allSettled([
      listDocuments(1, 500, signal),
      apiRequest<TrustedSummary>('/api/v1/canonicalization/status', {}, signal),
      listGisLayers(signal),
      getProductionSummary(signal),
      getAuditEvents(1, 6, signal),
    ]).then(async ([documentsResult, trustedResult, gisResult, productionResult, activityResult]) => {
      if (signal.aborted) return
      if (documentsResult.status === 'fulfilled') {
        const found = documentsResult.value
        setDocuments({ state: 'ready', value: found })
        const checks: Array<ProcessingItem | null> = await Promise.all(found.slice(0, 5).map(async (document): Promise<ProcessingItem | null> => {
          try { return { document, status: await getProcessingStatus(document.id, signal), message: null } }
          catch (reason) {
            if (signal.aborted) return null
            if (reason instanceof ApiError && reason.status === 404) return { document, status: null, message: 'No processing job recorded' }
            return { document, status: null, message: errorText(reason) }
          }
        }))
        if (!signal.aborted) setProcessing({ state: 'ready', value: checks.filter((item): item is ProcessingItem => item !== null) })
      } else {
        setDocuments({ state: 'error', message: errorText(documentsResult.reason) })
        setProcessing({ state: 'ready', value: [] })
      }
      setTrusted(trustedResult.status === 'fulfilled' ? { state: 'ready', value: trustedResult.value } : { state: 'error', message: errorText(trustedResult.reason) })
      setGis(gisResult.status === 'fulfilled' ? { state: 'ready', value: gisResult.value } : { state: 'error', message: errorText(gisResult.reason) })
      setProduction(productionResult.status === 'fulfilled' ? { state: 'ready', value: productionResult.value } : { state: 'error', message: errorText(productionResult.reason) })
      setActivity(activityResult.status === 'fulfilled' ? { state: 'ready', value: activityResult.value.items } : { state: 'error', message: errorText(activityResult.reason) })
    })
    return () => controller.abort()
  }, [reload])

  const documentCount = documents.state === 'ready'
    ? documents.value.length === 500 ? '500+' : String(documents.value.length)
    : undefined
  const trustedCount = trusted.state === 'ready'
    ? String(Object.values(trusted.value.trusted_record_counts).reduce((total, count) => total + (Number.isFinite(count) ? count : 0), 0))
    : undefined
  const spatialCount = gis.state === 'ready'
    ? String(gis.value.layers.reduce((total, layer) => total + (Number.isFinite(layer.count) ? layer.count : 0), 0))
    : undefined
  const productionCount = production.state === 'ready' ? String(production.value.record_count) : undefined
  const processedRecent = processing.state === 'ready'
    ? `${processing.value.filter(item => item.status?.job.status === 'COMPLETED').length} / ${processing.value.length}`
    : undefined

  return <div className="page-content overview-page viewer-overview-page">
    <PageHeader title="Overview" description="Read-only summary of DATA MINE source and trusted data." action={<button className="button button-secondary" type="button" onClick={() => setReload(value => value + 1)}>Refresh data</button>}/>

    <section className="dashboard-section" aria-labelledby="viewer-summary-heading">
      <div className="dashboard-section-heading"><div><h2 id="viewer-summary-heading">Workspace summary</h2><p>Live counts and statuses returned by existing DATA MINE APIs.</p></div></div>
      <div className="viewer-summary-grid">
        <Metric title="Total documents" resource={documents} value={documentCount} detail={documents.state === 'ready' && documents.value.length === 500 ? 'At least 500; document API page limit reached.' : 'Count from the document list API.'}/>
        <Metric title="Processed documents" resource={processing} value={processedRecent} detail="Completed processing jobs among the five most recent documents checked; no global total is exposed."/>
        <Metric title="Verified / trusted records" resource={trusted} value={trustedCount} detail="Sum of trusted-record counts returned by canonicalization status."/>
        <Metric title="GIS / spatial records" resource={gis} value={spatialCount} detail={gis.state === 'ready' && gis.value.status === 'INSUFFICIENT_VERIFIED_SPATIAL_DATA' ? 'No verified spatial records are currently available.' : 'Verified spatial entity counts from GIS layers.'}/>
        <Metric title="Production records" resource={production} value={productionCount} detail={production.state === 'ready' && production.value.status === 'INSUFFICIENT_VERIFIED_DATA' ? 'Insufficient verified production data.' : 'Verified production record count from Analytics.'}/>
      </div>
    </section>

    <div className="viewer-overview-columns">
      <Card title="Recent document processing" detail="Status for up to five most recent source documents">
        {documents.state === 'loading' || processing.state === 'loading' ? <DataState kind="loading"/> : documents.state === 'error' ? <DataState kind="error" detail={documents.message} onRetry={() => setReload(value => value + 1)}/> : documents.value.length === 0 ? <DataState kind="empty" title="No documents available"/> : <div className="viewer-processing-list">{processing.state === 'ready' && processing.value.map(item => <div className="viewer-processing-row" key={item.document.id}><div><strong>{item.document.original_filename}</strong><small>{item.document.status}</small></div>{item.status ? <StatusBadge status={item.status.job.status}/> : <span className="muted">{item.message ?? 'Processing status unavailable'}</span>}</div>)}</div>}
      </Card>
      <Card title="Recent activity" detail="Latest audit events returned by the audit API">
        {activity.state === 'loading' ? <DataState kind="loading"/> : activity.state === 'error' ? <DataState kind="error" detail={activity.message} onRetry={() => setReload(value => value + 1)}/> : activity.value.length === 0 ? <DataState kind="empty" title="No activity available"/> : <div className="viewer-activity-list">{activity.value.map(event => <div className="viewer-activity-row" key={event.id}><div><strong>{event.action.replace(/_/g, ' ')}</strong><small>{event.entity_type}{event.entity_id ? ` · ${event.entity_id}` : ''}</small></div><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></div>)}</div>}
      </Card>
    </div>

    <section className="quick-actions viewer-quick-actions" aria-labelledby="viewer-quick-actions-heading"><div className="dashboard-section-heading"><div><h2 id="viewer-quick-actions-heading">Workspace sections</h2><p>Open a read-only area of the platform.</p></div></div><div className="quick-action-list">{navigation.map(item => <button key={item.path} className="quick-action" type="button" onClick={() => navigate(item.path)}><span><strong>{item.label}</strong><small>{item.detail}</small></span><span className="quick-action-arrow" aria-hidden="true">→</span></button>)}</div></section>
  </div>
}

function Metric({ title, resource, value, detail }: { title: string; resource: Resource<unknown>; value: string | undefined; detail: string }) {
  return <Card title={title}>
    {resource.state === 'loading' ? <DataState kind="loading" title="Loading live data…"/> : resource.state === 'error' ? <DataState kind="error" detail={resource.message}/> : value === undefined ? <DataState kind="empty" title="No data available"/> : <><strong className="viewer-metric-value">{value}</strong><p className="viewer-metric-detail">{detail}</p></>}
  </Card>
}
