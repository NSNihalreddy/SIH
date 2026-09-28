import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { ApiError } from '../api/client'
import { approveCandidate, claimCandidate, editAndApproveCandidate, getVerificationCandidate, getVerificationQueue, listCanonicalEntities, markCandidateUnresolved, rejectCandidate, resolveVerificationConflict } from '../api/verification'
import type { VerificationCandidateDetail, VerificationRecord } from '../api/types'
import { Card, DataState, PageHeader, StatusBadge } from '../components/ui'
import './verification.css'

type QueueFilter = 'OPEN' | 'PENDING' | 'IN_REVIEW'
type ReviewAction = 'CLAIM' | 'APPROVE' | 'EDIT' | 'REJECT' | 'UNRESOLVED' | 'RESOLVE_CONFLICT'
type QueueState = { loading: boolean; error: string | null }
type CanonicalEntity = { id: string; entity_type: string; canonical_name: string; normalized_key: string }
const date = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
const displayError = (error: unknown) => error instanceof ApiError ? `${error.message}${error.status ? ` (HTTP ${error.status})` : ''}` : error instanceof Error ? error.message : 'The request could not be completed.'
const stringify = (value: unknown) => JSON.stringify(value, null, 2)
const candidateSourceUnit = (record: VerificationRecord | VerificationCandidateDetail | null) => {
  if (!record) return null
  const item = record as VerificationCandidateDetail
  const metadata = item.candidate?.candidate_metadata ?? {}
  const source = item.source ?? {}
  const reference = metadata.source_reference && typeof metadata.source_reference === 'object' ? metadata.source_reference as Record<string, unknown> : {}
  const value = metadata.source_unit_id ?? metadata.sourceUnitId ?? source.source_unit_id ?? reference.source_unit_id
  return value === null || value === undefined ? null : String(value)
}

export function VerificationPage() {
  const { user } = useAuth()
  const canReview = ['ADMIN', 'VERIFIER'].includes(user?.role.toUpperCase() ?? '')
  const [filter, setFilter] = useState<QueueFilter>('OPEN')
  const [queuePage, setQueuePage] = useState(1)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [pending, setPending] = useState<VerificationRecord[]>([])
  const [inReview, setInReview] = useState<VerificationRecord[]>([])
  const [pendingTotal, setPendingTotal] = useState(0)
  const [inReviewTotal, setInReviewTotal] = useState(0)
  const [queueState, setQueueState] = useState<QueueState>({ loading: true, error: null })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<VerificationCandidateDetail | null>(null)
  const [detailState, setDetailState] = useState<QueueState>({ loading: false, error: null })
  const [action, setAction] = useState<ReviewAction | null>(null)
  const [reason, setReason] = useState('')
  const [editedValue, setEditedValue] = useState('')
  const [canonicalId, setCanonicalId] = useState('')
  const [conflictId, setConflictId] = useState('')
  const [conflictStatus, setConflictStatus] = useState<'RESOLVED' | 'ACCEPTED_AS_SOURCE_VARIATION'>('RESOLVED')
  const [entities, setEntities] = useState<CanonicalEntity[]>([])
  const [actionState, setActionState] = useState<QueueState>({ loading: false, error: null })
  const [notice, setNotice] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [conflictResolution, setConflictResolution] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setQueueState({ loading: true, error: null })
    Promise.all([
      getVerificationQueue('PENDING', controller.signal, queuePage, 100, search.trim() || undefined),
      getVerificationQueue('IN_REVIEW', controller.signal, queuePage, 100, search.trim() || undefined),
    ]).then(([pendingResult, reviewResult]) => {
      if (controller.signal.aborted) return
      setPending(pendingResult.items); setPendingTotal(pendingResult.total)
      setInReview(reviewResult.items); setInReviewTotal(reviewResult.total)
      setQueueState({ loading: false, error: null })
      const all = [...pendingResult.items, ...reviewResult.items]
      setSelectedId(current => current && all.some(item => item.candidate.id === current) ? current : all[0]?.candidate.id ?? null)
    }).catch(error => { if (!controller.signal.aborted) setQueueState({ loading: false, error: displayError(error) }) })
    return () => controller.abort()
  }, [reload, queuePage, search])

  useEffect(() => {
    if (!selectedId) { setDetail(null); setDetailState({ loading: false, error: null }); return }
    const controller = new AbortController()
    setDetailState({ loading: true, error: null }); setDetail(null)
    getVerificationCandidate(selectedId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setDetail(value); setCanonicalId(''); setDetailState({ loading: false, error: null }) }
    }).catch(error => { if (!controller.signal.aborted) setDetailState({ loading: false, error: displayError(error) }) })
    return () => controller.abort()
  }, [selectedId, reload])

  useEffect(() => {
    const type = detail?.candidate?.classification
    if (!type || detail?.mapping_proposal?.status !== 'POSSIBLE_MATCH') { setEntities([]); return }
    const controller = new AbortController()
    listCanonicalEntities(type, controller.signal).then(result => { if (!controller.signal.aborted) setEntities(result.items) }).catch(() => { if (!controller.signal.aborted) setEntities([]) })
    return () => controller.abort()
  }, [detail?.candidate?.classification, detail?.mapping_proposal?.status])

  const allOpen = useMemo(() => [...pending, ...inReview], [pending, inReview])
  const visible = filter === 'PENDING' ? pending : filter === 'IN_REVIEW' ? inReview : allOpen
  const selectedQueueItem = allOpen.find(item => item.candidate.id === selectedId) ?? null
  const queuePages = Math.max(1, Math.ceil((filter === 'PENDING' ? pendingTotal : filter === 'IN_REVIEW' ? inReviewTotal : Math.max(pendingTotal, inReviewTotal)) / 100))
  const candidateDetail = detail ?? selectedQueueItem
  const candidate = candidateDetail?.candidate
  const source = candidateDetail?.source
  const proposal = candidateDetail?.mapping_proposal
  const conflicts = candidateDetail?.conflicts ?? []
  const proposalStatus = String(proposal?.status ?? candidate?.mapping_status ?? 'NOT CLASSIFIED')

  function beginAction(next: ReviewAction, selectedConflictId = '') {
    setAction(next); setReason(''); setEditedValue(''); setConflictId(selectedConflictId); setConflictResolution(''); setActionState({ loading: false, error: null }); setNotice(null)
  }

  async function submitAction(event: FormEvent) {
    event.preventDefault()
    if (!action || !candidate || !canReview || actionState.loading) return
    setActionState({ loading: true, error: null })
    try {
      let message = ''
      if (action === 'CLAIM') { await claimCandidate(candidate.id); message = 'Candidate claimed for review.' }
      else if (action === 'APPROVE') {
        await approveCandidate(candidate.id, { reason: reason.trim(), ...(canonicalId ? { canonical_entity_id: canonicalId } : {}) }); message = 'Candidate approved and recorded as trusted by the backend.'
      } else if (action === 'EDIT') {
        const parsed: unknown = JSON.parse(editedValue)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Edited value must be a JSON object.')
        await editAndApproveCandidate(candidate.id, { edited_value: parsed as Record<string, unknown>, reason: reason.trim(), ...(canonicalId ? { canonical_entity_id: canonicalId } : {}) }); message = 'Edited candidate submitted for approval.'
      } else if (action === 'REJECT') { await rejectCandidate(candidate.id, { reason: reason.trim() }); message = 'Candidate rejected.' }
      else if (action === 'UNRESOLVED') { await markCandidateUnresolved(candidate.id, { reason: reason.trim() }); message = 'Candidate marked unresolved.' }
      else if (action === 'RESOLVE_CONFLICT') { await resolveVerificationConflict(conflictId, { status: conflictStatus, resolution: conflictResolution.trim() }); message = 'Conflict resolution submitted.' }
      setActionState({ loading: false, error: null }); setAction(null); setNotice(message); setReload(value => value + 1)
    } catch (error) { setActionState({ loading: false, error: displayError(error) }) }
  }

  function actionTitle(value: ReviewAction) {
    return ({ CLAIM: 'Claim candidate', APPROVE: 'Approve candidate', EDIT: 'Edit and approve', REJECT: 'Reject candidate', UNRESOLVED: 'Mark unresolved', RESOLVE_CONFLICT: 'Resolve conflict' })[value]
  }

  return <main className="page-content verification-page">
    <PageHeader title="Verification" description="Human review of extracted candidates before they can become trusted canonical records." action={<button className="button button-secondary" onClick={() => setReload(value => value + 1)}>Refresh queue</button>}/>
    <div className="verification-trust-banner"><strong>EXTRACTION CANDIDATE ≠ VERIFIED TRUSTED RECORD</strong><span>Extracted values remain untrusted until a reviewer checks their source evidence and completes an explicit verification action.</span></div>
    <section className="verification-summary" aria-label="Open verification queue counts">
      <div><span>Pending</span><strong>{queueState.loading ? 'Loading' : queueState.error ? '—' : pendingTotal}</strong><small>Awaiting claim</small></div>
      <div><span>In review</span><strong>{queueState.loading ? 'Loading' : queueState.error ? '—' : inReviewTotal}</strong><small>Claimed for human review</small></div>
      <div><span>Current page loaded</span><strong>{queueState.loading ? 'Loading' : queueState.error ? '—' : allOpen.length}</strong><small>Up to 100 per status</small></div>
    </section>
    {notice && <p className="verification-action-notice" role="status">{notice}</p>}

    <div className="verification-workspace">
      <Card title="Review queue" detail="Real candidates returned from the verification API">
        <div className="verification-queue-controls"><label>Queue status<select value={filter} onChange={event => { setFilter(event.target.value as QueueFilter); setQueuePage(1) }}><option value="OPEN">Pending + In review</option><option value="PENDING">Pending</option><option value="IN_REVIEW">In review</option></select></label><label>Search all candidates
  <input
    value={searchInput}
    onChange={event => setSearchInput(event.target.value)}
    onKeyDown={event => {
      if (event.key === 'Enter') {
        setQueuePage(1)
        setSearch(searchInput.trim())
      }
    }}
    placeholder="Value, type, document, page, evidence"
    aria-label="Search all verification candidates"
  />
</label>
<button
  type="button"
  className="button button-secondary button-small"
  onClick={() => {
    setQueuePage(1)
    setSearch(searchInput.trim())
  }}
>
  Search
</button>
{search && <button
  type="button"
  className="button button-secondary button-small"
  onClick={() => {
    setSearchInput('')
    setSearch('')
    setQueuePage(1)
  }}
>
  Clear
</button>}</div>
        {queueState.loading ? <DataState kind="loading"/> : queueState.error ? <DataState kind={queueState.error.includes('403') ? 'forbidden' : queueState.error.includes('401') ? 'unauthorized' : 'error'} title="Verification queue unavailable" detail={queueState.error} onRetry={() => setReload(value => value + 1)}/> : visible.length === 0 ? <DataState kind="empty" title="No open candidates match this view" detail="No candidate values are inferred when the backend returns none."/> : <div className="verification-queue-list" role="list">
          {visible.map(item => {
            const value = item.candidate.normalized_value ?? item.candidate.raw_value ?? item.candidate.raw_text
            return <button type="button" role="listitem" className={`verification-queue-item ${selectedId === item.candidate.id ? 'selected' : ''}`} key={item.candidate.id} onClick={() => setSelectedId(item.candidate.id)}>
              <span className="verification-item-heading"><strong>{item.candidate.candidate_type.replace(/_/g, ' ')}</strong><StatusBadge status={item.verification_status}/></span>
              <span className="verification-item-value">{value || 'Value not returned'}</span>
              <span className="verification-item-meta">{String(item.classification ?? 'UNCLASSIFIED')} · {String(item.classification_status ?? 'NOT_CLASSIFIED')} · mapping {String(item.mapping_status ?? item.candidate.mapping_status ?? 'UNRESOLVED')}</span>
              <span className="verification-item-source">{item.source.document_name ?? item.candidate.document_id}{item.source.page_number ? ` · page ${item.source.page_number}` : ''}</span>
            </button>
          })}
        </div>}
        {!queueState.loading && !queueState.error && <div className="verification-pagination"><span>Page {queuePage} of {queuePages} · {search ? `Global search: “${search}” · ` : ''}100 candidates per status page</span><div><button className="button button-secondary button-small" disabled={queuePage <= 1} onClick={() => setQueuePage(value => value - 1)}>Previous</button><button className="button button-secondary button-small" disabled={queuePage >= queuePages} onClick={() => setQueuePage(value => value + 1)}>Next</button></div></div>}
      </Card>

      <Card title="Candidate evidence" detail="Source context and provenance returned by the backend">
        {!selectedId ? <DataState kind="empty" title="Select a candidate to review" detail="Candidate detail, context and mapping information appear here."/> : detailState.loading ? <DataState kind="loading"/> : detailState.error ? <DataState kind={detailState.error.includes('403') ? 'forbidden' : detailState.error.includes('401') ? 'unauthorized' : 'error'} title="Candidate detail unavailable" detail={detailState.error} onRetry={() => setReload(value => value + 1)}/> : !candidate || !source ? <DataState kind="empty" title="Candidate detail was not returned"/> : <div className="verification-detail">
          <div className="verification-detail-heading"><div><span>{candidate.candidate_type.replace(/_/g, ' ')}</span><h3>{candidate.normalized_value ?? candidate.raw_value ?? 'No normalized value'}</h3></div><StatusBadge status={candidate.verification_status}/></div>
          <div className="verification-classification"><div><small>Classification</small><strong>{String(candidate.classification ?? candidateDetail?.classification ?? 'UNKNOWN')}</strong></div><div><small>Classification state</small><strong>{String(candidate.classification_status ?? candidateDetail?.classification_status ?? 'NOT_CLASSIFIED')}</strong></div><div><small>Candidate match</small><strong>{candidate.match_status}</strong></div><div><small>Mapping status</small><strong>{String(candidate.mapping_status ?? candidateDetail?.mapping_status ?? 'UNRESOLVED')}</strong></div></div>
          <dl className="verification-provenance-grid"><div><dt>Source document</dt><dd>{source.document_name ?? 'Name not returned'}</dd></div><div><dt>Source page</dt><dd>{source.page_number ?? 'Not linked'}</dd></div><div><dt>Document ID</dt><dd className="verification-id">{source.document_id ?? candidate.document_id}</dd></div><div><dt>Version</dt><dd>{source.version_number ?? 'Not returned'} · <span className="verification-id">{source.document_version_id ?? candidate.document_version_id}</span></dd></div><div><dt>Page ID</dt><dd className="verification-id">{source.page_id ?? candidate.source_page_id ?? 'Not linked'}</dd></div><div><dt>Source unit ID</dt><dd className="verification-id">{candidateSourceUnit(candidateDetail) ?? 'Not returned by backend'}</dd></div><div><dt>Extraction confidence</dt><dd>{candidate.extraction_confidence ?? 'Not provided'}</dd></div><div><dt>Created</dt><dd>{date(candidate.created_at)}</dd></div></dl>
          <section className="verification-value-grid"><div><span>Raw extracted value</span><p>{candidate.raw_value ?? 'Not separately returned'}</p></div><div><span>Normalized value</span><p>{candidate.normalized_value ?? 'Not returned'}</p></div>{candidate.normalized_numeric_value !== null && <div><span>Normalized numeric value</span><p>{candidate.normalized_numeric_value} {candidate.unit ?? ''}</p></div>}<div className="verification-evidence-context"><span>Extraction context / source evidence</span><pre>{source.source_text || source.raw_extracted_text || source.page_text || 'Source text was not returned for this candidate.'}</pre></div>{source.source_cell && <div><span>Source table cell</span><p>Row {source.source_cell.row_index ?? '—'} · Column {source.source_cell.column_index ?? '—'} · {source.source_cell.raw_value ?? 'No raw cell value returned'}</p></div>}</section>
          <details className="verification-disclosure"><summary>Validation results</summary><pre>{stringify(candidate.validation_results ?? candidateDetail.validation_results ?? [])}</pre></details>
          {proposal && <details className="verification-disclosure" open><summary>Canonical match / mapping proposal · {proposalStatus}</summary><pre>{stringify(proposal)}</pre></details>}
          {candidateDetail.canonical_record && <details className="verification-disclosure"><summary>Existing canonical record</summary><pre>{stringify(candidateDetail.canonical_record)}</pre></details>}
          {(candidateDetail.verification_events?.length ?? 0) > 0 && <details className="verification-disclosure"><summary>Verification history ({candidateDetail.verification_events?.length})</summary><pre>{stringify(candidateDetail.verification_events)}</pre></details>}
          {conflicts.length > 0 && <section className="verification-conflicts"><h4>Conflict handling</h4>{conflicts.map((conflict, index) => <article key={String(conflict.id ?? index)}><div><strong>{String(conflict.conflict_type ?? 'Conflict')}</strong><StatusBadge status={String(conflict.status ?? 'OPEN')}/></div><p>{String(conflict.description ?? 'Conflict details were not returned.')}</p>{canReview && !['RESOLVED', 'ACCEPTED_AS_SOURCE_VARIATION'].includes(String(conflict.status)) && <button className="button button-secondary button-small" onClick={() => beginAction('RESOLVE_CONFLICT', String(conflict.id))}>Resolve conflict</button>}</article>)}</section>}
          {canReview ? <div className="verification-actions"><p>Review actions are explicit and create verification history. Check the source evidence before submitting.</p>{candidate.verification_status === 'PENDING' && <button className="button button-primary" onClick={() => beginAction('CLAIM')}>Claim for review</button>}{candidate.verification_status === 'IN_REVIEW' && <div className="verification-action-buttons"><button className="button button-primary" onClick={() => beginAction('APPROVE')}>Approve</button><button className="button button-secondary" onClick={() => beginAction('EDIT')}>Edit and approve</button><button className="button button-secondary" onClick={() => beginAction('REJECT')}>Reject</button><button className="button button-secondary" onClick={() => beginAction('UNRESOLVED')}>Mark unresolved</button></div>}</div> : <p className="verification-readonly-note">Your role can inspect candidate evidence, but only Admin and Verifier roles can claim or review candidates.</p>}
        </div>}
      </Card>
    </div>
    {action && candidate && <div className="verification-modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && !actionState.loading && setAction(null)}><section className="verification-modal" role="dialog" aria-modal="true" aria-labelledby="verification-modal-title"><header><h2 id="verification-modal-title">{actionTitle(action)}</h2><button className="icon-button" aria-label="Close" disabled={actionState.loading} onClick={() => setAction(null)}>×</button></header><form onSubmit={submitAction}>
      <p className="verification-modal-boundary">Candidate <code>{candidate.id}</code> · {candidate.verification_status}. This action may update trusted records or the review history.</p>
      {action === 'CLAIM' ? <p>Claiming moves this candidate from Pending to In review. It does not approve the extracted value.</p> : action === 'RESOLVE_CONFLICT' ? <><label>Resolution type<select value={conflictStatus} onChange={event => setConflictStatus(event.target.value as typeof conflictStatus)}><option value="RESOLVED">Resolved</option><option value="ACCEPTED_AS_SOURCE_VARIATION">Accepted as source variation</option></select></label><label>Resolution note<textarea required maxLength={4000} rows={4} value={conflictResolution} onChange={event => setConflictResolution(event.target.value)} placeholder="Describe the evidence-based resolution"/></label></> : <>
        <label>Review reason<textarea required maxLength={2000} rows={3} value={reason} onChange={event => setReason(event.target.value)} placeholder="Record why this action is supported by the source evidence"/></label>
        {action === 'EDIT' && <label>Accepted value (JSON object)<textarea required rows={6} value={editedValue} onChange={event => setEditedValue(event.target.value)} placeholder={'Enter the reviewed fields, such as {"normalized_value":"..."}'} className="verification-json-input"/></label>}
        {action === 'APPROVE' && proposalStatus === 'POSSIBLE_MATCH' && <label>Canonical entity match<select required value={canonicalId} onChange={event => setCanonicalId(event.target.value)}><option value="">Choose an existing entity</option>{entities.map(entity => <option value={entity.id} key={entity.id}>{entity.canonical_name} · {entity.entity_type}</option>)}</select><small>Possible matches require an explicit human choice. No entity is preselected.</small></label>}
      </>}
      {actionState.error && <DataState kind={actionState.error.includes('403') ? 'forbidden' : 'error'} title="Action was not completed" detail={actionState.error}/>}
      <div className="verification-modal-actions"><button type="button" className="button button-secondary" disabled={actionState.loading} onClick={() => setAction(null)}>Cancel</button><button type="submit" className="button button-primary" disabled={actionState.loading || (action !== 'CLAIM' && action !== 'RESOLVE_CONFLICT' && !reason.trim()) || (action === 'EDIT' && !editedValue.trim()) || (action === 'APPROVE' && proposalStatus === 'POSSIBLE_MATCH' && !canonicalId) || (action === 'RESOLVE_CONFLICT' && !conflictResolution.trim())}>{actionState.loading ? 'Submitting…' : `Confirm ${actionTitle(action).toLowerCase()}`}</button></div>
    </form></section></div>}
  </main>
}
