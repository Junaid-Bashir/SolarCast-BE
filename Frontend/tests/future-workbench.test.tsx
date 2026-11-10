import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FutureWorkbench } from "../components/future-workbench";
import type { BacktestResults, FutureForecast, Job } from "../lib/api";

vi.mock("../components/future-chart",()=>({FutureChart:({forecast}:{forecast:FutureForecast})=><div>Future chart for {forecast.model_label}</div>}));
const source:BacktestResults={job_id:"source",dataset_id:"dataset",filename:"demo.csv",demo:true,horizon_minutes:60,frequency_minutes:60,train_fraction:.8,evaluation:"rolling_origin_holdout",best_model:"lightgbm",metrics:[{model:"lightgbm",label:"LightGBM",mae:1,rmse:2,bias:0,skill:50},{model:"persistence",label:"Persistence",mae:3,rmse:4,bias:0,skill:0},{model:"seasonal_naive",label:"Seasonal naive",mae:2,rmse:3,bias:0,skill:25}],failures:[],split:{training_rows:200,evaluation_rows:40,warmup_rows:24,purged_origins:1,first_training_origin:"2026-06-02T00:00Z",last_training_target:"2026-06-10T00:00Z",first_evaluation_origin:"2026-06-10T01:00Z",first_evaluation_target:"2026-06-10T02:00Z",last_evaluation_target:"2026-06-11T17:00:00+00:00",timezone:"UTC"},points:[],total_predictions_per_model:40,feature_names:[],notes:[]};
const queued:Job={job_id:"future",dataset_id:"dataset",status:"queued",progress:0,stage:"Queued for future forecasting",error:null};
const output:FutureForecast={forecast_id:"future",source_job_id:"source",dataset_id:"dataset",filename:"demo.csv",demo:true,kind:"future_forecast",model:"lightgbm",model_label:"LightGBM",method:"recursive_one_step",fit_strategy:"refit_all_observed_history",training_rows:240,backtest_horizon_minutes:60,horizon_minutes:1440,frequency_minutes:60,origin:"2026-06-11T17:00:00+00:00",generated_at:"2026-11-10T12:00:00+00:00",timezone:"UTC",context:[{timestamp:"2026-06-11T17:00:00+00:00",generation:8}],points:[{timestamp:"2026-06-11T18:00:00+00:00",predicted_generation:7.5,lead_minutes:60},{timestamp:"2026-06-12T17:00:00+00:00",predicted_generation:6.25,lead_minutes:1440}],notes:["No future actuals are used."]};
const fetchMock=vi.fn();
let state:Job;
let submissionFailure=false;
let pollingFailure=false;
beforeEach(()=>{
  state={...queued,status:"completed",progress:100,stage:"Future forecast ready"};submissionFailure=false;pollingFailure=false;
  fetchMock.mockReset();
  fetchMock.mockImplementation(async(url:string,init?:RequestInit)=>{
    if(url.endsWith("/predict"))return {ok:!submissionFailure,json:async()=>submissionFailure?{detail:"The source comparison expired. Run a new comparison."}:queued};
    if(url.includes("/status/"))return {ok:!pollingFailure,json:async()=>pollingFailure?{detail:"Status temporarily unavailable."}:state};
    if(url==="/api/forecast/future/future")return {ok:true,json:async()=>output};
    if(init?.method==="DELETE")return {ok:true};
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch",fetchMock);
});
describe("future forecasting",()=>{
  it("defaults to the selected winner and disables windows shorter than cadence",()=>{
    render(<FutureWorkbench source={source}/>);
    expect(screen.getByLabelText("Forecast model")).toHaveValue("lightgbm");
    expect(screen.getByLabelText("Future forecast window")).toHaveValue("1440");
    expect(screen.getByRole("option",{name:/15 minutes · unavailable/})).toBeDisabled();
    expect(screen.getByText(/original backtest stays unchanged/)).toBeVisible();
    expect(screen.getByText(/dates are relative to the uploaded history/)).toBeVisible();
  });
  it("submits only source, chosen model and forecast window",async()=>{
    render(<FutureWorkbench source={source}/>);
    fireEvent.change(screen.getByLabelText("Forecast model"),{target:{value:"seasonal_naive"}});
    fireEvent.change(screen.getByLabelText("Future forecast window"),{target:{value:"360"}});
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    await screen.findByText("Future solar generation");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({source_job_id:"source",model:"seasonal_naive",horizon_minutes:360});
  });
  it("renders future results, explicit limitations, and a distinct CSV download",async()=>{
    render(<FutureWorkbench source={source}/>);
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    expect(await screen.findByText("Future solar generation")).toBeVisible();
    expect(screen.getByText("Future chart for LightGBM")).toBeVisible();
    expect(screen.getByText("No future accuracy metrics")).toBeVisible();
    expect(screen.getByText(/not a live forecast for today/)).toBeVisible();
    expect(screen.getByRole("link",{name:"Download future CSV"})).toHaveAttribute("href","/api/forecast/future/future/download");
    expect(screen.getByText("7.5")).toBeVisible();
    expect(screen.queryByText("MAE")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Future forecast method & provenance"));
    expect(screen.getByText("No future actuals are used.")).toBeVisible();
  });
  it("locks settings during generation and allows cancelling",async()=>{
    state={...queued,status:"running",progress:60,stage:"Generating future trajectory"};
    const onActiveChange=vi.fn();
    render(<FutureWorkbench source={source} onActiveChange={onActiveChange}/>);
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    expect(await screen.findByText("Generating future trajectory")).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow","60");
    expect(screen.getByLabelText("Forecast model")).toBeDisabled();
    expect(onActiveChange).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button",{name:"Cancel future forecast"}));
    await waitFor(()=>expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([url,init])=>url==="/api/forecast/jobs/future"&&init.method==="DELETE")).toBe(true);
  });
  it("shows submission errors and leaves generation available",async()=>{
    submissionFailure=true;render(<FutureWorkbench source={source}/>);
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("source comparison expired");
    expect(screen.getByRole("button",{name:"Generate future forecast"})).toBeEnabled();
  });
  it("can retry polling without re-submitting a forecast",async()=>{
    pollingFailure=true;render(<FutureWorkbench source={source}/>);
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    expect(await screen.findByText("Status temporarily unavailable.")).toBeVisible();
    pollingFailure=false;fireEvent.click(screen.getByRole("button",{name:"Retry future status"}));
    expect(await screen.findByText("Future solar generation")).toBeVisible();
    expect(fetchMock.mock.calls.filter(([url])=>url.endsWith("/predict"))).toHaveLength(1);
  });
  it("shows a failed forecast without fabricating chart values",async()=>{
    state={...queued,status:"failed",stage:"Future forecasting stopped",error:"The selected model could not forecast."};
    render(<FutureWorkbench source={source}/>);
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not forecast");
    expect(screen.queryByText("Future solar generation")).not.toBeInTheDocument();
  });
  it("retains generated provenance when controls change for a new forecast",async()=>{
    render(<FutureWorkbench source={source}/>);
    fireEvent.click(screen.getByRole("button",{name:"Generate future forecast"}));
    await screen.findByText("Future solar generation");
    fireEvent.change(screen.getByLabelText("Forecast model"),{target:{value:"persistence"}});
    expect(screen.getByText("Future chart for LightGBM")).toBeVisible();
    expect(screen.getByText(/Persistence holds the last observed/)).toBeVisible();
  });
});
