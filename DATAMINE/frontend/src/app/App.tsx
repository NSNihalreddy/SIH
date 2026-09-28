import { lazy, Suspense, useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { usePath, navigate } from './router'
import { AppShell } from '../components/shell'
import { DataState, PageHeader, Card, StatusBadge } from '../components/ui'
import { listDocuments } from '../api/documents'
import { getProductionSummary } from '../api/analytics'
import { listGisLayers } from '../api/gis'
import { listTopics } from '../api/intelligence'
import { listReports } from '../api/reports'
import { getVerificationQueue } from '../api/verification'
import { ViewerAccounts } from './ViewerAccounts'
import { ApiError } from '../api/client'
import type { IntelligenceTopicsResponse } from '../api/types'

const LoginPage = lazy(() =>
  import('./LoginPage').then(m => ({ default: m.LoginPage }))
)

const OverviewDashboard = lazy(() =>
  import('./OverviewDashboard').then(m => ({ default: m.OverviewDashboard }))
)

const ViewerOverview = lazy(() =>
  import('./ViewerOverview').then(m => ({ default: m.ViewerOverview }))
)

const DocumentsPage = lazy(() =>
  import('./Documents').then(m => ({ default: m.DocumentsPage }))
)

const DocumentDetailPage = lazy(() =>
  import('./Documents').then(m => ({ default: m.DocumentDetailPage }))
)

const QueryConsole = lazy(() =>
  import('./QueryConsole').then(m => ({ default: m.QueryConsole }))
)

const AnalyticsPage = lazy(() =>
  import('./Analytics').then(m => ({ default: m.AnalyticsPage }))
)

const GISPage = lazy(() =>
  import('./GIS').then(m => ({ default: m.GISPage }))
)

const ReportsPage = lazy(() =>
  import('./Reports').then(m => ({ default: m.ReportsPage }))
)

const VerificationPage = lazy(() =>
  import('./Verification').then(m => ({ default: m.VerificationPage }))
)

const AuditPage = lazy(() =>
  import('./Audit').then(m => ({ default: m.AuditPage }))
)

type PageKey =
  | 'documents'
  | 'intelligence'
  | 'analytics'
  | 'gis'
  | 'query'
  | 'reports'
  | 'verification'
  | 'audit'
  | 'settings'

const routes: Record<string, PageKey> = {
  '/documents': 'documents',
  '/intelligence': 'intelligence',
  '/analytics': 'analytics',
  '/gis': 'gis',
  '/query': 'query',
  '/reports': 'reports',
  '/verification': 'verification',
  '/audit': 'audit',
  '/settings': 'settings',
}

const descriptions: Record<PageKey, string> = {
  documents: 'Manage source documents and inspect processing status.',
  intelligence: 'Explore topics and evidence from indexed source documents.',
  analytics: 'Review deterministic calculations from verified records.',
  gis: 'Inspect spatial layers and evidence-backed geospatial records.',
  query: 'Search source material and ask evidence-grounded questions.',
  reports: 'Create and review evidence-backed reports.',
  verification: 'Review extraction candidates and verification work.',
  audit: 'Review system provenance and audit events.',
  settings: 'Account and workspace configuration.',
}

export function App() {
  const location = usePath()
  const path = location.split('?')[0]
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <main className="auth-loading">
        <span className="spinner" /> Restoring your session…
      </main>
    )
  }

  if (path === '/login') {
    const requested = new URLSearchParams(
      location.split('?')[1] ?? ''
    ).get('next')

    const destination =
      requested?.startsWith('/') && !requested.startsWith('//')
        ? requested
        : '/'

    return user ? (
      <RouteRedirect path={destination} />
    ) : (
      <Suspense
        fallback={
          <main className="auth-loading">
            <span className="spinner" />
          </main>
        }
      >
        <LoginPage />
      </Suspense>
    )
  }

  if (!user) {
    return (
      <RouteRedirect
        path={`/login?next=${encodeURIComponent(path)}`}
      />
    )
  }

  const viewerPages = new Set(['/', '/analytics', '/gis', '/query', '/intelligence', '/reports', '/audit'])
  if (user.role.toUpperCase() === 'VIEWER' && !viewerPages.has(path)) {
    return <RouteRedirect path="/query" />
  }

  const page = routes[path]

  const documentDetail = path.match(
    /^\/documents\/([^/]+)$/
  )

  if (path !== '/' && !page && !documentDetail) {
    return (
      <AppShell page="not-found">
        <div className="page-content">
          <PageHeader
            title="Page not found"
            description="This route is not part of the DATA MINE workspace."
          />
          <DataState
            kind="empty"
            title="The requested page does not exist."
          />
        </div>
      </AppShell>
    )
  }

  if (documentDetail) {
    return (
      <AppShell page="documents">
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <DocumentDetailPage
            documentId={decodeURIComponent(documentDetail[1])}
          />
        </Suspense>
      </AppShell>
    )
  }

  return (
    <AppShell page={page ?? 'overview'}>
      {page === 'documents' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <DocumentsPage />
        </Suspense>
      ) : page === 'query' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <QueryConsole
            locationSearch={location.split('?')[1] ?? ''}
          />
        </Suspense>
      ) : page === 'analytics' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <AnalyticsPage />
        </Suspense>
      ) : page === 'gis' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <GISPage />
        </Suspense>
      ) : page === 'reports' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <ReportsPage />
        </Suspense>
      ) : page === 'verification' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <VerificationPage />
        </Suspense>
      ) : page === 'audit' ? (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          <AuditPage />
        </Suspense>
      ) : page === 'intelligence' ? (
        <IntelligencePage />
      ) : page ? (
        <ModulePage
          page={page}
          locationSearch={location.split('?')[1] ?? ''}
        />
      ) : (
        <Suspense
          fallback={
            <main className="page-content">
              <DataState kind="loading" />
            </main>
          }
        >
          {user?.role.toUpperCase() === 'VIEWER' ? <ViewerOverview /> : <OverviewDashboard />}
        </Suspense>
      )}
    </AppShell>
  )
}

function RouteRedirect({ path }: { path: string }) {
  useEffect(() => {
    navigate(path)
  }, [path])

  return (
    <main className="auth-loading">
      <span className="spinner" /> Loading…
    </main>
  )
}


/* =========================================================
   INTELLIGENCE PAGE
   ========================================================= */

function IntelligencePage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<IntelligenceTopicsResponse | null>(null)
  const [documentNames, setDocumentNames] = useState<
    Record<string, string>
  >({})
  const [search, setSearch] = useState('')

  const loadTopics = async () => {
    try {
      setLoading(true)
      setError(null)

      // Load intelligence results and real document metadata together.
      const [topicsResponse, documentsResponse] = await Promise.all([
        listTopics(),
        listDocuments(1, 100),
      ])

      setData(topicsResponse)

      const response = documentsResponse as unknown as {
        items?: Array<Record<string, unknown>>
      } | Array<Record<string, unknown>>

      const documents = Array.isArray(response)
        ? response
        : response?.items ?? []

      const documentMap: Record<string, string> = {}

      for (const document of documents) {
        const id =
          typeof document.id === 'string'
            ? document.id
            : typeof document.document_id === 'string'
              ? document.document_id
              : undefined

        const nameCandidates = [
          document.filename,
          document.file_name,
          document.original_filename,
          document.original_file_name,
          document.name,
          document.title,
        ]

        const name = nameCandidates.find(
          value =>
            typeof value === 'string' && value.trim().length > 0
        )

        if (id && typeof name === 'string') {
          documentMap[id] = name
        }
      }

      setDocumentNames(documentMap)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Unable to load intelligence data.'
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTopics()
  }, [])

  const items = data?.items ?? []

  const filteredItems = items.filter(item =>
    item.name
      .toLowerCase()
      .includes(search.toLowerCase())
  )

  const maxFrequency = Math.max(
    ...items.map(item => item.frequency),
    1
  )

  return (
    <div className="page-content">

      <PageHeader
        title="Intelligence"
        description="Explore topics, frequencies and evidence from indexed source documents."
      />

      {loading ? (
        <DataState kind="loading" />
      ) : error ? (
        <DataState
          kind="error"
          detail={error}
          onRetry={loadTopics}
        />
      ) : !items.length ? (
        <DataState
          kind="empty"
          title="No intelligence results"
          detail="No topic analysis results are available."
        />
      ) : (
        <>
          {/* Summary */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns:
                'repeat(auto-fit, minmax(180px, 1fr))',
              gap: '16px',
              marginBottom: '20px',
            }}
          >
            <Card title="Topics">
              <div
                style={{
                  fontSize: '30px',
                  fontWeight: 700,
                }}
              >
                {data?.total ?? items.length}
              </div>
              <div className="muted">
                Identified topic terms
              </div>
            </Card>

            <Card title="Source documents">
              <div
                style={{
                  fontSize: '30px',
                  fontWeight: 700,
                }}
              >
                {Math.max(
                  ...items.map(
                    item => item.document_count ?? 0
                  )
                )}
              </div>
              <div className="muted">
                Documents represented
              </div>
            </Card>

            <Card title="Analysis status">
              <StatusBadge status="Live response" />
              <div
                className="muted"
                style={{ marginTop: '8px' }}
              >
                Run ID: {data?.run_id ?? 'Available'}
              </div>
            </Card>
          </div>

          {/* Topic word view */}
          <Card
            title="Topic overview"
            detail="Topic frequency represented visually"
          >
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '12px 18px',
                alignItems: 'center',
                padding: '20px 8px',
              }}
            >
              {items.slice(0, 30).map(item => {
                const scale =
                  14 +
                  Math.round(
                    (item.frequency / maxFrequency) * 22
                  )

                return (
                  <span
                    key={item.name}
                    title={`${item.name}: ${item.frequency}`}
                    style={{
                      fontSize: `${scale}px`,
                      fontWeight:
                        item.frequency > maxFrequency * 0.5
                          ? 700
                          : 500,
                      lineHeight: 1.2,
                      cursor: 'default',
                    }}
                  >
                    {item.name}
                  </span>
                )
              })}
            </div>
          </Card>

          {/* Search */}
          <Card
            title="Topic explorer"
            detail="Search the real intelligence results"
          >
            <input
              type="search"
              placeholder="Search topics..."
              value={search}
              onChange={e =>
                setSearch(e.target.value)
              }
              style={{
                width: '100%',
                maxWidth: '420px',
                padding: '11px 13px',
                border: '1px solid #d7dcd9',
                borderRadius: '6px',
                marginBottom: '18px',
                fontSize: '14px',
              }}
            />

            <div
              style={{
                overflowX: 'auto',
              }}
            >
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  minWidth: '650px',
                }}
              >
                <thead>
                  <tr>
                    <th style={tableHeader}>Topic</th>
                    <th style={tableHeader}>Frequency</th>
                    <th style={tableHeader}>Confidence</th>
                    <th style={tableHeader}>Documents</th>
                    <th style={tableHeader}>Evidence</th>
                  </tr>
                </thead>

                <tbody>
                  {filteredItems.map(item => (
                    <tr key={item.name}>
                      <td style={tableCell}>
                        <strong>{item.name}</strong>
                      </td>

                      <td style={tableCell}>
                        {item.frequency.toLocaleString()}
                      </td>

                      <td style={tableCell}>
                        {item.confidence !== undefined
                          ? `${Math.round(
                              item.confidence * 100
                            )}%`
                          : '—'}
                      </td>

                      <td style={tableCell}>
                        {item.document_count ?? '—'}
                      </td>

                      <td style={tableCell}>
                        {item.evidence?.length ?? 0} references
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {!filteredItems.length && (
              <DataState
                kind="empty"
                title="No matching topics"
                detail="Try another topic name."
              />
            )}
          </Card>

          {/* Evidence */}
          {filteredItems.slice(0, 10).map(item => (
            <Card
              key={`evidence-${item.name}`}
              title={item.name}
              detail={`${item.frequency.toLocaleString()} occurrences`}
            >
              {item.evidence &&
              item.evidence.length > 0 ? (
                <div>
                  <div
                    style={{
                      fontWeight: 600,
                      marginBottom: '10px',
                    }}
                  >
                    Source evidence
                  </div>

                  {item.evidence
                    .slice(0, 5)
                    .map((evidence, index) => {
                      const documentName = evidence.document_id
                        ? documentNames[evidence.document_id]
                        : undefined

                      return (
                        <div
                          key={`${item.name}-${index}-${evidence.document_id ?? 'unknown'}`}
                          style={{
                            padding: '12px 0',
                            borderTop:
                              '1px solid #e5e8e6',
                            display: 'flex',
                            justifyContent:
                              'space-between',
                            alignItems: 'center',
                            gap: '20px',
                          }}
                        >
                          <div
                            style={{
                              minWidth: 0,
                              flex: 1,
                            }}
                          >
                            <div
                              style={{
                                fontWeight: 600,
                                wordBreak: 'break-word',
                              }}
                            >
                              {documentName ??
                                'Source document'}
                            </div>

                            <div
                              className="muted"
                              style={{
                                marginTop: '3px',
                              }}
                            >
                              Page{' '}
                              {evidence.page_number ??
                                'Unknown'}
                            </div>
                          </div>

                          <span
                            className="muted"
                            style={{
                              whiteSpace: 'nowrap',
                              fontSize: '12px',
                            }}
                          >
                            SOURCE_DOCUMENT
                          </span>
                        </div>
                      )
                    })}
                </div>
              ) : (
                <div className="muted">
                  No evidence references returned.
                </div>
              )}
            </Card>
          ))}
        </>
      )}
    </div>
  )
}


/* =========================================================
   EXISTING GENERIC MODULE PAGE
   ========================================================= */

function ModulePage({
  page,
  locationSearch,
}: {
  page: PageKey
  locationSearch: string
}) {
  const { user } = useAuth()

  const roleAllowed =
    page !== 'verification' ||
    ['ADMIN', 'VERIFIER'].includes(
      user?.role.toUpperCase() ?? ''
    )

  const [refresh, setRefresh] = useState(0)

  const [state, setState] = useState<{
    loading: boolean
    error: string | null
    forbidden: boolean
    data: unknown
  }>({
    loading: page !== 'audit' && roleAllowed,
    error: null,
    forbidden: !roleAllowed,
    data: null,
  })

  useEffect(() => {
    const controller = new AbortController()

    setState({
      loading: page !== 'audit' && roleAllowed,
      error: null,
      forbidden: !roleAllowed,
      data: null,
    })

    if (page === 'audit' || !roleAllowed) {
      return () => controller.abort()
    }

    const load = (
      {
        documents: (signal?: AbortSignal) =>
          listDocuments(1, 25, signal),

        intelligence: listTopics,

        analytics: getProductionSummary,

        gis: listGisLayers,

        reports: listReports,

        verification: (signal?: AbortSignal) =>
          getVerificationQueue('PENDING', signal),

        audit: async () => null,

        query: async () => null,

        settings: async () => null,
      } as Record<
        PageKey,
        (signal?: AbortSignal) => Promise<unknown>
      >
    )[page]

    load(controller.signal)
      .then(data =>
        setState({
          loading: false,
          error: null,
          forbidden: false,
          data,
        })
      )
      .catch(error => {
        if (!controller.signal.aborted) {
          setState({
            loading: false,
            error:
              error instanceof Error
                ? error.message
                : 'The request could not be completed.',
            forbidden:
              error instanceof ApiError &&
              error.status === 403,
            data: null,
          })
        }
      })

    return () => controller.abort()
  }, [page, refresh, roleAllowed])

  const label =
    page === 'query'
      ? 'Query Console'
      : page[0].toUpperCase() + page.slice(1)

  return (
    <div className="page-content">
      <PageHeader
        title={label}
        description={descriptions[page]}
      />

      {page === 'audit' ? (
        <Card title="Audit events">
          <DataState
            kind="empty"
            title="Audit endpoint unavailable"
            detail="The current backend OpenAPI contract does not expose an audit route. No endpoint has been assumed."
          />
        </Card>
      ) : page === 'settings' ? (
        <>
          {user?.role.toUpperCase() === 'ADMIN' ? <ViewerAccounts /> : <Card title="Account">
            <p className="muted">
              Authentication uses the existing backend
              session. Account settings are not exposed in
              the current API contract.
            </p>
          </Card>}
        </>
      ) : (
        <Card
          title={
            page === 'analytics'
              ? 'Production data availability'
              : `${label} records`
          }
          detail="Live API response"
        >
          <div className="module-result">
            {state.loading ? (
              <DataState kind="loading" />
            ) : state.forbidden ? (
              <DataState
                kind="forbidden"
                detail="This module is available to Admin and Verifier roles."
              />
            ) : state.error ? (
              <DataState
                kind="error"
                detail={state.error}
                onRetry={() =>
                  setRefresh(x => x + 1)
                }
              />
            ) : (
              <LiveResult
                page={page}
                data={state.data}
              />
            )}
          </div>
        </Card>
      )}
    </div>
  )
}

function LiveResult({
  page,
  data,
}: {
  page: PageKey
  data: unknown
}) {
  if (
    page === 'analytics' &&
    data &&
    typeof data === 'object' &&
    'status' in data
  ) {
    const value = data as {
      status: string
      items?: unknown[]
      record_count?: number
    }

    if (
      value.status ===
      'INSUFFICIENT_VERIFIED_DATA'
    ) {
      return (
        <DataState
          kind="insufficient"
          detail="Verified source records are required before numerical results can be shown."
        />
      )
    }

    if (!value.items?.length) {
      return (
        <DataState
          kind="empty"
          title="No verified production records returned"
        />
      )
    }
  }

  const list = Array.isArray(data)
    ? data
    : data &&
      typeof data === 'object' &&
      'items' in data &&
      Array.isArray(
        (data as { items: unknown[] }).items
      )
    ? (data as { items: unknown[] }).items
    : null

  if (
    page === 'gis' &&
    list?.length === 0
  ) {
    return (
      <DataState
        kind="insufficient"
        title="INSUFFICIENT VERIFIED SPATIAL DATA"
        detail="No verified spatial records were returned by the backend."
      />
    )
  }

  if (list?.length === 0) {
    return <DataState kind="empty" />
  }

  return (
    <>
      <StatusBadge status="Live response" />
      <pre className="result-json">
        {JSON.stringify(data, null, 2)}
      </pre>
    </>
  )
}

const tableHeader: React.CSSProperties = {
  textAlign: 'left',
  padding: '12px',
  borderBottom: '1px solid #dfe4e1',
  fontSize: '12px',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
}

const tableCell: React.CSSProperties = {
  padding: '13px 12px',
  borderBottom: '1px solid #e8ebe9',
  fontSize: '14px',
}
