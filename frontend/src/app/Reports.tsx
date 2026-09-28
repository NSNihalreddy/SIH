import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { listDocuments } from '../api/documents'
import { ApiError } from '../api/client'
import { createReport, downloadReportArtifact, getReport, getReportEvidence, listReports, type ReportCreateRequest, type ReportDetail } from '../api/reports'
import type { DocumentRecord } from '../api/types'
import { Card, DataState, PageHeader, StatusBadge } from '../components/ui'
import './reports.css'

const reportTypes = [
  { value: 'PRODUCTION', label: 'Production', description: 'Deterministic summary of verified production records.' },
  { value: 'DOCUMENT_INTELLIGENCE', label: 'Document Intelligence', description: 'Evidence and findings from selected indexed documents.' },
  { value: 'MULTI_DOCUMENT_COMPARISON', label: 'Multi-document Comparison', description: 'Compare evidence across at least two distinct documents.' },
  { value: 'EXECUTIVE_SUMMARY', label: 'Executive Summary', description: 'Evidence-led overview with verified-data limitations.' },
] as const
type ReportType = typeof reportTypes[number]['value']
type RequestState = { loading: boolean; error: string | null }
const formatDate = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
const formatBytes = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(0)} KB` : `${(value / (1024 * 1024)).toFixed(1)} MB`
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const typeLabel = (value: string) => reportTypes.find(item => item.value === value)?.label ?? value.replace(/_/g, ' ')
const errorMessage = (error: unknown) => error instanceof ApiError ? `${error.message}${error.status ? ` (HTTP ${error.status})` : ''}` : error instanceof Error ? error.message : 'The request could not be completed.'

export function ReportsPage() {
  const { user } = useAuth()
  const canCreate = ['ADMIN', 'ANALYST', 'VERIFIER'].includes(user?.role.toUpperCase() ?? '')
  const [reports, setReports] = useState<ReportDetail[]>([])
  const [total, setTotal] = useState(0)
  const [listState, setListState] = useState<RequestState>({ loading: true, error: null })
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [documentsState, setDocumentsState] = useState<RequestState>({ loading: true, error: null })
  const [selected, setSelected] = useState<ReportDetail | null>(null)
  const [evidence, setEvidence] = useState<Record<string, unknown>[] | null>(null)
  const [detailState, setDetailState] = useState<RequestState>({ loading: false, error: null })
  const [downloadState, setDownloadState] = useState<string | null>(null)
  const [downloadMessage, setDownloadMessage] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [createState, setCreateState] = useState<RequestState>({ loading: false, error: null })
  const [createNotice, setCreateNotice] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [reportType, setReportType] = useState<ReportType>('DOCUMENT_INTELLIGENCE')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [documentIds, setDocumentIds] = useState<string[]>([])
  const [year, setYear] = useState('')
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  const [commodity, setCommodity] = useState('')

  const reloadList = useCallback(() => setReload(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    setListState({ loading: true, error: null })
    listReports(1, 50, controller.signal).then(response => {
      if (controller.signal.aborted) return
      setReports(response.items); setTotal(response.total ?? response.items.length); setListState({ loading: false, error: null })
    }).catch(error => { if (!controller.signal.aborted) setListState({ loading: false, error: errorMessage(error) }) })
    return () => controller.abort()
  }, [reload])

  useEffect(() => {
    const controller = new AbortController()
    listDocuments(1, 100, controller.signal).then(response => {
      if (!controller.signal.aborted) { setDocuments(response); setDocumentsState({ loading: false, error: null }) }
    }).catch(error => { if (!controller.signal.aborted) setDocumentsState({ loading: false, error: errorMessage(error) }) })
    return () => controller.abort()
  }, [])

  const selectedReportType = useMemo(() => reportTypes.find(item => item.value === reportType)!, [reportType])
  const sourceDocumentRequired = reportType !== 'PRODUCTION'
  const validDocumentSelection = reportType === 'MULTI_DOCUMENT_COMPARISON' ? documentIds.length >= 2 : !sourceDocumentRequired || documentIds.length >= 1
  const validProductionFilters = !(year && (yearFrom || yearTo)) && !(yearFrom && yearTo && Number(yearFrom) > Number(yearTo))

  async function openReport(report: ReportDetail) {
    setSelected(report); setEvidence(null); setDownloadMessage(null); setDownloadError(null); setDetailState({ loading: true, error: null })
    try {
      const [detail, evidenceResponse] = await Promise.all([getReport(report.report_id), getReportEvidence(report.report_id)])
      setSelected(detail); setEvidence(evidenceResponse.evidence); setDetailState({ loading: false, error: null })
    } catch (error) { setDetailState({ loading: false, error: errorMessage(error) }) }
  }

  async function submitReport(event: FormEvent) {
    event.preventDefault()
    if (!canCreate || createState.loading || !validDocumentSelection) return
    const request: ReportCreateRequest = {
      report_type: reportType,
      title: title.trim(),
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(reportType === 'PRODUCTION' ? {
        ...(year ? { year: Number(year) } : {}),
        ...(yearFrom ? { year_from: Number(yearFrom) } : {}),
        ...(yearTo ? { year_to: Number(yearTo) } : {}),
        ...(stateFilter.trim() ? { state: stateFilter.trim() } : {}),
        ...(commodity.trim() ? { commodity: commodity.trim() } : {}),
      } : { document_ids: documentIds }),
    }
    setCreateState({ loading: true, error: null }); setCreateNotice(null)
    try {
      const response = await createReport(request, crypto.randomUUID())
      setCreateNotice(`Report submitted · ${response.status} · ${response.report_id}`)
      setCreateState({ loading: false, error: null }); setTitle(''); setDescription(''); setDocumentIds([])
      reloadList()
    } catch (error) { setCreateState({ loading: false, error: errorMessage(error) }) }
  }

  async function download(report: ReportDetail, format: 'PDF' | 'DOCX' | 'XLSX') {
    const key = `${report.report_id}:${format}`
    setDownloadState(key); setDownloadMessage(null); setDownloadError(null)
    try {
      const blob = await downloadReportArtifact(report.report_id, format)
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = report.artifacts?.find(item => item.artifact_type === format)?.filename ?? `${report.title}.${format.toLowerCase()}`
      document.body.appendChild(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
      setDownloadMessage(`${format} download requested.`)
    } catch (error) { setDownloadError(errorMessage(error)) }
    finally { setDownloadState(null) }
  }

  function toggleDocument(id: string) {
    setDocumentIds(current => current.includes(id) ? current.filter(value => value !== id) : current.length < 20 ? [...current, id] : current)
  }

  return <main className="page-content reports-page">
    <PageHeader title="Reports" description="Create and review evidence-backed mining and geological reports." action={<button className="button button-secondary" onClick={reloadList}>Refresh reports</button>}/>
    <section className="reports-summary" aria-label="Report service summary">
      <div><span>Reports returned</span><strong>{listState.loading ? 'Loading' : listState.error ? 'Unavailable' : total}</strong><small>Authenticated Reports API</small></div>
      <div><span>Completed reports</span><strong>{listState.loading || listState.error ? '—' : reports.filter(report => report.status === 'COMPLETED').length}</strong><small>Current page · up to 50 records</small></div>
      <div><span>Evidence-bearing reports</span><strong>{listState.loading || listState.error ? '—' : reports.filter(report => (report.evidence_count ?? 0) > 0).length}</strong><small>Count returned by the backend</small></div>
    </section>

    <div className="reports-layout">
      <section className="reports-list-section">
        <Card title="Report library" detail="Reports returned by the authenticated backend">
          {listState.loading ? <DataState kind="loading"/> : listState.error ? <DataState kind={listState.error.includes('403') ? 'forbidden' : listState.error.includes('401') ? 'unauthorized' : 'error'} title="Reports could not be loaded" detail={listState.error} onRetry={reloadList}/> : reports.length === 0 ? <DataState kind="empty" title="No reports are available" detail="Reports will appear here after an authorized request completes."/> : <div className="reports-table-wrap"><table className="reports-table"><thead><tr><th>Report</th><th>Type</th><th>Status</th><th>Created</th><th>Evidence</th></tr></thead><tbody>{reports.map(report => <tr key={report.report_id} className={selected?.report_id === report.report_id ? 'report-row-selected' : ''}>
            <td><button className="report-open" onClick={() => void openReport(report)}><strong>{report.title}</strong><small>{report.description || report.report_id}</small></button></td>
            <td>{typeLabel(report.report_type)}</td><td><StatusBadge status={report.status}/></td><td>{formatDate(report.created_at)}</td><td>{report.evidence_count ?? '—'}</td>
          </tr>)}</tbody></table></div>}
        </Card>

        <Card title="Create a report" detail="Generation uses persisted verified records and indexed document evidence">
          {!canCreate ? <DataState kind="forbidden" title="Report generation is not available for this role" detail="Generation is limited to Admin, Analyst and Verifier roles. You can still review reports."/> : <form className="report-create-form" onSubmit={submitReport}>
            <label>Report type<select value={reportType} onChange={event => { setReportType(event.target.value as ReportType); setDocumentIds([]); setCreateNotice(null) }}>{reportTypes.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select><small>{selectedReportType.description}</small></label>
            <label>Title<input required maxLength={240} value={title} onChange={event => setTitle(event.target.value)} placeholder="Give this report a clear title"/></label>
            <label>Description <span className="muted">(optional)</span><textarea maxLength={2000} rows={2} value={description} onChange={event => setDescription(event.target.value)} placeholder="Purpose or scope of this report"/></label>
            {reportType === 'PRODUCTION' ? <div className="report-production-fields"><div className="report-field-grid"><label>Year<input type="number" min="1800" max="2200" value={year} onChange={event => setYear(event.target.value)} placeholder="Any year" disabled={!!(yearFrom || yearTo)}/></label><label>State<input maxLength={120} value={stateFilter} onChange={event => setStateFilter(event.target.value)} placeholder="Any state"/></label><label>Commodity<input maxLength={120} value={commodity} onChange={event => setCommodity(event.target.value)} placeholder="Any commodity"/></label></div><div className="report-field-grid"><label>From year<input type="number" min="1800" max="2200" value={yearFrom} onChange={event => setYearFrom(event.target.value)} placeholder="Optional" disabled={!!year}/></label><label>To year<input type="number" min="1800" max="2200" value={yearTo} onChange={event => setYearTo(event.target.value)} placeholder="Optional" disabled={!!year}/></label></div><p className="report-boundary-note">No verified production data is assumed. The generated report will state insufficient verified data when the API has no matching records.</p></div> : <fieldset className="report-document-picker"><legend>Source documents {reportType === 'MULTI_DOCUMENT_COMPARISON' ? '(select at least 2 distinct documents)' : '(select at least 1)'}</legend>
              {documentsState.loading ? <DataState kind="loading"/> : documentsState.error ? <DataState kind="error" title="Documents could not be loaded" detail={documentsState.error}/> : documents.length === 0 ? <DataState kind="empty" title="No source documents returned"/> : <div className="report-document-options">{documents.map(document => <label key={document.id}><input type="checkbox" checked={documentIds.includes(document.id)} onChange={() => toggleDocument(document.id)} disabled={!documentIds.includes(document.id) && documentIds.length >= 20}/><span><strong>{document.original_filename}</strong><small>{document.status.replace(/_/g, ' ')} · {formatDate(document.created_at)}</small></span></label>)}</div>}
              {reportType === 'MULTI_DOCUMENT_COMPARISON' && documents.length < 2 && <p className="report-boundary-note">Comparison requires at least two distinct existing documents. No duplicate documents will be presented as a comparison.</p>}
            </fieldset>}
            {reportType === 'PRODUCTION' && !validProductionFilters && <p className="report-field-error" role="alert">Choose a single year or a year range, and make sure the range starts before it ends.</p>}
            <button className="button button-primary" type="submit" disabled={createState.loading || !title.trim() || !validDocumentSelection || (reportType === 'PRODUCTION' && !validProductionFilters)}>{createState.loading ? 'Submitting…' : 'Generate report'}</button>
            {createNotice && <p className="report-create-success" role="status">{createNotice}</p>}
            {createState.error && <DataState kind={createState.error.includes('403') ? 'forbidden' : createState.error.includes('401') ? 'unauthorized' : 'error'} title="Report request failed" detail={createState.error}/>}
          </form>}
        </Card>
      </section>

      <section className="report-detail-section" aria-label="Report details">
        <Card title="Report detail" detail="Evidence, validation and protected artifacts">
          {!selected ? <DataState kind="empty" title="Select a report to inspect" detail="Report sections, source references and available downloads appear here."/> : detailState.loading ? <DataState kind="loading"/> : detailState.error ? <DataState kind={detailState.error.includes('403') ? 'forbidden' : detailState.error.includes('401') ? 'unauthorized' : 'error'} title="Report detail unavailable" detail={detailState.error} onRetry={() => void openReport(selected)}/> : <div className="report-detail-content">
            <div className="report-detail-heading"><div><span>{typeLabel(selected.report_type)}</span><h3>{selected.title}</h3></div><StatusBadge status={selected.status}/></div>
            {selected.error_message && <div className="report-error-note"><strong>Generation diagnostic</strong><p>{selected.error_message}</p></div>}
            <dl className="report-meta-grid"><div><dt>Created</dt><dd>{formatDate(selected.created_at)}</dd></div><div><dt>Completed</dt><dd>{formatDate(selected.completed_at)}</dd></div><div><dt>Validation</dt><dd>{selected.validation_status ?? 'Not returned'}</dd></div><div><dt>Evidence references</dt><dd>{selected.evidence_count ?? evidence?.length ?? 'Not returned'}</dd></div></dl>
            <div className="report-source-ids"><strong>Source documents</strong>{selected.source_document_ids?.length ? <ul>{selected.source_document_ids.map(id => <li key={id}>{id}</li>)}</ul> : <p className="muted">No source documents were returned.</p>}</div>
            {selected.sections?.length ? <div className="report-sections"><h4>Report sections</h4>{selected.sections.map((section, index) => <details key={`${String(section.title ?? 'section')}-${index}`} open={index === 0}><summary><span>{String(section.title ?? `Section ${index + 1}`)}</span>{typeof section.status === 'string' && <StatusBadge status={section.status}/>}</summary><pre>{pretty(section)}</pre></details>)}</div> : <DataState kind={selected.status === 'FAILED' ? 'error' : 'empty'} title={selected.status === 'FAILED' ? 'Report generation failed' : 'Report sections are not available yet'} detail={selected.status === 'PROCESSING' || selected.status === 'PENDING' ? 'Refresh this report to check its latest status.' : undefined} onRetry={selected.status === 'PROCESSING' || selected.status === 'PENDING' ? () => { reloadList(); void openReport(selected) } : undefined}/>}
            {evidence && <details className="report-evidence"><summary>Evidence &amp; provenance ({evidence.length})</summary>{evidence.length ? <pre>{pretty(evidence)}</pre> : <p className="muted">The API returned no evidence references for this report.</p>}</details>}
            {selected.provenance && <details className="report-evidence"><summary>Provenance chain</summary><pre>{pretty(selected.provenance)}</pre></details>}
            {selected.validation && Object.keys(selected.validation).length > 0 && <details className="report-evidence"><summary>Validation details</summary><pre>{pretty(selected.validation)}</pre></details>}
            <div className="report-artifacts"><h4>Artifacts</h4>{selected.artifacts?.length ? selected.artifacts.map(artifact => <div className="report-artifact-row" key={artifact.artifact_type}><span><strong>{artifact.filename}</strong><small>{artifact.artifact_type} · {formatBytes(artifact.size_bytes)}</small></span><button className="button button-secondary button-small" disabled={downloadState !== null} onClick={() => void download(selected, artifact.artifact_type as 'PDF' | 'DOCX' | 'XLSX')}>{downloadState === `${selected.report_id}:${artifact.artifact_type}` ? 'Downloading…' : `Download ${artifact.artifact_type}`}</button></div>) : <p className="muted">No downloadable artifacts are listed for this report.</p>}{downloadMessage && <p className="report-download-success" role="status">{downloadMessage}</p>}{downloadError && <DataState kind={downloadError.includes('403') ? 'forbidden' : downloadError.includes('401') ? 'unauthorized' : 'error'} title="Download failed" detail={downloadError}/>}</div>
          </div>}
        </Card>
      </section>
    </div>
    <p className="reports-footnote">Report content, evidence and artifacts are returned by the backend. An insufficient-data status is preserved as returned; no business figures or evidence are synthesized in this interface.</p>
  </main>
}
