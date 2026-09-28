import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { ApiError } from '../api/client'
import { getDocument, getDocumentDownload, getDocumentPages, getProcessingStatus, listDocumentExtractions, listDocumentVersions, listDocuments, runDocumentExtraction, startDocumentProcessing, uploadDocument } from '../api/documents'
import type { DocumentPage, DocumentRecord, DocumentVersion, ExtractedTable, ExtractionCandidate, ProcessingStatus } from '../api/types'
import { useAuth } from '../auth/AuthProvider'
import { Card, DataState, PageHeader, Pagination, StatusBadge } from '../components/ui'
import { navigate } from './router'
import './documents.css'

const PAGE_SIZE = 12
const allowedTypes = '.pdf,.docx,.xlsx,.xls,.png,.jpg,.jpeg,.tif,.tiff'
const activeStatuses = new Set(['QUEUED', 'PROCESSING', 'PARSING', 'OCR', 'TABLE_EXTRACTION', 'NORMALIZATION', 'VALIDATION', 'VERIFICATION_PENDING', 'RUNNING'])
const formatBytes = (bytes: number) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
const formatDate = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
const shortId = (value: string) => `${value.slice(0, 8)}…${value.slice(-4)}`

export function DocumentsPage() {
  const [page, setPage] = useState(1)
  const [records, setRecords] = useState<DocumentRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [selected, setSelected] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(null)
    listDocuments(page, PAGE_SIZE + 1, controller.signal).then(data => { setRecords(data); setLoading(false) }).catch((cause: unknown) => {
      if (!controller.signal.aborted) { setError(cause instanceof ApiError ? cause : new ApiError('The document list could not be loaded.')); setLoading(false) }
    })
    return () => controller.abort()
  }, [page, refresh])
  const hasNext = records.length > PAGE_SIZE
  const shown = records.slice(0, PAGE_SIZE)
  async function submitUpload() {
    if (!selected || uploading) return
    setUploading(true); setUploadMessage(null)
    try {
      const created = await uploadDocument(selected)
      setUploadMessage({ tone: 'success', text: `${created.original_filename} was uploaded successfully.` })
      setSelected(null)
      const input = document.getElementById('document-file') as HTMLInputElement | null
      if (input) input.value = ''
      setPage(1); setRefresh(value => value + 1)
      navigate(`/documents/${encodeURIComponent(created.id)}`)
    } catch (cause) {
      const apiError = cause instanceof ApiError ? cause : new ApiError('The upload could not be completed.')
      if (apiError.status === 409) setUploadMessage({ tone: 'info', text: 'This file matches an existing upload. No new document was created. Review the current document list.' })
      else if (apiError.status === 413) setUploadMessage({ tone: 'error', text: 'The selected file exceeds the configured upload size limit.' })
      else if (apiError.status === 415) setUploadMessage({ tone: 'error', text: 'This file type is not supported by the upload service.' })
      else setUploadMessage({ tone: 'error', text: apiError.message })
    } finally { setUploading(false) }
  }
  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null
    setSelected(file)
    setUploadMessage(file && file.size === 0 ? { tone: 'error', text: 'Empty files cannot be uploaded.' } : null)
  }
  return <div className="page-content documents-page">
    <PageHeader title="Documents" description="Source files, processing state and evidence-backed extraction."/>
    <Card title="Upload source document" detail="PDF, scanned PDF, Word, Excel and supported image files">
      <div className="upload-row"><label className="upload-picker" htmlFor="document-file"><span className="upload-symbol" aria-hidden="true">＋</span><span><strong>{selected?.name ?? 'Choose a file'}</strong><small>{selected ? formatBytes(selected.size) : 'Select a supported source file'}</small></span><input id="document-file" type="file" accept={allowedTypes} onChange={chooseFile}/></label>
        <button className="button button-primary" disabled={!selected || uploading || selected.size === 0} onClick={submitUpload}>{uploading ? 'Uploading…' : 'Upload document'}</button></div>
      {uploadMessage && <p className={`upload-message message-${uploadMessage.tone}`} role={uploadMessage.tone === 'error' ? 'alert' : 'status'}>{uploadMessage.text}</p>}
      {uploading && <p className="muted upload-progress" role="status">Upload in progress. The server does not expose byte-level progress.</p>}
    </Card>
    <Card title="Source documents" detail="Newest documents first · list data comes directly from the document API">
      {loading ? <DataState kind="loading"/> : error ? <DataState kind={error.status === 403 ? 'forbidden' : error.status === 401 ? 'unauthorized' : 'error'} detail={error.message} onRetry={() => setRefresh(value => value + 1)}/> : shown.length === 0 ? <DataState kind="empty" title="No documents are available" detail="Uploaded source documents will appear here."/> : <>
        <div className="documents-table-wrap"><table className="documents-table"><thead><tr><th>Document</th><th>Type</th><th>Status</th><th>Size</th><th>Uploaded</th><th>Actions</th></tr></thead><tbody>{shown.map(record => <tr key={record.id}><td><button className="document-name" onClick={() => navigate(`/documents/${encodeURIComponent(record.id)}`)}>{record.original_filename}</button><small className="document-id">{shortId(record.id)}</small></td><td>{record.document_type ?? record.mime_type ?? '—'}</td><td><StatusBadge status={record.status}/></td><td>{formatBytes(record.file_size)}</td><td>{formatDate(record.created_at)}</td><td><button className="button button-secondary button-small" onClick={() => navigate(`/documents/${encodeURIComponent(record.id)}`)}>Open</button></td></tr>)}</tbody></table></div>
        <Pagination page={page} hasNext={hasNext} onChange={setPage}/>
        <p className="page-note">Showing up to {shown.length} documents on this page. The API does not return a total count.</p>
      </>}
    </Card>
  </div>
}

export function DocumentDetailPage({ documentId }: { documentId: string }) {
  const { user } = useAuth()
  const [document, setDocument] = useState<DocumentRecord | null>(null)
  const [versions, setVersions] = useState<DocumentVersion[]>([])
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error' | 'forbidden' | 'unauthorized'>('loading')
  const [loadError, setLoadError] = useState('')
  const [processing, setProcessing] = useState<ProcessingStatus | null>(null)
  const [processingError, setProcessingError] = useState('')
  const [processingLoading, setProcessingLoading] = useState(true)
  const [processAction, setProcessAction] = useState(false)
  const [processMessage, setProcessMessage] = useState('')
  const [pages, setPages] = useState<DocumentPage[] | null>(null)
  const [pagesError, setPagesError] = useState('')
  const [pagesLoading, setPagesLoading] = useState(false)
  const [candidates, setCandidates] = useState<ExtractionCandidate[] | null>(null)
  const [candidateError, setCandidateError] = useState('')
  const [candidateLoading, setCandidateLoading] = useState(false)
  const [candidatePage, setCandidatePage] = useState(1)
  const [extracting, setExtracting] = useState(false)
  const [extractMessage, setExtractMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [downloadLoading, setDownloadLoading] = useState(false)
  const [detailCandidate, setDetailCandidate] = useState<ExtractionCandidate | null>(null)
  const pagesRequest = useRef<AbortController | null>(null)
  const candidatesRequest = useRef<AbortController | null>(null)
  const refreshStatus = useCallback((signal?: AbortSignal) => {
    setProcessingLoading(true); setProcessingError('')
    return getProcessingStatus(documentId, signal).then(result => { setProcessing(result); setProcessingLoading(false); return result }).catch((cause: unknown) => {
      if (signal?.aborted) return null
      if (cause instanceof ApiError && cause.status === 404) { setProcessing(null); setProcessingError(''); setProcessingLoading(false); return null }
      setProcessing(null); setProcessingError(cause instanceof Error ? cause.message : 'Processing status is unavailable.'); setProcessingLoading(false); return null
    })
  }, [documentId])
  useEffect(() => {
    const controller = new AbortController()
    setLoadState('loading'); setDocument(null); setProcessing(null); setPages(null); setCandidates(null)
    Promise.all([getDocument(documentId, controller.signal), listDocumentVersions(documentId, controller.signal)])
      .then(([record, versionList]) => { setDocument(record); setVersions(versionList); setLoadState('ready') })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setLoadError(cause instanceof Error ? cause.message : 'Document details could not be loaded.')
        setLoadState(cause instanceof ApiError && cause.status === 403 ? 'forbidden' : cause instanceof ApiError && cause.status === 401 ? 'unauthorized' : 'error')
      })
    void refreshStatus(controller.signal)
    return () => { controller.abort(); pagesRequest.current?.abort(); candidatesRequest.current?.abort() }
  }, [documentId, refreshStatus])
  useEffect(() => {
    if (!processing?.job || !activeStatuses.has(processing.job.status.toUpperCase())) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => { void refreshStatus(controller.signal) }, 3500)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [processing, refreshStatus])

  async function processDocument() {
    setProcessAction(true); setActionError(''); setProcessMessage('')
    try { await startDocumentProcessing(documentId); setProcessMessage('Processing job submitted.'); await refreshStatus() }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : 'Could not submit processing.') }
    finally { setProcessAction(false) }
  }
  async function loadPages() {
    pagesRequest.current?.abort()
    const controller = new AbortController(); pagesRequest.current = controller
    setPagesLoading(true); setPagesError('')
    try { setPages(await getDocumentPages(documentId, controller.signal)) }
    catch (cause) { if (!controller.signal.aborted) setPagesError(cause instanceof Error ? cause.message : 'Parsed pages could not be loaded.') }
    finally { if (pagesRequest.current === controller) { pagesRequest.current = null; setPagesLoading(false) } }
  }
  async function loadCandidates() {
    candidatesRequest.current?.abort()
    const controller = new AbortController(); candidatesRequest.current = controller
    setCandidateLoading(true); setCandidateError('')
    try { setCandidates(await listDocumentExtractions(documentId, controller.signal)); setCandidatePage(1) }
    catch (cause) { if (!controller.signal.aborted) setCandidateError(cause instanceof Error ? cause.message : 'Extraction candidates could not be loaded.') }
    finally { if (candidatesRequest.current === controller) { candidatesRequest.current = null; setCandidateLoading(false) } }
  }
  async function extract() {
    setExtracting(true); setActionError(''); setExtractMessage('')
    try { const result = await runDocumentExtraction(documentId); setExtractMessage(`Extraction finished: ${result.candidate_count} candidates returned by the service.`); setCandidates(null) }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : 'Extraction could not be started.') }
    finally { setExtracting(false) }
  }
  async function download() {
    if (!document) return
    setDownloadLoading(true); setActionError('')
    try {
      const blob = await getDocumentDownload(documentId)
      const url = URL.createObjectURL(blob); const anchor = window.document.createElement('a')
      anchor.href = url; anchor.download = document.original_filename; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : 'The document download failed.') }
    finally { setDownloadLoading(false) }
  }
  if (loadState !== 'ready' || !document) return <div className="page-content"><PageHeader title="Document" action={<button className="button button-secondary" onClick={() => navigate('/documents')}>Back to documents</button>}/><Card>{loadState === 'loading' ? <DataState kind="loading"/> : <DataState kind={loadState === 'forbidden' ? 'forbidden' : loadState === 'unauthorized' ? 'unauthorized' : 'error'} detail={loadError} onRetry={() => window.location.reload()}/>}</Card></div>
  const status = processing?.job.status.toUpperCase()
  const canProcess = !status || !activeStatuses.has(status)
  const isAdmin = user?.role.toUpperCase() === 'ADMIN'
  const isVerifier = user?.role.toUpperCase() === 'VERIFIER'
  const candidateSlice = (candidates ?? []).slice((candidatePage - 1) * 20, candidatePage * 20)
  const pageHasNext = (candidates?.length ?? 0) > candidatePage * 20
  const ocrPages = pages?.filter(p => p.page_metadata?.ocr_used === true).length
  return <div className="page-content document-detail">
    <PageHeader title={document.original_filename} description="Document record, processing history and source-linked intelligence." action={<div className="detail-actions"><button className="button button-secondary" onClick={() => navigate('/documents')}>All documents</button><button className="button button-primary" onClick={download} disabled={downloadLoading}>{downloadLoading ? 'Preparing…' : 'Download source'}</button></div>}/>
    {actionError && <div className="document-alert" role="alert">{actionError}</div>}
    <div className="detail-grid">
      <Card title="Document record" detail="Metadata returned by the documents API">
        <dl className="metadata-grid"><div><dt>Document ID</dt><dd className="monospace">{document.id}</dd></div><div><dt>Document type</dt><dd>{document.document_type ?? '—'}</dd></div><div><dt>MIME type</dt><dd>{document.mime_type ?? '—'}</dd></div><div><dt>File size</dt><dd>{formatBytes(document.file_size)}</dd></div><div><dt>Created</dt><dd>{formatDate(document.created_at)}</dd></div><div><dt>Record status</dt><dd><StatusBadge status={document.status}/></dd></div><div className="metadata-wide"><dt>SHA-256 checksum</dt><dd className="monospace break-all">{document.sha256_checksum ?? 'Not provided'}</dd></div></dl>
      </Card>
      <Card title="Processing" detail="Most recent processing job for this document">
        {processingLoading ? <DataState kind="loading"/> : processingError ? <DataState kind="error" detail={processingError} onRetry={() => void refreshStatus()}/> : processing ? <>
          <div className="processing-summary"><StatusBadge status={processing.job.status}/><span className="muted">Job {shortId(processing.job.id)}</span></div>
          <div className="processing-meta"><span>Started {formatDate(processing.job.started_at)}</span><span>Updated {formatDate(processing.job.updated_at)}</span><span>Completed {formatDate(processing.job.completed_at)}</span></div>
          {processing.stages.length > 0 && <ol className="stage-list">{processing.stages.map(stage => <li key={stage.id}><span className="stage-marker"/><span><strong>{stage.stage.replace(/_/g, ' ')}</strong><small>{formatDate(stage.started_at ?? stage.created_at)}{stage.completed_at ? ` · ${formatDate(stage.completed_at)}` : ''}</small></span><StatusBadge status={stage.status}/></li>)}</ol>}
          {status === 'FAILED' && <p className="muted">The processing service reported a failure. Internal error details are not shown here.</p>}
          {activeStatuses.has(status ?? '') && <p className="muted">Status refreshes while this job is active.</p>}
        </> : <DataState kind="empty" title="No processing job recorded" detail="Submit processing to parse the latest document version."/>}
        {canProcess && <div className="card-action"><button className="button button-secondary" disabled={processAction} onClick={processDocument}>{processAction ? 'Submitting…' : status ? 'Reprocess latest version' : 'Start processing'}</button></div>}
        {processMessage && <p className="message-success">{processMessage}</p>}
      </Card>
    </div>
    <Card title="Document versions" detail="Version metadata and checksums from the backend">
      {versions.length === 0 ? <DataState kind="empty" title="No version records returned"/> : <div className="documents-table-wrap"><table><thead><tr><th>Version</th><th>Created</th><th>Size</th><th>SHA-256</th><th>Processing scope</th></tr></thead><tbody>{versions.map((version, index) => <tr key={version.id}><td><strong>v{version.version_number}</strong>{index === 0 && <span className="current-version">Latest</span>}</td><td>{formatDate(version.created_at)}</td><td>{formatBytes(version.file_size)}</td><td className="monospace checksum-cell">{version.sha256_checksum ?? '—'}</td><td>{index === 0 ? 'Processing and download APIs use latest version' : 'Metadata only; API scopes processing/download to latest'}</td></tr>)}</tbody></table></div>}
    </Card>
    <Card title="Document intelligence" detail="Parsed pages and extraction evidence are loaded on request.">
      <div className="intelligence-actions"><button className="button button-secondary" disabled={pagesLoading || status !== 'COMPLETED'} onClick={loadPages}>{pagesLoading ? 'Loading pages…' : pages ? 'Refresh parsed pages' : 'Load parsed pages'}</button>
        <button className="button button-secondary" disabled={candidateLoading || candidates !== null} onClick={loadCandidates}>{candidateLoading ? 'Loading candidates…' : candidates ? 'Candidates loaded' : 'Load extraction candidates'}</button>
        {status === 'COMPLETED' && isAdmin && <button className="button button-primary" disabled={extracting} onClick={extract}>{extracting ? 'Extracting…' : 'Run extraction'}</button>}
      </div>
      {status !== 'COMPLETED' && <p className="page-note">Page results become available after the latest processing job completes. Extraction is enabled for Admin after processing.</p>}
      {extractMessage && <p className="message-success" role="status">{extractMessage}</p>}
      {pagesError && <DataState kind="error" detail={pagesError} onRetry={loadPages}/>}
      {pages && <>
        <div className="result-summary"><span><strong>{pages.length}</strong> parsed pages</span><span><strong>{pages.reduce((sum, item) => sum + item.tables.length, 0)}</strong> extracted tables</span><span><strong>{ocrPages ?? '—'}</strong> pages with OCR used</span></div>
        {pages.length === 0 ? <DataState kind="empty" title="No parsed pages returned"/> : <div className="page-results">{pages.map((item, index) => <PageEvidence key={item.id} page={item} index={index}/>)}</div>}
      </>}
      {candidateError && <DataState kind="error" detail={candidateError} onRetry={loadCandidates}/>}
      {candidates && <section className="candidate-section"><div className="candidate-heading"><div><h3>Extraction candidates</h3><p>{candidates.length} candidate records returned by the API</p></div>{(isAdmin || isVerifier) && candidates.length > 0 && <button className="button button-secondary button-small" onClick={() => navigate('/verification')}>Open Verification</button>}</div>
        <div className="trust-notice"><strong>EXTRACTED ≠ VERIFIED</strong><span>Candidate values remain untrusted until they pass the established verification workflow.</span></div>
        {candidates.length === 0 ? <DataState kind="empty" title="No extraction candidates returned"/> : <><div className="documents-table-wrap"><table className="candidate-table"><thead><tr><th>Entity / type</th><th>Candidate value</th><th>Verification</th><th>Confidence</th><th>Provenance</th><th/></tr></thead><tbody>{candidateSlice.map(candidate => <tr key={candidate.id}><td>{candidate.candidate_type}</td><td className="candidate-value">{candidate.normalized_value ?? candidate.raw_value ?? candidate.raw_text}</td><td><StatusBadge status={candidate.verification_status}/></td><td>{candidate.extraction_confidence ?? '—'}</td><td>{candidate.source_page_id ? `Page source ${shortId(candidate.source_page_id)}` : 'Source reference unavailable'}</td><td><button className="button button-secondary button-small" onClick={() => setDetailCandidate(candidate)}>Evidence</button></td></tr>)}</tbody></table></div><Pagination page={candidatePage} hasNext={pageHasNext} onChange={setCandidatePage}/></>}
      </section>}
    </Card>
    {detailCandidate && <div className="evidence-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && setDetailCandidate(null)}><aside className="evidence-panel" role="dialog" aria-modal="true" aria-labelledby="evidence-title"><header><div><h2 id="evidence-title">Candidate evidence</h2><p>Source → Extraction → Validation → Verification</p></div><button className="icon-button" aria-label="Close evidence" onClick={() => setDetailCandidate(null)}>×</button></header><div className="evidence-body"><div className="trust-notice"><strong>{detailCandidate.verification_status}</strong><span>This candidate is not promoted to trusted data by this view.</span></div><dl className="metadata-grid"><div><dt>Candidate type</dt><dd>{detailCandidate.candidate_type}</dd></div><div><dt>Match status</dt><dd>{detailCandidate.match_status}</dd></div><div className="metadata-wide"><dt>Raw extracted text</dt><dd>{detailCandidate.raw_text || '—'}</dd></div><div><dt>Raw value</dt><dd>{detailCandidate.raw_value ?? '—'}</dd></div><div><dt>Normalized value</dt><dd>{detailCandidate.normalized_value ?? '—'}</dd></div><div><dt>Normalized numeric value</dt><dd>{detailCandidate.normalized_numeric_value ?? '—'} {detailCandidate.unit ?? ''}</dd></div><div><dt>Confidence</dt><dd>{detailCandidate.extraction_confidence ?? 'Not provided'}</dd></div><div><dt>Candidate ID</dt><dd className="monospace break-all">{detailCandidate.id}</dd></div><div><dt>Document ID</dt><dd className="monospace break-all">{detailCandidate.document_id}</dd></div><div><dt>Version ID</dt><dd className="monospace break-all">{detailCandidate.document_version_id}</dd></div>{(['source_page_id','source_content_id','source_table_id','source_cell_id'] as const).map(key => <div key={key}><dt>{key.replace('source_', '').replace(/_/g, ' ')}</dt><dd className="monospace break-all">{detailCandidate[key] ?? 'Not provided'}</dd></div>)}</dl>
          <h3>Validation results</h3><pre className="evidence-json">{JSON.stringify(detailCandidate.validation_results, null, 2)}</pre><h3>Candidate metadata</h3><pre className="evidence-json">{JSON.stringify(detailCandidate.candidate_metadata, null, 2)}</pre>
          {(isAdmin || isVerifier) && <button className="button button-primary evidence-verify" onClick={() => navigate('/verification')}>Open Verification</button>}
        </div></aside></div>}
  </div>
}

function PageEvidence({ page, index }: { page: DocumentPage; index: number }) {
  const [open, setOpen] = useState(index === 0)
  return <details className="page-evidence" open={open} onToggle={event => setOpen((event.currentTarget as HTMLDetailsElement).open)}>
    <summary><span className="page-title">Page {page.page_number ?? index + 1}</span><span>{page.contents.length} content units</span><span>{page.tables.length} tables</span>{page.page_metadata?.ocr_used === true && <span className="ocr-label">OCR used</span>}</summary>
    {open && <div className="page-evidence-body">
      {page.page_metadata && <details className="metadata-disclosure"><summary>Processing metadata</summary><pre className="evidence-json">{JSON.stringify(page.page_metadata, null, 2)}</pre></details>}
      {page.text && <details className="metadata-disclosure"><summary>Extracted page text</summary><pre className="page-text">{page.text}</pre></details>}
      {page.contents.length > 0 && <div className="content-units"><h4>Source content units</h4>{page.contents.map(content => <article className="content-unit" key={content.id}><div className="content-unit-meta"><StatusBadge status={content.content_type}/><code>{shortId(content.id)}</code>{content.confidence !== null && <span>Confidence {content.confidence}</span>}</div><p>{content.text}</p>{content.extraction_metadata && <details className="metadata-disclosure"><summary>Extraction metadata</summary><pre className="evidence-json">{JSON.stringify(content.extraction_metadata, null, 2)}</pre></details>}</article>)}</div>}
      {page.tables.length > 0 ? page.tables.map(table => <TableViewer table={table} key={table.id} pageNumber={page.page_number}/>) : !page.text && page.contents.length === 0 && <p className="muted">No page text or extracted content was returned.</p>}
    </div>}
  </details>
}

function TableViewer({ table, pageNumber }: { table: ExtractedTable; pageNumber: number | null }) {
  const matrix = useMemo(() => {
    const dimensions = table.cells.reduce((size, cell) => ({ rows: Math.max(size.rows, cell.row_index + 1), columns: Math.max(size.columns, cell.column_index + 1) }), { rows: 0, columns: 0 })
    const rowCount = dimensions.rows
    const columnCount = dimensions.columns
    const rows = Array.from({ length: rowCount }, () => Array.from({ length: columnCount }, () => ''))
    table.cells.forEach(cell => { rows[cell.row_index][cell.column_index] = cell.normalized_value ?? cell.raw_value ?? '' })
    return rows
  }, [table])
  return <section className="extracted-table"><header><div><h4>Extracted table {table.table_order}</h4><p>Page {pageNumber ?? 'unknown'} · Source table {shortId(table.id)} · {table.extraction_method ?? 'method not provided'}</p></div>{table.confidence !== null && <span className="table-confidence">Confidence {table.confidence}</span>}</header>
    {matrix.length ? <div className="table-wrap"><table><tbody>{matrix.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, colIndex) => <td key={colIndex}>{value || '—'}</td>)}</tr>)}</tbody></table></div> : <DataState kind="empty" title="No table cells returned"/>}
  </section>
}
