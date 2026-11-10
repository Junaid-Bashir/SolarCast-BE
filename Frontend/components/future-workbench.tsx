"use client";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, Download, Loader2, Sun, X } from "lucide-react";
import { api, BacktestResults, FutureForecast, Job, ModelName } from "@/lib/api";
import { FutureChart } from "./future-chart";

const windows=[{minutes:15,label:"15 minutes"},{minutes:60,label:"1 hour"},{minutes:360,label:"6 hours"},{minutes:1440,label:"24 hours"}];
const stamp=(s:string)=>s.replace("T"," ");
export function FutureWorkbench({source,onActiveChange}:{source:BacktestResults;onActiveChange?:(active:boolean)=>void}) {
  const [model,setModel]=useState<ModelName>(source.best_model);
  const [windowMinutes,setWindowMinutes]=useState(1440);
  const [job,setJob]=useState<Job|null>(null);
  const [forecast,setForecast]=useState<FutureForecast|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [pollError,setPollError]=useState("");
  const [retry,setRetry]=useState(0);
  const active=job?.status==="queued"||job?.status==="running";
  const isML=model!=="persistence"&&model!=="seasonal_naive";
  useEffect(()=>{onActiveChange?.(!!active||busy);return()=>onActiveChange?.(false);},[active,busy,onActiveChange]);
  const jobId=job?.job_id;
  useEffect(()=>{
    if(!jobId)return;
    let disposed=false;
    let timer:ReturnType<typeof setTimeout>;
    async function poll(){
      try{
        const state=await api<Job>(`/forecast/status/${jobId}`);
        if(disposed)return;
        setJob(state);setPollError("");
        if(state.status==="completed"){
          const result=await api<FutureForecast>(`/forecast/future/${jobId}`);
          if(!disposed)setForecast(result);
        }else if(state.status==="queued"||state.status==="running")timer=setTimeout(()=>void poll(),1000);
      }catch(e){if(!disposed)setPollError(e instanceof Error?e.message:"Unable to check future forecasting progress.");}
    }
    void poll();
    return()=>{disposed=true;clearTimeout(timer);};
  },[jobId,retry]);
  async function generate(){
    setBusy(true);setError("");setPollError("");
    try{
      const state=await api<Job>("/forecast/predict",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_job_id:source.job_id,model,horizon_minutes:windowMinutes})});
      setForecast(null);setJob(state);
    }catch(e){setError(e instanceof Error?e.message:"Unable to start future forecasting.");}
    finally{setBusy(false);}
  }
  async function remove(){
    if(!job)return;
    setBusy(true);setError("");
    try{
      const response=await fetch(`/api/forecast/jobs/${job.job_id}`,{method:"DELETE"});
      if(!response.ok)throw new Error("Unable to remove the future forecast. Try again.");
      setJob(null);setForecast(null);setPollError("");
    }catch(e){setError(e instanceof Error?e.message:"Unable to remove the forecast.");}
    finally{setBusy(false);}
  }
  return <section className="future-workbench" aria-label="Future solar generation forecasts" id="future-workbench">
    <div className="section-heading"><div><div className="eyebrow"><Sun size={15}/> BEYOND THE OBSERVED HORIZON</div><h2>Forecast what comes next.</h2></div><span className="pill future-pill">Future generation · Phase 3</span></div>
    <p className="future-intro">Generate a new trajectory after your dataset ends. These are unobserved predictions, separate from the historical backtest above.</p>
    <div className="future-config"><div className="future-fields"><label className="field-label">Forecast model<select aria-label="Forecast model" value={model} onChange={e=>setModel(e.target.value as ModelName)} disabled={!!active||busy}>{source.metrics.map(m=><option key={m.model} value={m.model}>{m.label}{m.model===source.best_model?" · best holdout RMSE":""}</option>)}</select></label><label className="field-label">Future forecast window<select aria-label="Future forecast window" value={windowMinutes} onChange={e=>setWindowMinutes(Number(e.target.value))} disabled={!!active||busy}>{windows.map(w=><option key={w.minutes} value={w.minutes} disabled={w.minutes%source.frequency_minutes!==0}>{w.label}{w.minutes%source.frequency_minutes!==0?" · unavailable at this cadence":""}</option>)}</select></label></div><div className="future-approach"><strong>{isML?"Refit, then forecast recursively":"Forecast from observed history"}</strong><p>{isML?"The selected algorithm is refitted as a one-step model on all uploaded history. Each predicted value feeds later forecast steps. The original backtest stays unchanged; its scores do not measure this recursive trajectory.":model==="persistence"?"Persistence holds the last observed generation value throughout the forecast window. No model refit or future actuals are used.":"Seasonal naive repeats the last 24 elapsed hours at the same cadence. No future actuals or DST-adjusted local-clock alignment are assumed."}</p></div></div>
    <div className="future-cutoff"><span>Starts after dataset cutoff</span><strong>{stamp(source.split.last_evaluation_target)}</strong><span>{source.split.timezone} · dates are relative to the uploaded history, not today.</span></div>
    {error&&<div className="error-banner" role="alert"><AlertCircle size={17}/>{error}</div>}
    <div className="training-actions"><p>No future actuals or weather inputs. Forecasts are individual generation observations in your source unit.</p><button className="button future-button" disabled={!!active||busy} onClick={()=>void generate()}>{busy?<Loader2 size={16} className="spin"/>:<ArrowRight size={16}/>}Generate future forecast</button></div>
    {job&&<div className="job-panel future-job"><div className="panel-top"><div><div className="eyebrow">FUTURE FORECAST JOB</div><h3>{job.stage}</h3></div><button className="text-button" disabled={busy} onClick={()=>void remove()}><X size={14}/>{active?"Cancel future forecast":"Remove future forecast"}</button></div><div className="job-progress" role="progressbar" aria-label="Future forecasting progress" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}><span style={{width:`${job.progress}%`}}/></div><div className="job-stage" role="status" aria-live="polite">{active&&<Loader2 size={14} className="spin"/>}{job.progress}% · {job.status==="completed"?"Future forecast ready":job.stage}</div>{job.error&&<p className="job-error" role="alert">{job.error}</p>}{pollError&&<div className="error-banner" role="alert"><span>{pollError}</span><button className="text-button" onClick={()=>setRetry(n=>n+1)}>Retry future status</button></div>}</div>}
    {forecast&&<div className="future-results"><div className="report-heading"><div><div className="eyebrow">UNOBSERVED TARGETS / {forecast.model_label.toUpperCase()}</div><h2>Future solar generation</h2></div><a className="button secondary" href={`/api/forecast/future/${forecast.forecast_id}/download`}><Download size={16}/>Download future CSV</a></div>{forecast.demo&&<p className="demo-results-note">Synthetic demo dataset · future is relative to the sample’s final timestamp, not a live forecast for today.</p>}
    <div className="stat-grid future-stats"><div className="stat"><div className="eyebrow">SELECTED MODEL</div><strong>{forecast.model_label}</strong><p>{forecast.method==="recursive_one_step"?"One-step refit · recursive forecast":"Observed-history baseline"}</p></div><div className="stat"><div className="eyebrow">FORECAST WINDOW</div><strong>{windows.find(w=>w.minutes===forecast.horizon_minutes)?.label}</strong><p>{forecast.frequency_minutes}-minute sampling interval</p></div><div className="stat"><div className="eyebrow">PREDICTED POINTS</div><strong>{forecast.points.length.toLocaleString()}</strong><p>No future actuals available</p></div><div className="stat"><div className="eyebrow">FINAL FORECAST DATE</div><strong>{forecast.points.at(-1)?.timestamp.slice(0,10)}</strong><p>{forecast.points.at(-1)?.timestamp.slice(11,16)} · {forecast.timezone}</p></div></div>
    <div className="future-chart-panel"><div className="panel-top"><div><div className="eyebrow">OBSERVED HISTORY → FUTURE FORECAST</div><h3>Beyond the data cutoff</h3></div><span className="pill future-pill">No future accuracy metrics</span></div><FutureChart forecast={forecast}/><div className="chart-bottom"><span>Solid: observed history · Dashed cyan: future forecast · source units · {forecast.timezone}</span><span>Hover to inspect · Drag handles to zoom</span></div></div>
    <div className="future-disclosure"><AlertCircle size={17}/><p>{forecast.method==="recursive_one_step"?"Recursive errors can accumulate. Historical comparison scores do not validate this refitted future trajectory.":"This baseline extrapolates observed values; historical comparison scores are not measured accuracy for these future targets."} No uncertainty band or future accuracy is claimed.</p></div>
    <div className="future-table table-scroll"><table><caption>First {Math.min(12,forecast.points.length)} future predictions · CSV includes all {forecast.points.length} points</caption><thead><tr><th>Forecast timestamp</th><th>Lead from cutoff</th><th>Predicted generation</th></tr></thead><tbody>{forecast.points.slice(0,12).map(p=><tr key={p.timestamp}><td>{stamp(p.timestamp)}</td><td>{p.lead_minutes} min</td><td>{p.predicted_generation.toLocaleString(undefined,{maximumFractionDigits:3})}</td></tr>)}</tbody></table></div>
    <details className="methodology future-methodology"><summary>Future forecast method & provenance</summary><dl><dt>Dataset cutoff / forecast origin</dt><dd>{stamp(forecast.origin)}</dd><dt>Generated at (UTC)</dt><dd>{stamp(forecast.generated_at)}</dd><dt>Refit examples</dt><dd>{forecast.training_rows?`${forecast.training_rows.toLocaleString()} known one-step targets from all uploaded history`:"Not applicable to this baseline"}</dd><dt>Original backtest lead</dt><dd>{forecast.backtest_horizon_minutes} minutes · comparison scores unchanged</dd></dl>{forecast.notes.map((note,i)=><p key={i}>{note}</p>)}</details>
    </div>}
  </section>;
}
