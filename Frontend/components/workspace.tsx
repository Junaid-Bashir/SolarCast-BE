"use client";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertCircle, ArrowLeft, ArrowRight, CheckCircle2, Download, FileSpreadsheet, Loader2, RefreshCw, ShieldCheck, Upload, XCircle } from "lucide-react";
import { Header } from "./brand";
import { GenerationChart } from "./generation-chart";
import { ForecastWorkbench } from "./forecast-workbench";
import { api, Inspection, Mapping, Report } from "@/lib/api";

const number = (n: number) => n.toLocaleString();
const date = (d: string | null) => d ? d.replace("T", " ").slice(0,16) : "Unavailable";
export function Workspace() {
  const params = useSearchParams();
  const loadedDemo = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const [dataset, setDataset] = useState<Inspection | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [mapping, setMapping] = useState({ timestamp_column: "", generation_column: "", capacity_column: "" });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [trainingActive, setTrainingActive] = useState(false);
  async function load(file?: File) {
    if (busy) return;
    setError(""); setBusy(file ? "Inspecting your CSV…" : "Loading demo dataset…");
    try {
      if (file && (!file.name.toLowerCase().endsWith(".csv") || file.size > 10*1024*1024)) throw new Error("Choose a .csv file of 10 MB or less.");
      const body = new FormData(); if (file) body.append("file", file);
      const next = await api<Inspection>(file ? "/datasets/upload" : "/datasets/sample", {method:"POST", ...(file ? {body} : {})});
      setDataset(next); setReport(null); setMapping({timestamp_column:"", generation_column:"", capacity_column:""});
    } catch(e) { setError(e instanceof Error ? e.message : "Unable to inspect the file."); }
    finally { setBusy(""); if(input.current) input.current.value=""; }
  }
  useEffect(() => { if(params.get("demo") === "1" && !loadedDemo.current) { loadedDemo.current = true; void load(); } }, [params]); // The ref prevents duplicate sample uploads in React Strict Mode.
  async function validate() {
    if (!dataset) return;
    setBusy("Checking dataset health…"); setError("");
    try {
      const payload: Mapping = {dataset_id:dataset.dataset_id, ...mapping, capacity_column:mapping.capacity_column || null};
      setReport(await api<Report>("/datasets/validate", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)}));
    } catch(e) { setError(e instanceof Error ? e.message : "Unable to validate the data."); }
    finally { setBusy(""); }
  }
  async function reset() {
    if (!dataset) return;
    setBusy("Removing dataset…"); setError("");
    try { const response=await fetch(`/api/datasets/${dataset.dataset_id}`, {method:"DELETE"}); if(!response.ok) throw new Error("Unable to remove dataset. Try again."); setDataset(null); setReport(null); }
    catch(e) { setError(e instanceof Error ? e.message : "Unable to reset."); }
    finally { setBusy(""); }
  }
  return <><Header/><main className="workspace shell"><div className="workspace-heading"><div><div className="eyebrow">DATA FOUNDATION / WORKSPACE</div><h1>Good forecasts start<br/>with good data<span>.</span></h1><p>Inspect your historical production. Understand what’s ready, and what needs attention.</p></div><span className="phase-label">PHASE 03 <span>Data, models & forecasts</span></span></div>
    <ol className="progress-steps">{["Upload CSV", "Map columns", "Review dataset"].map((label,i)=><li key={label} className={(report ? 2 : dataset ? 1 : 0) >= i ? "current" : ""}><span>{(report ? 2 : dataset ? 1 : 0) > i ? <CheckCircle2 size={16}/> : `0${i+1}`}</span>{label}</li>)}</ol>
    {error && <div className="error-banner" role="alert"><AlertCircle size={19}/><span>{error}</span></div>}
    {busy && <div className="busy-banner" role="status" aria-live="polite"><Loader2 className="spin" size={18}/>{busy}</div>}
    {!dataset ? <div className="upload-grid"><section className={`upload-panel ${dragging ? "dragging" : ""}`} onDragOver={e=>{e.preventDefault();setDragging(true);}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();setDragging(false); const file=e.dataTransfer.files[0];if(file)void load(file);}}><div className="upload-symbol"><Upload size={28} strokeWidth={1.5}/></div><h2>Your production data.<br/>A fresh perspective.</h2><p>Drag a CSV here, or choose a file to get started.</p><input ref={input} id="csv-upload" type="file" accept=".csv,text/csv" aria-label="Upload CSV file" className="sr-only" disabled={!!busy} onChange={e=>{const file=e.target.files?.[0];if(file)void load(file);}}/><button className="button primary" disabled={!!busy} onClick={()=>input.current?.click()}>Choose CSV file <ArrowRight size={17}/></button><span className="upload-limit">UTF-8 CSV · Up to 10 MB · 100,000 rows</span></section><aside className="upload-aside"><div className="eyebrow">WHAT YOU’LL NEED</div><h3>Two columns.<br/>That’s the starting point.</h3><div className="requirement"><span>01</span><div><strong>Timestamp</strong><p>ISO 8601 dates and times, ideally with a UTC offset.</p></div></div><div className="requirement"><span>02</span><div><strong>Solar generation</strong><p>Numeric production values in a consistent unit.</p></div></div><p className="aside-note">Your column names can be anything. You’ll map them in the next step.</p><div className="demo-card"><FileSpreadsheet size={22}/><div><strong>Just exploring?</strong><p>Try a synthetic solar dataset.</p></div><button className="text-button" disabled={!!busy} onClick={()=>void load()}>Load demo <ArrowRight size={15}/></button></div><a className="download-link" href="/api/datasets/sample/download"><Download size={14}/> Download sample CSV</a></aside></div> : <>
      <div className="dataset-bar"><FileSpreadsheet size={21}/><strong>{dataset.filename}</strong>{dataset.demo && <span className="pill">Synthetic demo dataset</span>}<span className="muted">{number(dataset.row_count)} rows · {dataset.columns.length} columns</span><button className="text-button" onClick={()=>void reset()} disabled={!!busy}><RefreshCw size={14}/> Remove & start over</button></div>
      {!report ? <section className="mapping-panel"><div className="section-heading"><div><div className="eyebrow">MAKE THE CONNECTION</div><h2>Map your columns.</h2></div><p>Choose the source for each field.<br/>We won’t assume your column names.</p></div><div className="mapping-fields">{[{key:"timestamp_column",label:"Timestamp column",optional:false},{key:"generation_column",label:"Solar generation column",optional:false},{key:"capacity_column",label:"Installed capacity",optional:true}].map(field=><label key={field.key}>{field.label} <span>{field.optional ? "Optional" : "Required"}</span><select value={mapping[field.key as keyof typeof mapping]} onChange={e=>setMapping({...mapping,[field.key]:e.target.value})} disabled={!!busy}><option value="">{field.optional ? "Not provided" : "Select a column"}</option>{dataset.columns.map(col=><option key={col} value={col}>{col}</option>)}</select></label>)}</div><SourceTable data={dataset}/><div className="mapping-footer"><p>Preview shows original values. No data has been changed.</p><button className="button primary" onClick={()=>void validate()} disabled={!!busy || !mapping.timestamp_column || !mapping.generation_column}>Validate dataset <ArrowRight size={17}/></button></div></section> : <>
      <div className="report-heading"><div><div className="eyebrow">YOUR DATA, IN FOCUS</div><h2>Dataset overview</h2></div><button className="text-button" onClick={()=>setReport(null)} disabled={!!busy || trainingActive}><ArrowLeft size={15}/> Edit column mapping</button></div>
      <div className="stat-grid"><Stat label="OBSERVATIONS" value={number(report.observations)} note="Original source rows"/><Stat label="SAMPLING INTERVAL" value={report.frequency_minutes ? `${report.frequency_minutes} min` : "Irregular"} note="Inferred from timestamps"/><Stat label="MISSING GENERATION" value={number(report.missing_generation)} note="Blank production values"/><Stat label="DATE RANGE" value={report.start?.slice(0,10) || "Unavailable"} note={`to ${report.end?.slice(0,10) || "unavailable"}`}/></div>
      <section className="data-chart-panel"><div className="panel-top"><div><div className="eyebrow">HISTORICAL OBSERVATIONS</div><h3>Solar generation</h3></div><span className="legend"><i/> Actual production · source units</span></div>{report.points.length ? <GenerationChart points={report.points} frequencyMinutes={report.frequency_minutes}/> : <p className="empty-chart">No consistently parseable timestamps to chart. Review dataset health below.</p>}<div className="chart-bottom"><span>{report.chart_note}</span><span>Drag handles to zoom · Hover to inspect</span></div></section>
      <div className="health-grid"><section className="health-panel"><div className="panel-top"><h3>Dataset health</h3><span className={`health-status ${report.status}`}>{report.status === "healthy" ? "Checks passed" : report.status === "invalid" ? "Action required" : "Review warnings"}</span></div><div className="checks">{report.checks.map(check=><div className={`check-row ${check.status}`} key={check.code}>{check.status === "pass" ? <CheckCircle2 size={17}/> : check.status === "error" ? <XCircle size={17}/> : <AlertCircle size={17}/>}<div><strong>{check.label}</strong><p>{check.detail}</p></div></div>)}</div></section><aside className="next-panel"><span className="eyebrow">A TRANSPARENT START</span><h3>Understand first.<br/>Forecast next.</h3><p>This report describes your original dataset. No rows were sorted, filled, removed, or corrected.</p><dl><dt>First observation</dt><dd>{date(report.start)}</dd><dt>Last observation</dt><dd>{date(report.end)}</dd><dt>Timezone</dt><dd>{report.timezone || "Not specified"}</dd></dl><div className="phase-notice"><strong>Next · Train & compare</strong><p>Use the workbench below to evaluate baselines and machine-learning models on a chronological holdout.</p><a className="text-button" href="#model-workbench">Open model workbench <ArrowRight size={14}/></a></div></aside></div><ForecastWorkbench key={JSON.stringify(mapping)} onActiveChange={setTrainingActive} mapping={{dataset_id:dataset.dataset_id,...mapping,capacity_column:mapping.capacity_column||null}}/><section className="source-panel"><h3>Source preview</h3><SourceTable data={dataset}/></section>
      </>}
    </>}
    <div className="privacy-note"><ShieldCheck size={19}/><p>During local development, uploaded files are processed locally and are not intentionally sent to third-party services. Datasets are held in backend memory for up to one hour and cleared when it restarts. Use “Remove & start over” to remove the dataset and its jobs. A running model fit releases its working data when the current training stage finishes.</p></div>
    </main><footer className="footer shell"><span>solarcast <span className="muted">/ Data before decisions.</span></span><span>Local development · Phase 3</span></footer></>;
}
function Stat({label,value,note}:{label:string;value:string;note:string}) {return <div className="stat"><div className="eyebrow">{label}</div><strong>{value}</strong><p>{note}</p></div>;}
function SourceTable({data}:{data:Inspection}) {return <div className="table-scroll"><table><caption>First {data.preview.length} rows · original CSV values</caption><thead><tr>{data.columns.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{data.preview.map((row,i)=><tr key={i}>{data.columns.map(c=><td key={c}>{row[c] === "" ? <span className="muted">Empty</span> : row[c]}</td>)}</tr>)}</tbody></table></div>;}
