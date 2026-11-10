import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ForecastWorkbench } from "../components/forecast-workbench";
import type { BacktestResults, ForecastOptions, Job, Mapping } from "../lib/api";

vi.mock("../components/backtest-charts",()=>({BacktestChart:({model}:{model:string})=><div>Backtest chart: {model}</div>,MetricChart:()=> <div>Comparison chart</div>}));
const mapping:Mapping = {dataset_id:"dataset",timestamp_column:"When",generation_column:"PV",capacity_column:null};
const options:ForecastOptions = {eligible:true,blockers:[],warnings:[],frequency_minutes:60,horizons:[{minutes:15,enabled:false,reason:"Not an integer number of intervals.",training_rows:0,evaluation_rows:0},{minutes:60,enabled:true,reason:null,training_rows:200,evaluation_rows:40},{minutes:360,enabled:true,reason:null,training_rows:195,evaluation_rows:35},{minutes:1440,enabled:true,reason:null,training_rows:177,evaluation_rows:24}],models:[{name:"random_forest",label:"Random Forest",available:true},{name:"xgboost",label:"XGBoost",available:true},{name:"lightgbm",label:"LightGBM",available:true}]};
const job:Job = {job_id:"job",dataset_id:"dataset",status:"queued",progress:0,stage:"Queued",error:null};
const results:BacktestResults={job_id:"job",dataset_id:"dataset",filename:"demo.csv",demo:true,horizon_minutes:60,frequency_minutes:60,train_fraction:.8,evaluation:"rolling_origin_holdout",best_model:"seasonal_naive",metrics:[{model:"seasonal_naive",label:"Seasonal naive",mae:1,rmse:2,bias:-.5,skill:50},{model:"persistence",label:"Persistence",mae:3,rmse:4,bias:1,skill:0}],failures:[],split:{training_rows:200,evaluation_rows:40,warmup_rows:24,purged_origins:1,first_training_origin:"2026-06-02T00:00",last_training_target:"2026-06-10T00:00",first_evaluation_origin:"2026-06-10T01:00",first_evaluation_target:"2026-06-10T02:00",last_evaluation_target:"2026-06-11T17:00",timezone:"UTC"},points:[{timestamp:"2026-06-10T02:00",origin:"2026-06-10T01:00",actual_generation:3,predictions:{persistence:5,seasonal_naive:2}}],total_predictions_per_model:40,feature_names:["observed_at_origin"],notes:["Historical evaluation only."]};
const fetchMock=vi.fn();
let state:Job;
let config:ForecastOptions;
let startFailure=false;
let pollFailure=false;
beforeEach(()=>{
  state={...job,status:"completed",progress:100,stage:"Comparison complete"};config=structuredClone(options);startFailure=false;pollFailure=false;
  fetchMock.mockReset();
  fetchMock.mockImplementation(async(url:string,init?:RequestInit)=>{
    if(url.endsWith("/options"))return {ok:true,json:async()=>config};
    if(url.endsWith("/train"))return {ok:!startFailure,json:async()=>startFailure?{detail:"The local training queue is full."}:job};
    if(url.includes("/status/"))return {ok:!pollFailure,json:async()=>pollFailure?{detail:"Service temporarily unavailable."}:state};
    if(url.includes("/results/"))return {ok:true,json:async()=>results};
    if(init?.method==="DELETE")return {ok:true};
    throw new Error(`Unexpected test request: ${url}`);
  });
  vi.stubGlobal("fetch",fetchMock);
});

async function ready(){render(<ForecastWorkbench mapping={mapping}/>);await waitFor(()=>expect(screen.getByRole("button",{name:/Train & compare/})).toBeEnabled());}
describe("forecast comparison workflow",()=>{
  it("enables supported horizons and always includes both baselines",async()=>{
    await ready();
    expect(screen.getByRole("option",{name:"15 minutes · unavailable"})).toBeDisabled();
    expect(screen.getByText("Persistence")).toBeVisible();
    expect(screen.getByText("Seasonal naive")).toBeVisible();
    for(const name of ["Random Forest","XGBoost","LightGBM"])fireEvent.click(screen.getByRole("checkbox",{name}));
    fireEvent.click(screen.getByRole("button",{name:/Train & compare/}));
    expect(await screen.findByText("Model comparison")).toBeVisible();
    const trainingCall=fetchMock.mock.calls.find(([url])=>url.endsWith("/train"));
    expect(trainingCall).toBeDefined();
    const payload=JSON.parse(trainingCall![1].body);
    expect(payload).toMatchObject({...mapping,models:[],horizon_minutes:60,train_fraction:.8});
  });
  it("renders measured results, exports, and selected-model chart",async()=>{
    await ready();fireEvent.click(screen.getByRole("button",{name:/Train & compare/}));
    expect(await screen.findByText("Model comparison")).toBeVisible();
    expect(screen.getByText("50%")).toBeVisible();
    expect(screen.getByText(/these scores describe synthetic observations/)).toBeVisible();
    expect(screen.getByRole("link",{name:/Download backtest CSV/})).toHaveAttribute("href","/api/forecast/download/job");
    expect(screen.getByText("Backtest chart: seasonal_naive")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Display model"),{target:{value:"persistence"}});
    expect(screen.getByText("Backtest chart: persistence")).toBeVisible();
    fireEvent.click(screen.getByText("Evaluation timeline & methodology"));
    expect(screen.getByText("Historical evaluation only.")).toBeVisible();
  });
  it("blocks invalid datasets and explains why",async()=>{
    config={...options,eligible:false,blockers:["Missing intervals must be resolved."],horizons:options.horizons.map(h=>({...h,enabled:false}))};
    render(<ForecastWorkbench mapping={mapping}/>);
    expect(await screen.findByText("Missing intervals must be resolved.")).toBeVisible();
    expect(screen.getByRole("button",{name:/Train & compare/})).toBeDisabled();
  });
  it("shows job progress, locks settings, and supports cancellation",async()=>{
    state={...job,status:"running",progress:45,stage:"Training Random Forest"};
    await ready();fireEvent.click(screen.getByRole("button",{name:/Train & compare/}));
    expect(await screen.findByText("Training Random Forest")).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow","45");
    expect(screen.getByLabelText("Forecast horizon")).toBeDisabled();
    fireEvent.click(screen.getByRole("button",{name:/Cancel job/}));
    await waitFor(()=>expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([url,init])=>url==="/api/forecast/jobs/job"&&init.method==="DELETE")).toBe(true);
  });
  it("shows a friendly submission failure",async()=>{
    startFailure=true;await ready();fireEvent.click(screen.getByRole("button",{name:/Train & compare/}));
    expect(await screen.findByRole("alert")).toHaveTextContent("training queue is full");
  });
  it("can retry interrupted status polling",async()=>{
    pollFailure=true;await ready();fireEvent.click(screen.getByRole("button",{name:/Train & compare/}));
    expect(await screen.findByText("Service temporarily unavailable.")).toBeVisible();
    pollFailure=false;fireEvent.click(screen.getByRole("button",{name:"Retry status"}));
    expect(await screen.findByText("Model comparison")).toBeVisible();
  });
  it("shows server training failures without fabricated results",async()=>{
    state={...job,status:"failed",stage:"Training stopped",error:"Training could not complete."};
    await ready();fireEvent.click(screen.getByRole("button",{name:/Train & compare/}));
    expect(await screen.findByRole("alert")).toHaveTextContent("Training could not complete.");
    expect(screen.queryByText("Model comparison")).not.toBeInTheDocument();
  });
  it("refreshes eligibility when the chronological split changes",async()=>{
    await ready();fireEvent.click(screen.getByText("Advanced settings"));
    fireEvent.change(screen.getByLabelText("Chronological training fraction"),{target:{value:"0.7"}});
    await waitFor(()=>expect(fetchMock.mock.calls.filter(([url])=>url.endsWith("/options"))).toHaveLength(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).train_fraction).toBe(.7);
  });
});
