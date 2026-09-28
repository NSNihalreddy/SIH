import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import { getProductionSummary, getValidationSummary, type ProductionResponse, type ValidationSummary } from '../api/analytics'
import { listDocuments, getProcessingStatus } from '../api/documents'
import { listGisLayers } from '../api/gis'
import { listKeywords, listTopics, type KeywordSummary } from '../api/intelligence'
import { listReports } from '../api/reports'
import { getVerificationQueue, type VerificationStatus } from '../api/verification'
import type { DocumentRecord, GisLayerSummary, ProcessingStatus, ReportRecord, VerificationQueueResponse, CollectionResponse, IntelligenceTopic } from '../api/types'
import { navigate } from './router'
import { useAuth } from '../auth/AuthProvider'
import { Card, DataState, DataTable, PageHeader, StatusBadge } from '../components/ui'
import './overview.css'

type Resource<T> = { phase: 'loading' } | { phase: 'error'; message: string; status?: number } | { phase: 'ready'; value: T }
type ProcessingItem = { document: DocumentRecord; processing: ProcessingStatus | null; error: string | null }
type VerificationCounts = Partial<Record<VerificationStatus, number>>

const loading = <T,>(): Resource<T> => ({ phase: 'loading' })
function failed<T>(error: unknown): Resource<T> { return { phase: 'error', message: error instanceof Error ? error.message : 'The request could not be completed.', status: error instanceof ApiError ? error.status : undefined } }
function ready<T>(value: T): Resource<T> { return { phase: 'ready', value } }
const verificationStatuses: VerificationStatus[] = ['PENDING', 'IN_REVIEW', 'VERIFIED', 'REJECTED', 'UNRESOLVED']

export function OverviewDashboard() {
  const { user } = useAuth()
  const [reload, setReload] = useState(0)
  const [api, setApi] = useState<Resource<{status:string}>>(loading())
  const [database, setDatabase] = useState<Resource<{status:string;database:string}>>(loading())
  const [documents, setDocuments] = useState<Resource<DocumentRecord[]>>(loading())
  const [processing, setProcessing] = useState<Resource<ProcessingItem[]>>(loading())
  const [verification, setVerification] = useState<Resource<VerificationCounts>>(loading())
  const [production, setProduction] = useState<Resource<ProductionResponse>>(loading())
  const [validations, setValidations] = useState<Resource<ValidationSummary>>(loading())
  const [gis, setGis] = useState<Resource<GisLayerSummary>>(loading())
  const [topics, setTopics] = useState<Resource<CollectionResponse<IntelligenceTopic>>>(loading())
  const [keywords, setKeywords] = useState<Resource<KeywordSummary>>(loading())
  const [reports, setReports] = useState<Resource<CollectionResponse<ReportRecord>>>(loading())

  useEffect(() => {
    const controller = new AbortController()
    const signal = controller.signal
    setApi(loading()); setDatabase(loading()); setDocuments(loading()); setProcessing(loading())
    setVerification(loading()); setProduction(loading()); setValidations(loading()); setGis(loading())
    setTopics(loading()); setKeywords(loading()); setReports(loading())

    void Promise.allSettled([
      fetchHealth(signal), fetchDatabaseHealth(signal), listDocuments(1, 4, signal),
      Promise.all(verificationStatuses.map(status => getVerificationQueue(status, signal))),
      getProductionSummary(signal), getValidationSummary(signal), listGisLayers(signal),
      listTopics(signal), listKeywords(signal), listReports(signal),
    ]).then(async ([apiResult, dbResult, documentResult, verificationResult, productionResult, validationResult, gisResult, topicsResult, keywordsResult, reportsResult]) => {
      if (signal.aborted) return
      setApi(apiResult.status === 'fulfilled' ? ready(apiResult.value) : failed(apiResult.reason))
      setDatabase(dbResult.status === 'fulfilled' ? ready(dbResult.value) : failed(dbResult.reason))
      setDocuments(documentResult.status === 'fulfilled' ? ready(documentResult.value) : failed(documentResult.reason))
      setVerification(verificationResult.status === 'fulfilled'
        ? ready(Object.fromEntries(verificationStatuses.map((status, index) => [status, verificationResult.value[index].total])) as VerificationCounts)
        : failed(verificationResult.reason))
      setProduction(productionResult.status === 'fulfilled' ? ready(productionResult.value) : failed(productionResult.reason))
      setValidations(validationResult.status === 'fulfilled' ? ready(validationResult.value) : failed(validationResult.reason))
      setGis(gisResult.status === 'fulfilled' ? ready(gisResult.value) : failed(gisResult.reason))
      setTopics(topicsResult.status === 'fulfilled' ? ready(topicsResult.value) : failed(topicsResult.reason))
      setKeywords(keywordsResult.status === 'fulfilled' ? ready(keywordsResult.value) : failed(keywordsResult.reason))
      setReports(reportsResult.status === 'fulfilled' ? ready(reportsResult.value) : failed(reportsResult.reason))

      if (documentResult.status === 'fulfilled') {
        const recent = documentResult.value.slice(0, 4)
        const jobs = await Promise.all(recent.map(async document => {
          try { return { document, processing: await getProcessingStatus(document.id, signal), error: null } }
          catch (error) {
            if (signal.aborted) return { document, processing: null, error: null }
            if (error instanceof ApiError && error.status === 404) return { document, processing: null, error: null }
            return { document, processing: null, error: error instanceof Error ? error.message : 'Processing status is unavailable' }
          }
        }))
        if (!signal.aborted) setProcessing(ready(jobs))
      } else if (!signal.aborted) setProcessing(failed(documentResult.reason))
    })
    return () => controller.abort()
  }, [reload])

  const isReviewer = user?.role === 'ADMIN' || user?.role === 'VERIFIER'
  return <div className="page-content overview-page">
    <PageHeader title="Overview" description="Geological, Mining & Production Intelligence" action={<button className="button button-secondary" onClick={() => setReload(value => value + 1)}>Refresh data</button>}/>

    <section className="dashboard-section" aria-labelledby="system-status-heading">
      <div className="dashboard-section-heading"><div><h2 id="system-status-heading">System status</h2><p>Live checks and capability readiness from current APIs.</p></div></div>
      <div className="status-grid">
        <HealthCard title="API" detail="GET /health" resource={api} value={value => value.status === 'ok' ? 'Available' : 'Unexpected status'} tone={value => value.status === 'ok' ? 'good' : 'bad'}/>
        <HealthCard title="Database" detail="Live PostgreSQL check" resource={database} value={value => value.status === 'ok' && value.database === 'connected' ? 'Available' : 'Unexpected status'} tone={value => value.status === 'ok' && value.database === 'connected' ? 'good' : 'bad'}/>
        <StaticHealth title="Document processing" detail="Status is reported per document, not as a service-wide health check." status="Not globally exposed" tone="neutral"/>
        <StaticHealth title="Search & RAG" detail="Routes exist; provider readiness has no health endpoint." status="Readiness not exposed" tone="neutral"/>
      </div>
    </section>

    <div className="dashboard-columns">
      <div className="dashboard-main-column">
        <Card title="Document intelligence" detail="Latest source documents and their recorded processing state">
          <ResourceState resource={documents} retry={() => setReload(value => value + 1)} emptyTitle="No source documents are available."/>
          {documents.phase === 'ready' && documents.value.length > 0 && <DataTable rowKey="id" rows={documents.value.map(document => ({...document, name: document.original_filename, created: formatDate(document.created_at)}))} columns={[{key:'name',label:'Source document'},{key:'status',label:'Document status',render:row=><StatusBadge status={String(row.status)}/>},{key:'created',label:'Added'}]}/>}
          {documents.phase === 'ready' && <div className="processing-summary"><h3>Processing activity</h3><ResourceState resource={processing} retry={() => setReload(value => value + 1)} emptyTitle="No recent documents to check."/>{processing.phase === 'ready' && processing.value.length > 0 && <div className="processing-list">{processing.value.map(item => <div className="processing-row" key={item.document.id}><span className="processing-name">{item.document.original_filename}</span>{item.error ? <span className="muted">{item.error}</span> : item.processing ? <StatusBadge status={item.processing.job.status}/> : <span className="muted">No processing job recorded</span>}</div>)}</div>}</div>}
          <div className="dashboard-card-footer"><span className="muted">The document endpoint does not expose a total count.</span><button className="text-link" onClick={() => navigate('/documents')}>Open documents <span aria-hidden="true">→</span></button></div>
        </Card>

        <Card title="Verification workload" detail="Extraction candidates are not trusted records.">
          <ResourceState resource={verification} retry={() => setReload(value => value + 1)} emptyTitle="Verification totals are unavailable."/>
          {verification.phase === 'ready' && <><div className="verification-counts">{verificationStatuses.map(status => <div className="verification-count" key={status}><span>{statusLabel(status)}</span><strong>{formatCount(verification.value[status])}</strong></div>)}</div>{(verification.value.PENDING ?? 0) > 0 && verification.value.VERIFIED === 0 && <p className="trust-distinction">Pending extraction candidates exist; there are no verified candidates in the current queue.</p>}</>}
          <div className="verified-record-line"><span>Trusted production data</span><ProductionValue resource={production}/></div>
          <div className="dashboard-card-footer"><span className="muted">Only verified canonical records feed production analytics.</span>{isReviewer && <button className="text-link" onClick={() => navigate('/verification')}>Open verification <span aria-hidden="true">→</span></button>}</div>
        </Card>

        <Card title="Analytics status" detail="Production summaries use verified records only.">
          <ResourceError resource={production} retry={() => setReload(value => value + 1)}/><ResourceError resource={validations} retry={() => setReload(value => value + 1)}/>
          <div className="analytics-status-list"><div className="analytics-status-item"><span>Production analytics</span><ProductionStatus resource={production}/></div><div className="analytics-status-item"><span>Validation results</span><ValidationValue resource={validations}/></div><div className="analytics-capabilities"><span>Trends</span><span>Statistics</span><span>Anomalies</span><small>Live calculation status is available in Analytics; the dashboard avoids repeating full-record queries.</small></div></div>
          <div className="dashboard-card-footer"><span className="muted">No production value is inferred from missing verified records.</span><button className="text-link" onClick={() => navigate('/analytics')}>Open analytics <span aria-hidden="true">→</span></button></div>
        </Card>

        <Card title="Intelligence" detail="Latest available topic and keyword analysis">
          <ResourceError resource={topics} retry={() => setReload(value => value + 1)}/><ResourceError resource={keywords} retry={() => setReload(value => value + 1)}/>
          <div className="intelligence-summary"><div><span>Topic analysis</span><IntelligenceStatus resource={topics}/></div><div><span>Topics</span><ResourceCount resource={topics} count={value => value.total} unavailable="Not available"/></div><div><span>Keywords</span><ResourceCount resource={keywords} count={value => value.total} unavailable="Not available"/></div><div><span>Source documents</span><ResourceCount resource={keywords} count={value => value.source_count} unavailable="Not available"/></div></div>
          <IntelligenceRunLine topics={topics} keywords={keywords}/>
          <div className="dashboard-card-footer"><span className="muted">Counts and run identifiers come from the intelligence API.</span><button className="text-link" onClick={() => navigate('/intelligence')}>Open intelligence <span aria-hidden="true">→</span></button></div>
        </Card>
      </div>

      <aside className="dashboard-side-column">
        <Card title="GIS status" detail="Spatial entity availability from PostGIS-backed API">
          <ResourceState resource={gis} retry={() => setReload(value => value + 1)} emptyTitle="Spatial summary is unavailable."/>
          {gis.phase === 'ready' && (gis.value.status === 'INSUFFICIENT_VERIFIED_SPATIAL_DATA' ? <DataState kind="insufficient" title="INSUFFICIENT VERIFIED SPATIAL DATA" detail="No trusted spatial entities were returned by the API."/> : gis.value.layers.length === 0 ? <DataState kind="empty" title="No spatial layers are available."/> : <div className="gis-layer-list">{gis.value.layers.map(layer => <div key={layer.entity_type}><span>{humanize(layer.entity_type)}</span><strong>{formatCount(layer.count)}</strong></div>)}</div>)}
          <div className="dashboard-card-footer"><span className="muted">Dashboard displays layer counts only; no map features are fabricated.</span><button className="text-link" onClick={() => navigate('/gis')}>Open GIS <span aria-hidden="true">→</span></button></div>
        </Card>

        <Card title="Recent reports" detail="Latest reports returned by the API">
          <ResourceState resource={reports} retry={() => setReload(value => value + 1)} emptyTitle="No reports are available."/>
          {reports.phase === 'ready' && reports.value.items.length > 0 && <div className="report-list">{reports.value.items.map(report => <div className="report-row" key={report.report_id}><div className="report-title"><strong>{report.title}</strong><span>{humanize(report.report_type)} · {formatDate(report.created_at)}</span></div><div className="report-meta"><StatusBadge status={report.status}/><span className="evidence-count">{report.evidence_count === undefined ? 'Evidence status unavailable' : report.evidence_count > 0 ? `${report.evidence_count} evidence references` : 'No evidence references'}</span></div></div>)}</div>}
          <div className="dashboard-card-footer"><span className="muted">No reports are generated by the dashboard.</span><button className="text-link" onClick={() => navigate('/reports')}>Open reports <span aria-hidden="true">→</span></button></div>
        </Card>

        <Card title="Recent activity" detail="Source-backed system activity">
          <DataState kind="empty" title="Recent activity is unavailable" detail="The current backend API does not expose a unified activity feed. No events or timestamps are inferred."/>
        </Card>

        <Card title="Data trust" detail="How source material becomes trusted intelligence">
          <div className="trust-flow" aria-label="Source documents flow through extraction, validation, human verification, trusted knowledge, analytics, GIS, and reports.">{['Source documents','Extraction','Validation','Human verification','Trusted knowledge','Analytics · GIS · Reports'].map((step,index)=><div className="trust-step" key={step}><span>{step}</span>{index < 5 && <i aria-hidden="true">↓</i>}</div>)}</div>
          <p className="trust-statement">Unverified extraction is not treated as authoritative data.</p>
        </Card>
      </aside>
    </div>

    <section className="quick-actions" aria-labelledby="quick-actions-heading"><div className="dashboard-section-heading"><div><h2 id="quick-actions-heading">Quick actions</h2><p>Navigate to a workspace module.</p></div></div><div className="quick-action-list"><QuickAction label="Upload document" detail="Documents" path="/documents"/><QuickAction label="Open Query Console" detail="Evidence search" path="/query"/><QuickAction label="Open Documents" detail="Source material" path="/documents"/>{isReviewer && <QuickAction label="Open Verification" detail="Review candidates" path="/verification"/>}<QuickAction label="Open Analytics" detail="Verified calculations" path="/analytics"/><QuickAction label="Open GIS" detail="Spatial intelligence" path="/gis"/><QuickAction label="Open Reports" detail="Evidence-backed outputs" path="/reports"/></div></section>
  </div>
}

async function fetchHealth(signal: AbortSignal) { return apiRequest<{status:string}>('/health', {}, signal) }
async function fetchDatabaseHealth(signal: AbortSignal) { return apiRequest<{status:string;database:string}>('/health/db', {}, signal) }
function HealthCard<T extends {status:string}>({ title, detail, resource, value, tone }: { title:string; detail:string; resource:Resource<T>; value(data:T):string; tone(data:T):'good'|'bad' }) {
  return <div className="system-status-card"><div className="system-status-heading"><span>{title}</span>{resource.phase === 'ready' ? <span className={`availability-dot ${tone(resource.value)}`} aria-hidden="true"/> : <span className={`availability-dot ${resource.phase === 'error' ? 'bad' : 'checking'}`} aria-hidden="true"/>}</div><p>{detail}</p>{resource.phase === 'loading' ? <DataState kind="loading"/> : resource.phase === 'error' ? <StatusBadge status={resource.status === 403 ? 'FORBIDDEN' : 'ERROR'}/> : <StatusBadge status={value(resource.value)}/>}</div>
}
function StaticHealth({ title, detail, status, tone }: {title:string;detail:string;status:string;tone:'neutral'}) { return <div className="system-status-card"><div className="system-status-heading"><span>{title}</span><span className={`availability-dot ${tone}`} aria-hidden="true"/></div><p>{detail}</p><StatusBadge status={status}/></div> }
function ResourceState<T>({ resource, retry, emptyTitle }: {resource:Resource<T>;retry():void;emptyTitle:string}) {
  if (resource.phase === 'loading') return <DataState kind="loading"/>
  if (resource.phase === 'error') return <DataState kind={resource.status === 401 ? 'unauthorized' : resource.status === 403 ? 'forbidden' : 'error'} detail={resource.message} onRetry={retry}/>
  if (Array.isArray(resource.value) && resource.value.length === 0) return <DataState kind="empty" title={emptyTitle}/>
  if (resource.value && typeof resource.value === 'object' && 'items' in resource.value && Array.isArray(resource.value.items) && resource.value.items.length === 0) return <DataState kind="empty" title={emptyTitle}/>
  return null
}
function ResourceError<T>({ resource, retry }: {resource:Resource<T>;retry():void}) {
  if (resource.phase !== 'error') return null
  return <DataState kind={resource.status === 401 ? 'unauthorized' : resource.status === 403 ? 'forbidden' : 'error'} detail={resource.message} onRetry={retry}/>
}
function ProductionStatus({ resource }: {resource:Resource<ProductionResponse>}) {
  if (resource.phase === 'loading') return <span className="muted">Loading</span>
  if (resource.phase === 'error') return <StatusBadge status={resource.status === 403 ? 'FORBIDDEN' : 'ERROR'}/>
  return <StatusBadge status={resource.value.status}/>
}
function ProductionValue({ resource }: {resource:Resource<ProductionResponse>}) {
  if (resource.phase === 'loading') return <span className="muted">Loading</span>
  if (resource.phase === 'error') return <span className="muted">Unavailable</span>
  return resource.value.status === 'INSUFFICIENT_VERIFIED_DATA' ? <span className="insufficient-text">Insufficient verified data</span> : <strong>{formatCount(resource.value.record_count)} verified production records</strong>
}
function ValidationValue({ resource }: {resource:Resource<ValidationSummary>}) {
  if (resource.phase === 'loading') return <span className="muted">Loading</span>
  if (resource.phase === 'error') return <StatusBadge status={resource.status === 401 ? 'UNAUTHORIZED' : resource.status === 403 ? 'FORBIDDEN' : 'ERROR'}/>
  return <span>{resource.value.total > 0 ? `${formatCount(resource.value.total)} recorded` : 'No validation results recorded'}</span>
}
function IntelligenceStatus({ resource }: {resource:Resource<CollectionResponse<IntelligenceTopic>>}) {
  if (resource.phase === 'loading') return <span className="muted">Loading</span>
  if (resource.phase === 'error') return <StatusBadge status={resource.status === 401 ? 'UNAUTHORIZED' : resource.status === 403 ? 'FORBIDDEN' : 'ERROR'}/>
  return <StatusBadge status={resource.value.status ?? (resource.value.total ? 'AVAILABLE' : 'NOT_READY')}/>
}
function ResourceCount<T>({ resource, count, unavailable }: {resource:Resource<T>;count(value:T):number | undefined;unavailable:string}) {
  if (resource.phase === 'loading') return <strong className="muted">—</strong>
  if (resource.phase === 'error') return <span className="muted">{unavailable}</span>
  return <strong>{count(resource.value) === undefined ? unavailable : formatCount(count(resource.value))}</strong>
}
function IntelligenceRunLine({ topics, keywords }: {topics:Resource<CollectionResponse<IntelligenceTopic>>;keywords:Resource<KeywordSummary>}) {
  if (topics.phase === 'error' || keywords.phase === 'error') return <p className="muted">Run metadata unavailable from one or more intelligence APIs.</p>
  if (topics.phase !== 'ready' || keywords.phase !== 'ready') return <p className="muted">Loading latest run metadata…</p>
  const runId = keywords.value.run_id ?? ('run_id' in topics.value ? String(topics.value.run_id ?? '') : '')
  const generatedAt = keywords.value.generated_at
  if (!runId && !generatedAt && topics.value.status === 'NOT_READY') return <DataState kind="empty" title="No completed intelligence run is available."/>
  return <p className="run-metadata">{runId ? `Run ${runId}` : 'Run identifier unavailable'}{generatedAt ? ` · Completed ${formatDate(generatedAt)}` : ''}{keywords.value.source_count >= 0 ? ` · ${formatCount(keywords.value.source_count)} source documents` : ''}</p>
}
function QuickAction({label,detail,path}:{label:string;detail:string;path:string}) { return <button className="quick-action" onClick={()=>navigate(path)}><span><strong>{label}</strong><small>{detail}</small></span><span className="quick-action-arrow" aria-hidden="true">→</span></button> }
function statusLabel(value: VerificationStatus) { return value === 'IN_REVIEW' ? 'In review' : value[0] + value.slice(1).toLowerCase() }
function formatCount(value: number | undefined) { return value === undefined || !Number.isFinite(value) ? 'Not available' : new Intl.NumberFormat().format(value) }
function formatDate(value?: string | null) { if (!value) return 'Date unavailable'; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? 'Date unavailable' : new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(parsed) }
function humanize(value: string) { return value.toLowerCase().replace(/_/g,' ').replace(/\b\w/g, letter=>letter.toUpperCase()) }
