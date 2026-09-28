import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError } from '../api/client'
import { askWithEvidence, semanticSearch, type RagCitation, type RagResponse, type SearchEvidence, type SemanticSearchResponse } from '../api/search'
import { Card, DataState, PageHeader, StatusBadge } from '../components/ui'
import { navigate } from './router'
import './query-console.css'

type Mode = 'search' | 'answer'
type Result = { mode: 'search'; response: SemanticSearchResponse } | { mode: 'answer'; response: RagResponse }
type QueryFailure = { message: string; status?: number; timedOut?: boolean }
const EXAMPLES = [
  'Summarize the key findings in the available coal directory.',
  'What coal categories are discussed in the document?',
  'What is the total reported raw coal production?'
]
const STATUS_COPY: Record<string, { title: string; detail: string }> = {
  DETERMINISTIC_CALCULATION_REQUIRED: { title: 'Deterministic calculation required', detail: 'The query API did not calculate a result. DATA MINE will not treat an LLM-generated number as authoritative. Use verified source records and the deterministic Analytics module for a supported calculation.' },
  INSUFFICIENT_EVIDENCE: { title: 'Insufficient indexed evidence', detail: 'The backend did not return evidence sufficient to answer this question.' },
  INSUFFICIENT_VERIFIED_DATA: { title: 'Insufficient verified data', detail: 'No authoritative verified data was returned for this question.' },
  INSUFFICIENT_DOCUMENT_SET: { title: 'Insufficient document set', detail: 'The backend reports that the available document set is insufficient.' },
  INSUFFICIENT_SPATIAL_DATA: { title: 'Insufficient spatial data', detail: 'The backend reports that the available spatial evidence is insufficient.' },
  INSUFFICIENT_VERIFIED_SPATIAL_DATA: { title: 'Insufficient verified spatial data', detail: 'No verified spatial data was returned for this question.' },
  LLM_UNAVAILABLE: { title: 'Answer provider unavailable', detail: 'Evidence retrieval completed, but the configured answer provider is unavailable. You can still inspect the retrieved sources below.' },
  LLM_FAILED: { title: 'Answer could not be prepared', detail: 'The backend could not prepare an answer. Retrieved evidence remains available for inspection.' },
  CITATION_VALIDATION_FAILED: { title: 'Answer withheld', detail: 'The backend could not validate the answer citations, so no answer was presented. Retrieved evidence remains available below.' }
}

export function QueryConsole({ locationSearch = '' }: { locationSearch?: string }) {
  const queryFromUrl = new URLSearchParams(locationSearch).get('q') ?? ''
  const [query, setQuery] = useState(queryFromUrl)
  const [mode, setMode] = useState<Mode>('answer')
  const [result, setResult] = useState<Result | null>(null)
  const [failure, setFailure] = useState<QueryFailure | null>(null)
  const [loading, setLoading] = useState(false)
  const [lastSubmitted, setLastSubmitted] = useState('')
  const [lastMode, setLastMode] = useState<Mode>('answer')
  const [selectedEvidence, setSelectedEvidence] = useState<SearchEvidence | null>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const submitting = useRef(false)
  useEffect(() => {
    if (queryFromUrl) { activeRequest.current?.abort(); activeRequest.current = null; submitting.current = false; setLoading(false); setQuery(queryFromUrl); setResult(null); setFailure(null) }
  }, [queryFromUrl])
  useEffect(() => () => { activeRequest.current?.abort(); activeRequest.current = null; submitting.current = false }, [])

  async function submit(value = query, selectedMode = mode) {
    const normalized = value.trim()
    if (!normalized || submitting.current) return
    const controller = new AbortController()
    activeRequest.current?.abort()
    activeRequest.current = controller
    submitting.current = true
    setLoading(true); setFailure(null); setResult(null); setLastSubmitted(normalized); setLastMode(selectedMode); setSelectedEvidence(null)
    let timedOut = false
    const timeoutId = window.setTimeout(() => { timedOut = true; controller.abort() }, 120_000)
    try {
      const response = selectedMode === 'answer'
        ? await askWithEvidence({ question: normalized, top_k: 8 }, controller.signal)
        : await semanticSearch({ query: normalized, top_k: 10 }, controller.signal)
      if (!controller.signal.aborted) setResult(selectedMode === 'answer' ? { mode: 'answer', response: response as RagResponse } : { mode: 'search', response: response as SemanticSearchResponse })
    } catch (error) {
      if (controller.signal.aborted && !timedOut) return
      const apiError = error instanceof ApiError ? error : null
      setFailure(timedOut
        ? { message: 'The request timed out. Retry when the service is available.', timedOut: true }
        : { message: error instanceof Error ? error.message : 'The query could not be completed.', status: apiError?.status })
    } finally {
      window.clearTimeout(timeoutId)
      if (activeRequest.current === controller) { activeRequest.current = null; submitting.current = false; setLoading(false) }
    }
  }
  function clearQuery() {
    activeRequest.current?.abort(); activeRequest.current = null; submitting.current = false
    setLoading(false); setQuery(''); setResult(null); setFailure(null); setLastSubmitted(''); setSelectedEvidence(null)
    navigate('/query')
  }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit() }
  }
  function handleSubmit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void submit() }
  const errorKind = failure?.status === 401 ? 'unauthorized' : failure?.status === 403 ? 'forbidden' : 'error'
  const failureDetail = failure?.status ? `${failure.message} (HTTP ${failure.status})` : failure?.message
  const rag = result?.mode === 'answer' ? result.response : null
  const evidence = result ? result.mode === 'answer' ? result.response.evidence ?? [] : result.response.items ?? [] : []
  const citations = rag?.citations ?? []

  return <div className="page-content query-console">
    <PageHeader title="Query Console" description="Search indexed source material and ask evidence-bound questions across the DATA MINE corpus."/>
    <Card title="Ask the corpus" detail="Requests use authenticated backend hybrid retrieval. Answers are restricted to retrieved evidence.">
      <div className="query-mode" role="group" aria-label="Query mode">
        <button type="button" aria-pressed={mode === 'answer'} className={mode === 'answer' ? 'mode-selected' : ''} onClick={() => { setMode('answer'); setResult(null); setFailure(null) }} disabled={loading}>Ask with evidence</button>
        <button type="button" aria-pressed={mode === 'search'} className={mode === 'search' ? 'mode-selected' : ''} onClick={() => { setMode('search'); setResult(null); setFailure(null) }} disabled={loading}>Search evidence</button>
      </div>
      <form className="query-form query-console-form" onSubmit={handleSubmit}>
        <label className="query-prompt-label" htmlFor="corpus-query">Question or search terms</label>
        <textarea id="corpus-query" value={query} maxLength={1000} rows={4} onChange={event => setQuery(event.target.value)} onKeyDown={handleKeyDown} placeholder="Ask about the indexed source documents…" aria-describedby="query-help"/>
        <div className="query-controls"><p id="query-help">Enter to submit · Shift+Enter for a new line · Up to 1,000 characters</p><div><button type="button" className="button button-secondary" onClick={clearQuery} disabled={!query && !result && !failure && !loading}>Clear / new query</button><button type="submit" className="button button-primary" disabled={loading || !query.trim()}>{loading ? 'Working…' : mode === 'answer' ? 'Ask with evidence' : 'Search evidence'}</button></div></div>
      </form>
      <div className="query-examples" aria-label="Example queries"><span>Examples</span>{EXAMPLES.map(example => <button type="button" key={example} disabled={loading} onClick={() => setQuery(example)}>{example}</button>)}</div>
    </Card>

    <Card title="Evidence and response" detail={loading ? 'The backend is retrieving evidence and preparing the requested response.' : 'Results below are returned by the backend; no answer or calculation is generated in this interface.'}>
      {loading && <div className="query-loading" role="status"><span className="spinner"/><div><strong>Request in progress</strong><span>Retrieving evidence and preparing response…</span></div></div>}
      {failure && <DataState kind={errorKind} detail={failureDetail} onRetry={() => void submit(lastSubmitted, lastMode)}/>}
      {!loading && !failure && !result && <DataState kind="empty" title="No query submitted" detail="Choose a mode, enter a question, and submit it to retrieve live evidence."/>}
      {!loading && result?.mode === 'search' && <SearchResults response={result.response} onOpenEvidence={setSelectedEvidence}/>}
      {!loading && result?.mode === 'answer' && <RagResults response={result.response} evidence={evidence} citations={citations} onOpenEvidence={setSelectedEvidence}/>}
    </Card>

    <div className="query-trust-grid">
      <Card title="Evidence path" detail="Source → retrieval → evidence → RAG interpretation"><ol className="trust-steps"><li><b>01</b><span>Source document</span></li><li><b>02</b><span>Hybrid retrieval</span></li><li><b>03</b><span>Cited evidence</span></li><li><b>04</b><span>Evidence-bound interpretation</span></li></ol></Card>
      <Card title="Authority boundary" detail="Numerical questions follow a separate deterministic path"><p className="authority-note">AI interpretation does not replace verified source data or deterministic calculations.</p><p className="muted">When a calculation is required, this query endpoint withholds an answer. Use verified records and supported deterministic analytics for authoritative results.</p></Card>
    </div>
    <Card title="Query history" detail="History is not stored in this browser."><p className="muted">Query history is not exposed by the current backend API.</p></Card>
    {selectedEvidence && <EvidenceDrawer evidence={selectedEvidence} citations={citations} onClose={() => setSelectedEvidence(null)}/>}
  </div>
}

function SearchResults({ response, onOpenEvidence }: { response: SemanticSearchResponse; onOpenEvidence(item: SearchEvidence): void }) {
  return <div className="query-results">
    <div className="query-result-status"><StatusBadge status={response.retrieval_metadata?.mode === 'hybrid' ? 'HYBRID RETRIEVAL' : response.retrieval_metadata?.mode ?? 'RETRIEVED'}/><span>{response.total} results returned by backend</span></div>
    {response.retrieval_metadata && <p className="retrieval-note">Retrieval metadata: {String(response.retrieval_metadata.mode ?? 'mode not provided')}{typeof response.retrieval_metadata.semantic_weight === 'number' && typeof response.retrieval_metadata.lexical_weight === 'number' ? ` · semantic ${response.retrieval_metadata.semantic_weight} · lexical ${response.retrieval_metadata.lexical_weight}` : ''}</p>}
    {response.items.length === 0 ? <DataState kind="empty" title="No evidence returned" detail="The backend returned no matching indexed results."/> : <div className="evidence-list">{response.items.map((item, index) => <EvidenceCard key={item.result_id} item={item} index={index} onOpen={() => onOpenEvidence(item)}/>)}</div>}
  </div>
}

function RagResults({ response, evidence, citations, onOpenEvidence }: { response: RagResponse; evidence: SearchEvidence[]; citations: RagCitation[]; onOpenEvidence(item: SearchEvidence): void }) {
  const stateCopy = STATUS_COPY[response.status]
  const citationsByEvidence = new Map(citations.map(citation => [citation.citation_id, citation]))
  return <div className="query-results">
    <div className="query-result-status"><StatusBadge status={response.status}/>{response.retrieval_metadata && <span>Retrieval and response returned by backend</span>}</div>
    {response.status === 'ANSWERED' && response.answer && <section className="answer-panel"><div className="answer-heading"><div><span className="eyebrow">EVIDENCE-BACKED RESPONSE</span><h3>Answer</h3></div><span className="answer-trust">AI interpretation · not canonical data</span></div><div className="answer-text">{renderCitedAnswer(response.answer, citationId => { const citation = citationsByEvidence.get(citationId); const item = evidence.find(ev => ev.result_id === citation?.chunk_id); if (item) onOpenEvidence(item) })}</div><p className="answer-footnote">Interpretation grounded in retrieved sources; consult the cited evidence before relying on it.</p></section>}
    {stateCopy && <div className={`query-state-panel ${response.status === 'DETERMINISTIC_CALCULATION_REQUIRED' ? 'calculation-required' : ''}`} role="status"><strong>{stateCopy.title}</strong><p>{stateCopy.detail}</p>{response.status === 'DETERMINISTIC_CALCULATION_REQUIRED' && <p className="calculation-note">No numerical answer, inputs, units, or result were returned by this query. No calculation was performed here.</p>}</div>}
    {response.status === 'ANSWERED' && !response.answer && <DataState kind="error" title="The backend returned an empty answer" detail="No answer text was presented. Retrieved evidence is shown below."/>}
    {response.status === 'INSUFFICIENT_EVIDENCE' && evidence.length === 0 && <DataState kind="insufficient" title={stateCopy?.title ?? 'Insufficient evidence'} detail={stateCopy?.detail}/>}
    {response.status === 'LLM_UNAVAILABLE' && evidence.length === 0 && <DataState kind="error" title={stateCopy?.title} detail={stateCopy?.detail}/>}
    {citations.length > 0 && <section className="citation-section"><div className="section-heading"><div><h3>Sources</h3><p>Returned citation references and source provenance</p></div><span>{citations.length} citations</span></div><div className="citation-list">{citations.map(citation => <button type="button" className="citation-card" key={citation.citation_id} onClick={() => { const item = evidence.find(ev => ev.result_id === citation.chunk_id); if (item) onOpenEvidence(item) }} disabled={!evidence.some(item => item.result_id === citation.chunk_id)}><strong>[{citation.citation_id}]</strong><span><b>{citation.document_name}</b><small>Page {citation.page_number ?? 'not provided'} · {citation.evidence_type ?? 'source evidence'}</small></span><span className="citation-open">Inspect evidence →</span></button>)}</div></section>}
    <section className="evidence-section"><div className="section-heading"><div><h3>Evidence</h3><p>Source excerpts supplied to the backend response</p></div><span>{evidence.length} items</span></div>{evidence.length === 0 ? <DataState kind="empty" title="No evidence returned by the backend"/> : <div className="evidence-list">{evidence.map((item, index) => <EvidenceCard key={item.result_id} item={item} index={index} citation={citations.find(ref => ref.chunk_id === item.result_id)} onOpen={() => onOpenEvidence(item)}/>)}</div>}</section>
    {response.trust_metadata && <div className="trust-metadata"><span>Trusted canonical data used: <strong>{response.trust_metadata.trusted_data_used ? 'Yes' : 'No'}</strong></span><span>Source documents used: <strong>{response.trust_metadata.source_documents_used ? 'Yes' : 'No'}</strong></span><span>Trusted data requested: <strong>{response.trust_metadata.trusted_data_requested ? 'Yes' : 'No'}</strong></span></div>}
  </div>
}

function EvidenceCard({ item, index, citation, onOpen }: { item: SearchEvidence; index: number; citation?: RagCitation; onOpen(): void }) {
  const excerpt = item.exact_source_text ?? item.text
  return <article className="evidence-card"><div className="evidence-card-top"><div><span className="result-index">{citation ? `[${citation.citation_id}]` : `Result ${index + 1}`}</span><strong>{item.document_name || 'Source document'}</strong></div><StatusBadge status={item.evidence_type ?? item.content_type ?? 'SOURCE'}/></div><p className="evidence-excerpt">{excerpt}</p><div className="evidence-meta"><span>Page {item.page_number ?? 'not provided'}</span>{item.source_unit_type && <span>{item.source_unit_type}</span>}{typeof item.semantic_score === 'number' && <span>Semantic {item.semantic_score.toFixed(3)}</span>}{typeof item.lexical_score === 'number' && <span>Lexical {item.lexical_score.toFixed(3)}</span>}</div><button type="button" className="text-link evidence-open" onClick={onOpen}>Inspect evidence & provenance →</button></article>
}

function EvidenceDrawer({ evidence, citations, onClose }: { evidence: SearchEvidence; citations: RagCitation[]; onClose(): void }) {
  const citation = citations.find(item => item.chunk_id === evidence.result_id)
  return <div className="query-evidence-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}><aside className="query-evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="query-evidence-title"><header><div><span className="eyebrow">SOURCE EVIDENCE</span><h2 id="query-evidence-title">{evidence.document_name || 'Retrieved source'}</h2></div><button className="icon-button" aria-label="Close evidence" onClick={onClose}>×</button></header><div className="query-drawer-body">{citation && <div className="citation-id-label">Citation [{citation.citation_id}]</div>}<dl className="source-metadata"><div><dt>Document</dt><dd>{evidence.document_name || 'Not provided'}</dd></div><div><dt>Page</dt><dd>{evidence.page_number ?? citation?.page_number ?? 'Not provided'}</dd></div><div><dt>Evidence type</dt><dd>{evidence.evidence_type ?? citation?.evidence_type ?? 'Not provided'}</dd></div><div><dt>Content type</dt><dd>{evidence.content_type ?? 'Not provided'}</dd></div><div><dt>Document ID</dt><dd className="source-id">{evidence.document_id}</dd></div><div><dt>Version ID</dt><dd className="source-id">{evidence.document_version_id}</dd></div><div><dt>Page reference</dt><dd className="source-id">{evidence.page_id ?? citation?.page_id ?? 'Not provided'}</dd></div><div><dt>Source unit</dt><dd className="source-id">{evidence.source_unit_id ?? citation?.source_unit_id ?? 'Not provided'}</dd></div><div><dt>Chunk reference</dt><dd className="source-id">{evidence.result_id}</dd></div></dl><h3>Evidence excerpt</h3><pre className="source-evidence-text">{evidence.exact_source_text ?? evidence.text}</pre>{evidence.context && <><h3>Retrieved context</h3><pre className="source-evidence-text">{evidence.context}</pre></>}{evidence.provenance && <><h3>Provenance metadata</h3><pre className="source-evidence-text">{JSON.stringify(evidence.provenance, null, 2)}</pre></>}<button className="button button-secondary open-source" onClick={() => navigate(`/documents/${encodeURIComponent(evidence.document_id)}`)}>Open source document</button></div></aside></div>
}

function renderCitedAnswer(answer: string, onCitation: (citationId: string) => void) {
  return answer.split(/(\[E\d+\])/g).map((part, index) => {
    const match = part.match(/^\[(E\d+)\]$/)
    return match ? <button className="inline-citation" type="button" key={`${match[1]}-${index}`} onClick={() => onCitation(match[1])}>{part}</button> : <span key={`text-${index}`}>{part}</span>
  })
}
