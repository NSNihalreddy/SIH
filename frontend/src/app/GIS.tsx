import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { calculateEntityDistance, findBoreholeMineCandidates, findEntitiesInBounds, findNearbyEntities, listGisEntities, listGisLayers, type GisDistanceResponse, type GisEntityResponse, type GisFeature, type GisLayerResponse, type GisRelationshipResponse, type GisSearchResponse } from '../api/gis'
import { Card, DataState, PageHeader, StatusBadge } from '../components/ui'
import './gis.css'

type RequestState<T> = {loading:boolean;error:string|null;status?:number;data:T|null}
type Mode = 'nearby'|'bbox'|'distance'|'relationships'
type SearchResult = {kind:'features';title:string;data:GisSearchResponse}|{kind:'distance';title:string;data:GisDistanceResponse}|{kind:'relationships';title:string;data:GisRelationshipResponse}
const loadingState = <T,>():RequestState<T> => ({loading:true,error:null,data:null})
const entityKinds=['MINE','BOREHOLE','VERIFIED_COORDINATE']

export function GISPage() {
  const [layers,setLayers]=useState<RequestState<GisLayerResponse>>(loadingState())
  const [entities,setEntities]=useState<RequestState<GisEntityResponse>>(loadingState())
  const [reload,setReload]=useState(0)
  const [activeTypes,setActiveTypes]=useState<string[]>([])
  const [mode,setMode]=useState<Mode>('nearby')
  const [entityFilter,setEntityFilter]=useState('ALL')
  const [entitySearch,setEntitySearch]=useState('')
  const [selectedEntity,setSelectedEntity]=useState<GisFeature|null>(null)
  const [queryState,setQueryState]=useState<{loading:boolean;error:string|null;status?:number}>({loading:false,error:null})
  const [result,setResult]=useState<SearchResult|null>(null)
  const [latitude,setLatitude]=useState(''); const [longitude,setLongitude]=useState(''); const [radius,setRadius]=useState('')
  const [west,setWest]=useState(''); const [south,setSouth]=useState(''); const [east,setEast]=useState(''); const [north,setNorth]=useState('')
  const [leftId,setLeftId]=useState(''); const [rightId,setRightId]=useState('')
  const queryForm=useRef<HTMLFormElement>(null)

  useEffect(()=>{
    const controller=new AbortController()
    setLayers({loading:true,error:null,data:null}); setEntities({loading:true,error:null,data:null})
    listGisLayers(controller.signal).then(data=>{if(!controller.signal.aborted){setLayers({loading:false,error:null,data});setActiveTypes(data.layers.filter(layer=>layer.count>0).map(layer=>layer.entity_type))}}).catch(error=>{if(!controller.signal.aborted)setLayers(failure(error))})
    listGisEntities(controller.signal).then(data=>{if(!controller.signal.aborted)setEntities({loading:false,error:null,data})}).catch(error=>{if(!controller.signal.aborted)setEntities(failure(error))})
    return ()=>controller.abort()
  },[reload])

  const trustedEntities=entities.data?.items??[]
  const features=(layers.data?.features??[]).map(normalizeFeature).filter(feature=>activeTypes.includes(feature.entity_type??''))
  const eligibleForDistance=trustedEntities.filter(item=>item.entity_type==='MINE'||item.entity_type==='BOREHOLE')
  const filteredEntities=useMemo(()=>trustedEntities.filter(item=>{
    const type=item.entity_type??''
    const matchType=entityFilter==='ALL'||type===entityFilter
    const name=(item.name??'').toLocaleLowerCase()
    const id=item.id.toLocaleLowerCase()
    const query=entitySearch.trim().toLocaleLowerCase()
    return matchType&&(!query||name.includes(query)||id.includes(query))
  }),[trustedEntities,entityFilter,entitySearch])

  async function submitSearch(event:FormEvent){
    event.preventDefault(); setQueryState({loading:true,error:null}); setResult(null); setSelectedEntity(null)
    try {
      let value:SearchResult
      if(mode==='nearby'){
        const lat=requiredNumber(latitude,'Latitude',-90,90),lon=requiredNumber(longitude,'Longitude',-180,180),radiusM=requiredNumber(radius,'Radius',0.000001,500000)
        value={kind:'features',title:'Nearby spatial entities',data:await findNearbyEntities({latitude:lat,longitude:lon,radius_m:radiusM,...(entityFilter!=='ALL'?{entity_type:entityFilter}:{})})}
      } else if(mode==='bbox'){
        const w=requiredNumber(west,'West',-180,180),s=requiredNumber(south,'South',-90,90),e=requiredNumber(east,'East',-180,180),n=requiredNumber(north,'North',-90,90)
        if(w>=e||s>=n) throw new Error('Bounding box requires west < east and south < north.')
        value={kind:'features',title:'Bounding-box results',data:await findEntitiesInBounds({west:w,south:s,east:e,north:n,...(entityFilter!=='ALL'?{entity_type:entityFilter}:{})})}
      } else if(mode==='distance'){
        const left=eligibleForDistance.find(item=>item.id===leftId),right=eligibleForDistance.find(item=>item.id===rightId)
        if(!left||!right) throw new Error('Select two trusted mine or borehole entities returned by the backend.')
        value={kind:'distance',title:'Deterministic distance result',data:await calculateEntityDistance({left_type:left.entity_type!,left_id:left.id,right_type:right.entity_type!,right_id:right.id})}
      } else {
        const radiusM=requiredNumber(radius,'Candidate radius',0.000001,500000)
        value={kind:'relationships',title:'Borehole-to-mine proximity candidates',data:await findBoreholeMineCandidates(radiusM)}
      }
      setResult(value); setQueryState({loading:false,error:null})
    }catch(error){setQueryState({loading:false,error:error instanceof Error?error.message:'The spatial query could not be completed.',status:error instanceof ApiError?error.status:undefined})}
  }

  return <main className="page-content gis-page">
    <PageHeader title="GIS & Spatial Intelligence" description="Explore trusted spatial entities and run evidence-backed spatial queries." action={<button className="button button-secondary" onClick={()=>setReload(value=>value+1)}>Refresh layers</button>}/>
    <div className="gis-trust-note"><strong>Trusted geometry only</strong><span>Map features come only from verified geometries returned by the GIS API. Proximity results are candidates; they do not prove ownership, identity, or a geological relationship.</span></div>
    <div className="gis-layout">
      <aside className="gis-controls">
        <Card title="Spatial layers" detail="Verified entities available from PostGIS">
          {layers.loading?<DataState kind="loading"/>:layers.error?<ApiFailure error={layers.error} status={layers.status} retry={()=>setReload(x=>x+1)}/>:<div className="gis-layer-list">{(layers.data?.layers??[]).map(layer=><label key={layer.entity_type} className={!layer.count?'unavailable':''}><input type="checkbox" checked={activeTypes.includes(layer.entity_type)} disabled={!layer.count} onChange={()=>setActiveTypes(current=>current.includes(layer.entity_type)?current.filter(type=>type!==layer.entity_type):[...current,layer.entity_type])}/><span><strong>{layerLabel(layer.entity_type)}</strong><small>{layer.count?`${layer.count} verified entities`:'No verified spatial records are currently available for this layer.'}</small></span><StatusBadge status={layer.count?'AVAILABLE':'UNAVAILABLE'}/></label>)}{!layers.data?.layers.length&&<DataState kind="insufficient" title="No spatial layers returned" detail="The GIS service did not return layer definitions."/>}</div>}
        </Card>
        <Card title="Spatial search" detail="Queries run only when submitted">
          <div className="gis-mode-tabs" role="tablist" aria-label="Spatial query type">{([['nearby','Nearby'],['bbox','Bounding box'],['distance','Distance'],['relationships','Proximity candidates']] as [Mode,string][]).map(([value,label])=><button key={value} type="button" role="tab" aria-selected={mode===value} className={mode===value?'selected':''} onClick={()=>{setMode(value);setQueryState({loading:false,error:null});setResult(null)}}>{label}</button>)}</div>
          {mode!=='distance'&&mode!=='relationships'&&<label className="gis-field">Entity layer<select value={entityFilter} onChange={event=>setEntityFilter(event.target.value)}><option value="ALL">All available entity types</option>{entityKinds.map(type=><option key={type} value={type}>{layerLabel(type)}</option>)}</select></label>}
          <form ref={queryForm} className="gis-query-form" onSubmit={submitSearch}>
            {mode==='nearby'&&<><div className="gis-field-row"><NumberField label="Latitude" value={latitude} setValue={setLatitude} min={-90} max={90} step="any"/><NumberField label="Longitude" value={longitude} setValue={setLongitude} min={-180} max={180} step="any"/></div><NumberField label="Radius (metres)" value={radius} setValue={setRadius} min={0} max={500000} step="any"/><small className="gis-help">Enter a real query location; no default coordinates are supplied.</small></>}
            {mode==='bbox'&&<><div className="gis-field-row"><NumberField label="West longitude" value={west} setValue={setWest} min={-180} max={180} step="any"/><NumberField label="South latitude" value={south} setValue={setSouth} min={-90} max={90} step="any"/></div><div className="gis-field-row"><NumberField label="East longitude" value={east} setValue={setEast} min={-180} max={180} step="any"/><NumberField label="North latitude" value={north} setValue={setNorth} min={-90} max={90} step="any"/></div><small className="gis-help">Use bounds from your actual area of interest.</small></>}
            {mode==='distance'&&<><EntitySelect label="First entity" value={leftId} setValue={setLeftId} entities={eligibleForDistance}/><EntitySelect label="Second entity" value={rightId} setValue={setRightId} entities={eligibleForDistance}/><small className="gis-help">Distance is calculated for selected trusted mine/borehole geometry and recorded by the backend.</small></>}
            {mode==='relationships'&&<><NumberField label="Candidate radius (metres)" value={radius} setValue={setRadius} min={0} max={500000} step="any"/><small className="gis-help">Returns spatial proximity candidates only; it does not establish a mine association.</small></>}
            <button className="button button-primary" type="submit" disabled={queryState.loading||((mode==='distance')&&eligibleForDistance.length===0)}>{queryState.loading?'Searching…':mode==='distance'?'Calculate distance':mode==='relationships'?'Find candidates':'Run spatial search'}</button>
          </form>
          {mode==='distance'&&entities.data?.status==='INSUFFICIENT_VERIFIED_SPATIAL_DATA'&&<DataState kind="insufficient" detail="Distance queries require two trusted entities with verified geometry."/>}
          {queryState.error&&<ApiFailure error={queryState.error} status={queryState.status} retry={()=>queryForm.current?.requestSubmit()}/>}
        </Card>
      </aside>
      <section className="gis-map-column" aria-label="GIS map and query results">
        <Card title="Spatial map" detail={features.length?`${features.length} verified features displayed`:'Verified GeoJSON features only'} className="gis-map-card">
          <div className="gis-map-toolbar"><span><i className={features.length?'gis-dot active':'gis-dot'}/>{features.length?'Verified geometries loaded':'Spatial data unavailable'}</span><small>EPSG:4326 · PostGIS</small></div>
          <GeoJsonMap features={features} onSelect={setSelectedEntity}/>
          {!features.length&&<div className="gis-empty-overlay">{layers.loading?<span className="spinner" aria-hidden="true"/>:<div className="gis-empty-icon" aria-hidden="true">⌖</div>}<strong>{layers.loading?'Loading verified spatial layers…':layers.error?'Spatial layers could not be loaded':'No verified spatial records are currently available for this layer.'}</strong><p>{layers.loading?'Waiting for the authenticated GIS API response.':layers.error?layers.error:'No map markers or shapes are shown without trusted geometry.'}</p></div>}
          {layers.error&&<div className="gis-map-error">Layer request failed: {layers.error}</div>}
          {selectedEntity&&<EntityDetail entity={selectedEntity} onClose={()=>setSelectedEntity(null)}/>}
        </Card>
        <Card title={result?.title??'Spatial query results'} detail="Results retain source references and backend status">
          {queryState.loading?<DataState kind="loading" title="Running spatial query…"/>:queryState.error?null:result?<SearchResults result={result}/>:<DataState kind="empty" title="No query submitted" detail="Choose a spatial search and submit real query parameters to retrieve results."/>}
        </Card>
      </section>
    </div>
    <Card title="Trusted entity directory" detail="Selectable entities returned by the authenticated GIS API">
      {entities.loading?<DataState kind="loading"/>:entities.error?<ApiFailure error={entities.error} status={entities.status} retry={()=>setReload(x=>x+1)}/>:<>
        <div className="gis-entity-filters"><input value={entitySearch} onChange={event=>setEntitySearch(event.target.value)} placeholder="Search trusted entities" aria-label="Search trusted entities"/><select value={entityFilter} onChange={event=>setEntityFilter(event.target.value)} aria-label="Filter entity type"><option value="ALL">All entity types</option>{entityKinds.map(type=><option key={type} value={type}>{layerLabel(type)}</option>)}</select><span>{filteredEntities.length} of {entities.data?.total??0} returned</span></div>
        {entities.data?.status==='INSUFFICIENT_VERIFIED_SPATIAL_DATA'||!filteredEntities.length?<DataState kind="insufficient" title="INSUFFICIENT VERIFIED SPATIAL DATA" detail="No verified spatial records are currently available for this layer."/>:<div className="gis-entity-list">{filteredEntities.map(entity=><button type="button" key={entity.id} className={`gis-entity-row ${selectedEntity?.id===entity.id?'selected':''}`} onClick={()=>setSelectedEntity(entity)}><span><strong>{entity.name}</strong><small>{layerLabel(entity.entity_type??'')} · {entity.id}</small></span><StatusBadge status={String(entity.properties?.verification_status??'VERIFIED')}/></button>)}</div>}
      </>}
    </Card>
    <p className="gis-footnote">Nearby and bounding-box searches use coordinates entered by the operator. Candidate proximity is not a confirmed business or geological relationship.</p>
  </main>
}

function NumberField({label,value,setValue,min,max,step}:{label:string;value:string;setValue(value:string):void;min:number;max:number;step:string}){return <label className="gis-field">{label}<input type="number" value={value} onChange={event=>setValue(event.target.value)} min={min} max={max} step={step} required/></label>}
function EntitySelect({label,value,setValue,entities}:{label:string;value:string;setValue(value:string):void;entities:GisFeature[]}){return <label className="gis-field">{label}<select value={value} onChange={event=>setValue(event.target.value)} required><option value="">Select a trusted entity</option>{entities.map(entity=><option key={entity.id} value={entity.id}>{entity.name} · {layerLabel(entity.entity_type??'')}</option>)}</select></label>}
function normalizeFeature(feature:GisFeature):GisFeature{return {...feature,entity_type:feature.entity_type??String(feature.properties?.entity_type??''),name:feature.name??String(feature.properties?.name??'Trusted spatial entity'),provenance:feature.provenance??(feature.properties?.provenance as Record<string,unknown>|undefined)??{},properties:feature.properties??{}}}
function layerLabel(type:string){return ({MINE:'Mines',BOREHOLE:'Boreholes',VERIFIED_COORDINATE:'Verified coordinates'} as Record<string,string>)[type]??type.replace(/_/g,' ')}
function requiredNumber(value:string,label:string,min:number,max:number){if(!value.trim())throw new Error(`${label} is required.`);const number=Number(value);if(!Number.isFinite(number)||number<min||number>max)throw new Error(`${label} must be between ${min} and ${max}.`);return number}
function failure(error:unknown){return {loading:false,error:error instanceof Error?error.message:'The request could not be completed.',status:error instanceof ApiError?error.status:undefined,data:null}}
function ApiFailure({error,status,retry}:{error:string;status?:number;retry?:()=>void}){const kind=status===401?'unauthorized':status===403?'forbidden':'error';const statusText=status?`HTTP ${status} · `:'';return <DataState kind={kind} title={status===401?'Authentication required':status===403?'Access denied':status===404?'Spatial resource not found':status===422?'Invalid spatial query':status===429?'GIS service rate limited':status&&status>=500?'GIS service error':'GIS request failed'} detail={`${statusText}${error}`} onRetry={retry}/>}
function GeoJsonMap({features,onSelect}:{features:GisFeature[];onSelect(feature:GisFeature):void}){if(!features.length)return <div className="gis-map-canvas gis-map-empty" role="img" aria-label="Empty GIS map; no verified spatial features returned"/>;const points=features.flatMap(feature=>coordinates(feature.geometry?.coordinates));if(!points.length)return <div className="gis-map-canvas gis-map-empty" role="img" aria-label="No renderable verified geometries returned"/>;const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);const bounds={minX:Math.min(...xs),maxX:Math.max(...xs),minY:Math.min(...ys),maxY:Math.max(...ys)};const project=(point:number[])=>{const pad=45;const w=Math.max(bounds.maxX-bounds.minX,0.00001),h=Math.max(bounds.maxY-bounds.minY,0.00001);const x=bounds.maxX===bounds.minX?0.5:(point[0]-bounds.minX)/w;const y=bounds.maxY===bounds.minY?0.5:(point[1]-bounds.minY)/h;return [pad+x*(1000-pad*2),600-pad-y*(600-pad*2)] as const};return <svg className="gis-map-canvas" viewBox="0 0 1000 600" role="img" aria-label={`${features.length} verified spatial features`}>{features.map((feature,index)=><g key={feature.id} onClick={()=>onSelect(feature)} className="gis-feature" tabIndex={0} role="button" aria-label={`${feature.name}, ${layerLabel(feature.entity_type??'')}`}><GeometryShape geometry={feature.geometry} project={project} index={index}/></g>)}</svg>}
function GeometryShape({geometry,project,index}:{geometry:GisFeature['geometry'];project:(point:number[])=>readonly[number,number];index:number}){if(!geometry)return null;const color=['#64796c','#a1744c','#637b8c'][index%3];const point=(coords:unknown)=>{if(Array.isArray(coords)&&typeof coords[0]==='number'&&typeof coords[1]==='number'){const [x,y]=project(coords as number[]);return <circle cx={x} cy={y} r="7" fill={color} stroke="white" strokeWidth="2"/>}return null};const line=(coords:unknown,close=false)=>{if(!Array.isArray(coords))return null;const pairs=coords.filter((p):p is number[]=>Array.isArray(p)&&typeof p[0]==='number'&&typeof p[1]==='number');if(!pairs.length)return null;const d=pairs.map((p,i)=>{const [x,y]=project(p);return `${i?'L':'M'}${x} ${y}`}).join(' ')+(close?' Z':'');return <path d={d} fill={close?`${color}33`:'none'} stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke"/>};const c=geometry.coordinates;switch(geometry.type){case'Point':return point(c);case'MultiPoint':return Array.isArray(c)?c.map((p,i)=><g key={i}>{point(p)}</g>):null;case'LineString':return line(c);case'MultiLineString':return Array.isArray(c)?c.map((p,i)=><g key={i}>{line(p)}</g>):null;case'Polygon':return Array.isArray(c)?c.map((p,i)=><g key={i}>{line(p,true)}</g>):null;case'MultiPolygon':return Array.isArray(c)?c.flatMap((poly,i)=>Array.isArray(poly)?poly.map((ring,j)=><g key={`${i}-${j}`}>{line(ring,true)}</g>):[]):null;default:return null}}
function coordinates(value:unknown):number[][]{if(!Array.isArray(value))return[];if(typeof value[0]==='number'&&typeof value[1]==='number')return[[Number(value[0]),Number(value[1])]];return value.flatMap(coordinates)}
function Provenance({value}:{value:unknown}){if(!value||typeof value!=='object'||!Object.values(value as object).some(Boolean))return <span className="gis-muted">No provenance returned</span>;return <details className="gis-provenance"><summary>Evidence / provenance</summary><dl>{Object.entries(value as Record<string,unknown>).filter(([,entry])=>entry!==null&&entry!==undefined).map(([key,entry])=><div key={key}><dt>{key.replace(/_/g,' ')}</dt><dd>{String(entry)}</dd></div>)}</dl></details>}
function EntityDetail({entity,onClose}:{entity:GisFeature;onClose():void}){return <aside className="gis-feature-detail"><button type="button" onClick={onClose} aria-label="Close entity detail">×</button><strong>{entity.name}</strong><span>{layerLabel(entity.entity_type??'')}</span><code>{entity.id}</code><Provenance value={entity.provenance}/></aside>}
function SearchResults({result}:{result:SearchResult}){if(result.kind==='features'){const data=result.data;if(data.status==='INSUFFICIENT_VERIFIED_SPATIAL_DATA'||!data.items.length)return <DataState kind="insufficient" detail="No verified spatial records were found for this query."/>;return <><div className="gis-result-summary"><StatusBadge status={data.status}/><span>{data.total??data.items.length} matching entities</span></div><div className="gis-results-list">{data.items.map(item=><article key={item.id}><div><strong>{item.name}</strong><span>{layerLabel(item.entity_type??'')} · {item.properties?.state?String(item.properties.state):'State not returned'}</span>{item.distance_m!==undefined&&<b>{item.distance_m.toLocaleString(undefined,{maximumFractionDigits:2})} {item.distance_unit??'m'}</b>}</div><Provenance value={item.provenance??item.properties?.provenance}/></article>)}</div></>}
  if(result.kind==='distance'){const data=result.data;if(data.status==='INSUFFICIENT_VERIFIED_SPATIAL_DATA'||!data.left||!data.right)return <DataState kind="insufficient" detail="Distance requires two trusted entities with verified geometry."/>;return <div className="gis-distance-result"><StatusBadge status={data.status}/><strong>{data.distance_m?.toLocaleString(undefined,{maximumFractionDigits:2})} {data.unit}</strong><span>{data.left.name} ↔ {data.right.name}</span><p>Calculated by {data.function}. This is a geometric distance, not evidence of ownership or a geological relationship.</p><Provenance value={{left:data.left.provenance,right:data.right.provenance}}/></div>}
  const data=result.data;if(data.status==='INSUFFICIENT_VERIFIED_SPATIAL_DATA'||!data.items.length)return <><StatusBadge status={data.status}/><DataState kind="insufficient" detail="No verified borehole–mine proximity candidates were returned."/><p className="gis-candidate-warning">Any returned pair is a proximity candidate only; it does not establish identity, ownership, or a mine association.</p></>;return <><div className="gis-candidate-warning">Proximity candidates only — not confirmed identity, ownership, or geological association.</div><div className="gis-results-list">{data.items.map((item,index)=><article key={`${item.borehole.id}-${item.mine.id}-${index}`}><div><strong>{item.borehole.name??'Borehole'} ↔ {item.mine.name??'Mine'}</strong><span>{item.relation_type} · relationship established: {String(item.established_relationship)}</span><b>{item.distance_m.toLocaleString(undefined,{maximumFractionDigits:2})} {item.unit}</b></div><div className="gis-provenance-pair"><Provenance value={item.borehole.provenance}/><Provenance value={item.mine.provenance}/></div></article>)}</div></>}
