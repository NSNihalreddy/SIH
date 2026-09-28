import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import {
  calculateDeterministically, getAnomalyAnalytics, getProductionAnalytics,
  getStatisticsAnalytics, getTrendAnalytics, getValidationAnalytics,
  type AnomaliesResponse, type CalculationResponse, type ProductionResponse,
  type StatisticsResponse, type TrendsResponse, type ValidationResponse
} from '../api/analytics'
import { Card, DataState, PageHeader, StatusBadge } from '../components/ui'
import { useAuth } from '../auth/AuthProvider'
import './analytics.css'

type Loadable<T> = { loading: boolean; error: string | null; data: T | null }
type AnalyticsData = {
  production: Loadable<ProductionResponse>
  trends: Loadable<TrendsResponse>
  statistics: Loadable<StatisticsResponse>
  anomalies: Loadable<AnomaliesResponse>
  validations: Loadable<ValidationResponse>
}
const empty = <T,>(): Loadable<T> => ({ loading: true, error: null, data: null })
const initial: AnalyticsData = { production: empty(), trends: empty(), statistics: empty(), anomalies: empty(), validations: empty() }
const calculations = [
  ['sum', 'Sum'], ['average', 'Average'], ['minimum', 'Minimum'], ['maximum', 'Maximum'],
  ['median', 'Median'], ['standard_deviation', 'Standard deviation'],
  ['production_difference', 'Production difference (two records)'],
  ['growth_percentage', 'Growth percentage (two records)'], ['production_share', 'Production share'],
  ['cagr', 'Compound annual growth rate (two records)']
] as const

export function AnalyticsPage() {
  const { user } = useAuth()
  const [data, setData] = useState<AnalyticsData>(initial)
  const [reload, setReload] = useState(0)
  const [calculationType, setCalculationType] = useState<string>('sum')
  const [years, setYears] = useState('')
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [calculation, setCalculation] = useState<{loading:boolean; error:string|null; result:CalculationResponse|null}>({loading:false,error:null,result:null})
  const canCalculate = ['ADMIN', 'ANALYST', 'VERIFIER'].includes(user?.role.toUpperCase() ?? '')

  useEffect(() => {
    const controller = new AbortController()
    const setters = {
      production: getProductionAnalytics,
      trends: getTrendAnalytics,
      statistics: getStatisticsAnalytics,
      anomalies: getAnomalyAnalytics,
      validations: getValidationAnalytics
    } as const
    for (const key of Object.keys(setters) as (keyof AnalyticsData)[]) {
      setData(current => ({ ...current, [key]: {loading:true,error:null,data:current[key].data} }))
      setters[key](controller.signal).then(value => {
        if (!controller.signal.aborted) setData(current => ({...current,[key]:{loading:false,error:null,data:value}}))
      }).catch(error => {
        if (!controller.signal.aborted) setData(current => ({...current,[key]:{loading:false,error:error instanceof Error ? error.message : 'The request could not be completed.',data:null}}))
      })
    }
    return () => controller.abort()
  }, [reload])

  const sourceOptions = useMemo(() => {
    const groups = data.production.data?.items ?? []
    const options = new Map<string, string>()
    groups.forEach(group => group.source_record_ids?.forEach(id => options.set(id, `${group.key} · ${id.slice(0, 8)}`)))
    return [...options.entries()].map(([id, label]) => ({id,label}))
  }, [data.production.data])
  const verifiedRecords = data.production.data?.record_count ?? 0
  const availablePeriods = data.trends.data?.observations?.length ?? 0
  const statisticalFlags = data.anomalies.data?.items?.filter(item => item.status === 'STATISTICAL_ANOMALY').length ?? 0
  const validationCount = data.validations.data?.total ?? 0

  const retry = useCallback((key: keyof AnalyticsData) => {
    const controller = new AbortController()
    const request = ({ production: getProductionAnalytics, trends: getTrendAnalytics, statistics: getStatisticsAnalytics, anomalies: getAnomalyAnalytics, validations: getValidationAnalytics })[key]
    setData(current => ({...current,[key]:{loading:true,error:null,data:current[key].data}}))
    request(controller.signal).then(value => setData(current => ({...current,[key]:{loading:false,error:null,data:value}}))).catch(error => {
      if (!(error instanceof DOMException && error.name === 'AbortError')) setData(current => ({...current,[key]:{loading:false,error:error instanceof Error ? error.message : 'The request could not be completed.',data:null}}))
    })
  }, [])

  async function submitCalculation(event: FormEvent) {
    event.preventDefault()
    setCalculation({loading:true,error:null,result:null})
    try {
      const response = await calculateDeterministically({calculation_type:calculationType,source_record_ids:selectedIds,entity_type:'PORTFOLIO',...(calculationType==='cagr'?{years:Number(years)}:{})})
      setCalculation({loading:false,error:null,result:response})
    } catch (error) {
      setCalculation({loading:false,error:error instanceof ApiError ? `${error.message}${error.status ? ` (HTTP ${error.status})` : ''}` : error instanceof Error ? error.message : 'The calculation could not be completed.',result:null})
    }
  }
  function toggleSource(id: string) {
    setSelectedIds(current => current.includes(id) ? current.filter(value => value !== id) : current.length >= 100 ? current : [...current,id])
    setCalculation({loading:false,error:null,result:null})
  }
  const pairCalculation = ['production_difference','growth_percentage','production_share','cagr'].includes(calculationType)
  const selectionValid = (pairCalculation ? selectedIds.length === 2 : selectedIds.length >= 1) && (calculationType !== 'cagr' || (Number.isInteger(Number(years)) && Number(years)>0))
  const hasVerifiedData = (data.production.data?.items?.length ?? 0) > 0 && data.production.data?.status !== 'INSUFFICIENT_VERIFIED_DATA'

  return <main className="page-content analytics-page">
    <PageHeader title="Analytics" description="Deterministic mining and production analysis from verified records." action={<button className="button button-secondary" onClick={() => setReload(value => value + 1)}>Refresh data</button>}/>
    <section className="analytics-trust-banner" aria-label="Analytics data boundary">
      <span className="trust-mark">!</span><div><strong>Verified data only</strong><p>Results are calculated from trusted, verified records returned by the backend. Statistical flags are signals for review, not real-world findings. No AI-generated numbers are used in this page.</p></div>
    </section>
    <section className="analytics-kpis" aria-label="Analytics summary">
      <SummaryMetric label="Verified production records" value={data.production.loading ? 'Loading' : data.production.error ? 'Unavailable' : String(verifiedRecords)} note={productionState(data.production)}/>
      <SummaryMetric label="Observed periods" value={data.trends.loading ? 'Loading' : data.trends.error ? 'Unavailable' : String(availablePeriods)} note={data.trends.data?.status ?? 'Waiting for API'}/>
      <SummaryMetric label="Statistical flags" value={data.anomalies.loading ? 'Loading' : data.anomalies.error ? 'Unavailable' : String(statisticalFlags)} note={data.anomalies.data?.status ?? 'Statistical signal only'}/>
      <SummaryMetric label="Saved validation results" value={data.validations.loading ? 'Loading' : data.validations.error ? 'Unavailable' : String(validationCount)} note="Persisted results returned by API"/>
    </section>
    {!data.production.loading && !data.production.error && data.production.data?.status === 'INSUFFICIENT_VERIFIED_DATA' && <div className="analytics-insufficient" role="status"><strong>Insufficient verified production data</strong><p>Production summaries, trends and statistical measures are unavailable until verified source records exist. Empty states below represent missing trusted inputs; they are not zero-production findings.</p></div>}

    <div className="analytics-grid">
      <Card title="Production analytics" detail="Verified production grouped by year">
        {sectionState(data.production,'production',retry) ?? (data.production.data?.status === 'INSUFFICIENT_VERIFIED_DATA' || !data.production.data?.items.length ? <DataState kind="insufficient" title="INSUFFICIENT VERIFIED DATA" detail="No production chart is shown because there are no verified production groups."/> : <ProductionView response={data.production.data}/>)}
      </Card>
      <Card title="Trends" detail="Observed periods and deterministic changes">
        {sectionState(data.trends,'trends',retry) ?? (data.trends.data?.status === 'INSUFFICIENT_VERIFIED_DATA' || !data.trends.data?.observations.length ? <DataState kind="insufficient" detail="No trend is inferred without verified period observations."/> : <TrendView response={data.trends.data}/>)}
      </Card>
      <Card title="Statistics" detail="Descriptive statistics from verified production">
        {sectionState(data.statistics,'statistics',retry) ?? (!data.statistics.data || data.statistics.data.status === 'INSUFFICIENT_VERIFIED_DATA' ? <DataState kind="insufficient" detail="The backend did not return sufficient verified records for statistical analysis."/> : <StatisticsView response={data.statistics.data}/>)}
      </Card>
      <Card title="Anomaly review" detail="Backend statistical screening">
        <div className="statistical-note"><strong>Statistical flag ≠ real-world finding</strong><span>Flags identify values for human review; they do not establish a mining incident or operational cause.</span></div>
        {sectionState(data.anomalies,'anomalies',retry) ?? (!data.anomalies.data || data.anomalies.data.status === 'INSUFFICIENT_VERIFIED_DATA' ? <DataState kind="insufficient" detail="Anomaly screening requires verified data."/> : <AnomalyView response={data.anomalies.data}/>)}
      </Card>
      <Card title="Validation results" detail="Previously persisted analytics validation results">
        {sectionState(data.validations,'validations',retry) ?? (data.validations.data ? <ValidationView response={data.validations.data}/> : <DataState kind="empty"/>)}
      </Card>
      <Card title="Deterministic calculation" detail="Explicitly submitted calculation using verified source record IDs">
        <p className="analytics-explainer">This calculation is performed by the deterministic analytics service, not interpreted or generated by an AI model. Submitting creates a calculation result and audit event.</p>
        {!canCalculate && <DataState kind="forbidden" title="Role cannot run calculations" detail="Calculation access is limited to Admin, Analyst and Verifier roles."/>}
        {canCalculate && !hasVerifiedData && <DataState kind="insufficient" title="No verified source records available" detail="The form is disabled until the production API returns selectable verified source record IDs."/>}
        {canCalculate && hasVerifiedData && <form className="calculation-form" onSubmit={submitCalculation}>
          <label>Calculation<select value={calculationType} onChange={event => {setCalculationType(event.target.value);setCalculation({loading:false,error:null,result:null})}}>{calculations.map(([value,label])=><option key={value} value={value}>{label}</option>)}<option value="stripping_ratio" disabled>Stripping ratio — required verified fields unavailable</option><option value="productivity" disabled>Productivity — required verified fields unavailable</option></select></label>
          {calculationType==='cagr' && <label>Elapsed years<input type="number" min="1" max="1000" step="1" required value={years} onChange={event=>setYears(event.target.value)} placeholder="Enter the elapsed years"/></label>}
          <fieldset><legend>Verified source records {pairCalculation ? '(select exactly 2)' : '(select at least 1)'}</legend>{sourceOptions.length === 0 ? <p className="muted">The production response did not include usable source record IDs.</p> : <div className="source-options">{sourceOptions.map(option=><label key={option.id}><input type="checkbox" checked={selectedIds.includes(option.id)} onChange={()=>toggleSource(option.id)} disabled={!selectedIds.includes(option.id) && selectedIds.length>=100}/><span>{option.label}</span></label>)}</div>}</fieldset>
          <button className="button button-primary" type="submit" disabled={!selectionValid || calculation.loading || !sourceOptions.length}>{calculation.loading ? 'Calculating…' : 'Run deterministic calculation'}</button>
          {calculation.error && <DataState kind="error" title="Calculation failed" detail={calculation.error}/>}
          {calculation.result && <CalculationResult result={calculation.result}/>}
        </form>}
      </Card>
    </div>
    <p className="analytics-footnote">Counts and statuses above describe API records and service state. A count of zero is not a production measurement.</p>
  </main>
}

function SummaryMetric({label,value,note}:{label:string;value:string;note:string}) { return <article className="analytics-metric"><span>{label}</span><strong>{value}</strong><small>{note.replace(/_/g,' ')}</small></article> }
function productionState(state: Loadable<ProductionResponse>) { return state.loading ? 'Loading API state' : state.error ? 'Request failed' : state.data?.status ?? 'No response' }
function sectionState<T>(state:Loadable<T>,key:keyof AnalyticsData,retry:(key:keyof AnalyticsData)=>void) { if(state.loading) return <DataState kind="loading"/>; if(state.error) return <DataState kind={state.error.includes('401') ? 'unauthorized' : state.error.includes('403') ? 'forbidden' : 'error'} detail={state.error} onRetry={()=>retry(key)}/>; if(!state.data) return <DataState kind="error" detail="No response data was returned." onRetry={()=>retry(key)}/>; return null }
function ProvenanceDetails({ids,provenance}:{ids?:string[];provenance?:unknown}) { const hasProvenance=Array.isArray(provenance)?provenance.length>0:!!provenance&&typeof provenance==='object'?Object.keys(provenance).length>0:typeof provenance==='string'&&provenance.length>0; if(!ids?.length && !hasProvenance) return <span className="muted">No provenance references returned</span>; const count=(ids?.length??0)+(Array.isArray(provenance)?provenance.length:hasProvenance?1:0); return <details className="analytics-provenance"><summary>Evidence &amp; provenance ({count})</summary>{!!ids?.length && <div><strong>Source record IDs</strong><ul>{ids.map(id=><li key={id} className="analytics-id">{id}</li>)}</ul></div>}{hasProvenance && <pre>{JSON.stringify(provenance,null,2)}</pre>}</details> }
function ProductionView({response}:{response:ProductionResponse}) { const max = Math.max(...response.items.map(item=>Number(item.total)).filter(Number.isFinite),0); return <><div className="analytics-status-row"><StatusBadge status={response.status}/><span>{response.record_count} verified source records · {response.items.length} returned groups</span></div><div className="production-groups">{response.items.map((item,index)=><div className="production-group" key={`${item.key}-${index}`}><div className="production-label"><strong>{item.key || 'Unspecified period'}</strong><span>{item.total} {item.unit ?? ''}</span></div><div className="production-track"><span style={{width:`${max>0?Math.max(2,Number(item.total)/max*100):0}%`}}/></div><small>{item.record_count} source records</small><ProvenanceDetails ids={item.source_record_ids} provenance={item.provenance}/></div>)}</div></> }
function TrendView({response}:{response:TrendsResponse}) { return <><div className="analytics-status-row"><StatusBadge status={response.status}/><span>{response.observations.length} observed periods</span></div><div className="table-wrap"><table><thead><tr><th>Period</th><th>Observed value</th><th>Unit</th><th>Records</th></tr></thead><tbody>{response.observations.map((item,index)=><tr key={`${item.year}-${index}`}><td>{item.year}</td><td>{formatValue(item.value)}</td><td>{item.unit ?? '—'}</td><td>{item.record_count}</td></tr>)}</tbody></table></div>{response.period_changes.length>0 && <><h3 className="analytics-subheading">Period changes</h3><div className="table-wrap"><table><thead><tr><th>From → To</th><th>Change</th><th>Change %</th><th>Unit</th></tr></thead><tbody>{response.period_changes.map((item,index)=><tr key={`${item.from_year}-${item.to_year}-${index}`}><td>{item.from_year} → {item.to_year}</td><td>{formatValue(item.change)}</td><td>{item.change_percent===null?'Not available':`${formatValue(item.change_percent)}%`}</td><td>{item.unit??'—'}</td></tr>)}</tbody></table></div>{response.period_changes.map((item,index)=><ProvenanceDetails key={`change-${index}`} ids={item.source_record_ids}/>)}</>}{response.cagr_percent !== null && <p className="analytics-method">CAGR: {formatValue(response.cagr_percent)}% · {response.cagr_method ?? 'Backend method not specified'}</p>}{response.observations.map((item,index)=><ProvenanceDetails key={`${item.year}-prov-${index}`} ids={item.source_record_ids} provenance={item.provenance}/>)}</> }
function StatisticsView({response}:{response:StatisticsResponse}) { const stats=response.statistics; const rows:[string,number|string|{requested:string;value:string}|null,string?][]=[['Count',stats.count],['Sum',stats.sum,stats.unit??undefined],['Mean',stats.mean,stats.unit??undefined],['Median',stats.median,stats.unit??undefined],['Minimum',stats.minimum,stats.unit??undefined],['Maximum',stats.maximum,stats.unit??undefined],['Standard deviation',stats.standard_deviation,stats.unit??undefined],['Percentile',stats.percentile]]; return <><div className="analytics-status-row"><StatusBadge status={response.status}/><span>Method: {stats.method ?? 'not specified'}</span></div>{stats.count === null ? <DataState kind="insufficient" detail="Statistics are unavailable without verified records."/> : <dl className="statistics-list">{rows.map(([label,value,unit])=><div key={label}><dt>{label}</dt><dd>{value === null ? 'Not returned' : typeof value==='object' ? `${value.value} (p${value.requested})` : `${formatValue(value)}${unit ? ` ${unit}` : ''}`}</dd></div>)}</dl>}<ProvenanceDetails ids={response.source_record_ids} provenance={response.provenance}/></> }
function AnomalyView({response}:{response:AnomaliesResponse}) { const flags=response.items.filter(item=>item.status==='STATISTICAL_ANOMALY'); return <><div className="analytics-status-row"><StatusBadge status={response.classification}/><span>{response.method} · threshold {response.threshold} · {flags.length} flags across {response.items.length} screened observations</span></div>{response.items.length ? <div className="table-wrap"><table><thead><tr><th>Period</th><th>Observed value</th><th>Screening score</th><th>Screening result</th></tr></thead><tbody>{response.items.map((item,index)=><tr key={item.record_id ?? index}><td>{String(item.period ?? 'Not returned')}</td><td>{item.value === undefined ? 'Not returned' : `${formatValue(item.value)} ${String(item.unit??'')}`}</td><td>{item.score === undefined ? 'Not returned' : formatValue(item.score)}</td><td><StatusBadge status={String(item.status ?? 'Result not returned')}/></td></tr>)}</tbody></table>{response.items.map((item,index)=><ProvenanceDetails key={`${index}-prov`} ids={item.record_id?[item.record_id]:undefined} provenance={item.provenance}/>)}</div> : <p className="muted">No statistical flags were returned by this screening method. This does not establish that no real-world issue exists.</p>}<p className="analytics-method">{response.interpretation}</p></> }
function ValidationView({response}:{response:ValidationResponse}) { if(!response.items.length) return <DataState kind="empty" title="No saved validation results" detail="The API returned no persisted validation results. This does not mean records passed validation."/>; return <><div className="analytics-status-row"><span>{response.total} saved results · page {response.page}</span></div><div className="validation-list">{response.items.map((item,index)=><article key={item.id ?? index}><div><strong>{String(item.validation_type ?? item.id ?? 'Validation result')}</strong><small>{String(item.severity ?? 'Severity not returned')} · {item.created_at ? new Date(item.created_at).toLocaleString() : 'Timestamp not returned'}</small>{item.detail && <span>{item.detail}</span>}</div><StatusBadge status={String(item.status ?? 'Status not returned')}/><ProvenanceDetails ids={item.compared_records} provenance={item.provenance}/></article>)}</div></> }
function CalculationResult({result}:{result:CalculationResponse}) { return <section className="calculation-result" aria-live="polite"><div><strong>Backend calculation result</strong><StatusBadge status={String(result.status ?? 'Completed')}/></div><dl>{(['result','value','calculation_id','formula_id','formula_version'] as const).filter(key=>result[key]!==undefined).map(key=><div key={key}><dt>{key.replace(/_/g,' ')}</dt><dd>{String(result[key])}</dd></div>)}</dl><ProvenanceDetails ids={undefined} provenance={result.provenance}/></section> }
function formatValue(value:number|string) { const numeric=Number(value); return Number.isFinite(numeric)?new Intl.NumberFormat(undefined,{maximumFractionDigits:4}).format(numeric):String(value) }
