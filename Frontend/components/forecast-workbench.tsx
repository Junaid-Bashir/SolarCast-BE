"use client";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Download, FlaskConical, Loader2, X } from "lucide-react";
import { api, BacktestResults, ForecastOptions, Job, Mapping, MLModel, ModelName } from "@/lib/api";
import { BacktestChart, MetricChart } from "./backtest-charts";
import { FutureWorkbench } from "./future-workbench";

const horizons: Record<number,string> = {15:"15 minutes",60:"1 hour",360:"6 hours",1440:"24 hours"};
const numeric = (value: number | null, suffix="") => value === null ? "Undefined" : `${value.toLocaleString(undefined,{maximumFractionDigits:3})}${suffix}`;
const timestamp = (value: string) => value.replace("T"," ");
export function ForecastWorkbench({mapping,onActiveChange}:{mapping:Mapping;onActiveChange?:(active:boolean)=>void}) {
  const [config,setConfig] = useState<ForecastOptions|null>(null);
  const [configBusy,setConfigBusy] = useState(true);
  const [fraction,setFraction] = useState(.8);
  const [horizon,setHorizon] = useState(60);
  const [models,setModels] = useState<MLModel[]>(["random_forest","xgboost","lightgbm"]);
  const [job,setJob] = useState<Job|null>(null);
  const [results,setResults] = useState<BacktestResults|null>(null);
  const [selected,setSelected] = useState<ModelName>("persistence");
  const [metric,setMetric] = useState<"mae"|"rmse"|"skill">("rmse");
  const [error,setError] = useState("");
  const [pollError,setPollError] = useState("");
  const [busy,setBusy] = useState(false);
  const [retry,setRetry] = useState(0);
  const [futureActive,setFutureActive] = useState(false);
  const active = job?.status === "queued" || job?.status === "running";
  const mappingKey = JSON.stringify(mapping);

  useEffect(()=>{
    onActiveChange?.(!!active||busy||futureActive);
    return ()=>onActiveChange?.(false);
  },[active,busy,futureActive,onActiveChange]);

  useEffect(()=>{
    let disposed=false;
    setConfigBusy(true); setConfig(null); setError("");
    api<ForecastOptions>("/forecast/options",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...JSON.parse(mappingKey),train_fraction:fraction})})
      .then(next=>{if(disposed)return;setConfig(next);setHorizon(current=>next.horizons.some(h=>h.minutes===current&&h.enabled)?current:next.horizons.find(h=>h.enabled)?.minutes??60);setModels(current=>current.filter(name=>next.models.some(m=>m.name===name&&m.available)));})
      .catch(e=>{if(!disposed)setError(e.message);})
      .finally(()=>{if(!disposed)setConfigBusy(false);});
    return ()=>{disposed=true;};
  },[mappingKey,fraction,retry]);

  const jobId = job?.job_id;
  useEffect(()=>{
    if(!jobId)return;
    let disposed=false;
    let timer:ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const state=await api<Job>(`/forecast/status/${jobId}`);
        if(disposed)return;
        setJob(state);setPollError("");
        if(state.status==="completed") {
          const output=await api<BacktestResults>(`/models/results/${jobId}`);
          if(!disposed){setResults(output);setSelected(output.best_model);}
        } else if(state.status==="queued"||state.status==="running") timer=setTimeout(()=>void poll(),1000);
      } catch(e) {if(!disposed)setPollError(e instanceof Error?e.message:"Unable to check training progress.");}
    }
    void poll();
    return ()=>{disposed=true;clearTimeout(timer);};
  },[jobId,retry]);

  async function start() {
    setBusy(true);setError("");setPollError("");
    try {
      const state=await api<Job>("/forecast/train",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...mapping,train_fraction:fraction,horizon_minutes:horizon,models})});
      setResults(null);setJob(state);
    }catch(e){setError(e instanceof Error?e.message:"Unable to start training.");}
    finally{setBusy(false);}
  }
  async function remove() {
    if(!job)return;
    setBusy(true);setError("");
    try {
      const response=await fetch(`/api/forecast/jobs/${job.job_id}`,{method:"DELETE"});
      if(!response.ok)throw new Error("Unable to remove the comparison. Try again.");
      setJob(null);setResults(null);setPollError("");
    }catch(e){setError(e instanceof Error?e.message:"Unable to remove comparison.");}
    finally{setBusy(false);}
  }
  const selectedHorizon = config?.horizons.find(h=>h.minutes===horizon);
  const best = results?.metrics.find(m=>m.model===results.best_model);
  return <section id="model-workbench" className="forecast-workbench" aria-label="Forecast model training and comparison">
    <div className="section-heading"><div><div className="eyebrow"><FlaskConical size={14}/> CHRONOLOGICAL BACKTESTING</div><h2>Put your models to the test.</h2></div><span className="pill">Historical evaluation · Phase 2</span></div>
    <p className="backtest-intro">Compare real model performance on later observations. Persistence and seasonal naive always set the benchmark.</p>
    {error&&<div className="error-banner" role="alert"><AlertCircle size={18}/><span>{error}</span><button className="text-button" onClick={()=>setRetry(n=>n+1)} disabled={!!active||busy}>Retry configuration</button></div>}
    <div className="training-config">
      <div className="config-main"><label className="field-label">Forecast horizon<select aria-label="Forecast horizon" value={horizon} onChange={e=>setHorizon(Number(e.target.value))} disabled={configBusy||!!active||busy||!config?.eligible}>{(config?.horizons??[15,60,360,1440].map(minutes=>({minutes,enabled:false,reason:null}))).map(h=><option key={h.minutes} value={h.minutes} disabled={!h.enabled}>{horizons[h.minutes]}{!h.enabled?" · unavailable":""}</option>)}</select></label>
      <fieldset disabled={configBusy||!!active||busy||!config?.eligible}><legend>Models to compare</legend><div className="model-choices"><span className="baseline-chip"><CheckCircle2 size={13}/> Persistence</span><span className="baseline-chip"><CheckCircle2 size={13}/> Seasonal naive</span>{config?.models.map(model=><label className="model-choice" key={model.name}><input type="checkbox" checked={models.includes(model.name)} disabled={!model.available} onChange={e=>setModels(current=>e.target.checked?[...current,model.name]:current.filter(m=>m!==model.name))}/>{model.label}{!model.available&&<span>Not installed</span>}</label>)}</div></fieldset>
      <details className="advanced-settings"><summary>Advanced settings</summary><label className="field-label">Chronological training fraction<select aria-label="Chronological training fraction" value={fraction} onChange={e=>setFraction(Number(e.target.value))} disabled={!!active||busy}><option value={.6}>60% train / 40% later holdout</option><option value={.7}>70% train / 30% later holdout</option><option value={.8}>80% train / 20% later holdout</option><option value={.9}>90% train / 10% later holdout</option></select></label><p>Raw-row boundary before warmup and horizon purge. Hyperparameters are fixed; no model tuning uses the holdout.</p></details></div>
      <aside className="evaluation-contract"><div className="eyebrow">WHAT THIS MEASURES</div><h3>A fair comparison.<br/>One shared holdout.</h3><p>Each forecast uses observations available at its issue time. Models stay frozen; earlier holdout actuals become history for later forecasts.</p><p>Assumes production at each timestamp is available immediately. The horizon predicts a single future observation, not total energy over a period.</p>{configBusy?<p role="status"><Loader2 size={13} className="spin"/> Checking supported horizons…</p>:config?.eligible&&selectedHorizon?<div className="sample-counts"><span><strong>{selectedHorizon.training_rows.toLocaleString()}</strong> usable training examples</span><span><strong>{selectedHorizon.evaluation_rows.toLocaleString()}</strong> evaluation targets</span></div>:null}</aside>
    </div>
    {config&&!config.eligible&&<div className="training-blockers" role="alert"><strong>Resolve these issues before training</strong>{config.blockers.length?config.blockers.map((reason,i)=><p key={i}>{reason}</p>):<p>{config.horizons[0].reason}</p>}<p>Return to the data health report above. The original upload has not been changed.</p></div>}
    {config?.eligible&&config.horizons.some(h=>!h.enabled)&&<details className="unavailable-horizons"><summary>Why are some horizons unavailable?</summary>{config.horizons.filter(h=>!h.enabled).map(h=><p key={h.minutes}><strong>{horizons[h.minutes]}:</strong> {h.reason}</p>)}</details>}
    {config?.warnings.length? <div className="training-warnings">{config.warnings.map((warning,i)=><p key={i}><AlertCircle size={13}/>{warning}</p>)}</div>:null}
    <div className="training-actions"><p>Both baselines always run. Uncheck all ML models for a baseline-only comparison.</p><button className="button primary" onClick={()=>void start()} disabled={busy||!!active||futureActive||configBusy||!config?.eligible||!selectedHorizon?.enabled}>{busy?<Loader2 size={16} className="spin"/>:<ArrowRight size={16}/>} {results?"Run a new comparison":"Train & compare"}</button></div>
    {job&&<div className="job-panel"><div className="panel-top"><div><div className="eyebrow">LOCAL TRAINING JOB</div><h3>{job.stage}</h3></div><button className="text-button" disabled={busy} onClick={()=>void remove()}><X size={14}/>{active?"Cancel job":"Remove comparison"}</button></div><div className="job-progress" role="progressbar" aria-label="Training progress" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}><span style={{width:`${job.progress}%`}}/></div><div className="job-stage" role="status" aria-live="polite">{active&&<Loader2 size={14} className="spin"/>}{job.progress}% · {job.status === "queued" ? "Waiting for the local worker" : job.status === "completed" ? "Historical evaluation complete" : job.stage}</div>{job.error&&<p className="job-error" role="alert">{job.error}</p>}{pollError&&<div className="error-banner" role="alert"><span>{pollError}</span><button className="text-button" onClick={()=>setRetry(n=>n+1)}>Retry status</button></div>}</div>}
    {results&&best&&<div className="backtest-results"><div className="report-heading"><div><div className="eyebrow">HISTORICAL HOLDOUT / {horizons[results.horizon_minutes].toUpperCase()} AHEAD</div><h2>Model comparison</h2></div><a className="button secondary" href={`/api/forecast/download/${results.job_id}`}><Download size={16}/> Download backtest CSV</a></div>{results.demo&&<p className="demo-results-note">Synthetic demo dataset · these scores describe synthetic observations, not measured company performance.</p>}
    <div className="stat-grid result-stats"><div className="stat"><div className="eyebrow">BEST HOLDOUT RMSE</div><strong>{best.label}</strong><p>Ranked on this comparison holdout</p></div><div className="stat"><div className="eyebrow">MEAN ABSOLUTE ERROR</div><strong>{numeric(best.mae)}</strong><p>Source generation unit</p></div><div className="stat"><div className="eyebrow">ROOT MEAN SQUARED ERROR</div><strong>{numeric(best.rmse)}</strong><p>Source generation unit</p></div><div className="stat"><div className="eyebrow">SKILL VS PERSISTENCE</div><strong>{numeric(best.skill,"%")}</strong><p>Based on RMSE; higher is better</p></div></div>
    <div className="comparison-grid"><div className="comparison-table"><div className="table-scroll"><table><caption>Every model uses the same {results.total_predictions_per_model.toLocaleString()} evaluation targets. Bias = predicted − actual.</caption><thead><tr><th>Model</th><th>MAE</th><th>RMSE</th><th>Bias</th><th>Skill (%)</th></tr></thead><tbody>{results.metrics.map(m=><tr className={m.model===results.best_model?"winning-model":""} key={m.model}><td>{m.label}{m.model===results.best_model&&<span className="winner-label">Best RMSE</span>}</td><td>{numeric(m.mae)}</td><td>{numeric(m.rmse)}</td><td>{numeric(m.bias)}</td><td>{numeric(m.skill)}</td></tr>)}</tbody></table></div></div><div className="comparison-metric"><div className="panel-top"><h3>At a glance</h3><select aria-label="Comparison metric" value={metric} onChange={e=>setMetric(e.target.value as typeof metric)}><option value="rmse">RMSE</option><option value="mae">MAE</option><option value="skill">Skill (%)</option></select></div>{metric==="skill"&&results.metrics.every(m=>m.skill===null)?<p className="muted">Skill is undefined because persistence has zero error.</p>:<MetricChart results={results} metric={metric}/>}</div></div>
    {results.failures.map(f=><div className="error-banner" role="alert" key={f.model}><AlertCircle size={17}/>{f.message}</div>)}
    <section className="data-chart-panel"><div className="panel-top"><div><div className="eyebrow">BACKTEST · NOT A FUTURE FORECAST</div><h3>Actual vs predicted generation</h3></div><label className="chart-model-label">Display model<select aria-label="Display model" value={selected} onChange={e=>setSelected(e.target.value as ModelName)}>{results.metrics.map(m=><option key={m.model} value={m.model}>{m.label}</option>)}</select></label></div><BacktestChart results={results} model={selected}/><div className="chart-bottom"><span>First {results.points.length.toLocaleString()} of {results.total_predictions_per_model.toLocaleString()} evaluation targets · source units · {results.split.timezone}</span><span>Hover to inspect · Drag handles to zoom · CSV includes every target</span></div></section>
    <details className="methodology"><summary>Evaluation timeline & methodology</summary><dl><dt>Last training target</dt><dd>{timestamp(results.split.last_training_target)}</dd><dt>First evaluation origin</dt><dd>{timestamp(results.split.first_evaluation_origin)}</dd><dt>Evaluation target range</dt><dd>{timestamp(results.split.first_evaluation_target)} → {timestamp(results.split.last_evaluation_target)}</dd><dt>Examples / feature warmup / purged origins</dt><dd>{results.split.training_rows.toLocaleString()} training · {results.split.evaluation_rows.toLocaleString()} evaluation · {results.split.warmup_rows} warmup · {results.split.purged_origins} purged</dd></dl>{results.notes.map((note,i)=><p key={i}>{note}</p>)}<p>Features: {results.feature_names.join(", ")}. Weather and capacity columns are not model inputs in this release.</p><p>Future forecasting is not part of this comparison workflow.</p></details>
    <FutureWorkbench key={results.job_id} source={results} onActiveChange={setFutureActive}/></div>}
  </section>;
}
