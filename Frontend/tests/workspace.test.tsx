import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Workspace } from "../components/workspace";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("../components/generation-chart", () => ({ GenerationChart: () => <div>Historical chart</div> }));
const inspection = {dataset_id:"1",filename:"custom.csv",demo:false,columns:["When","PV"],row_count:2,preview:[{When:"2026-01-01T10:00",PV:"3"}]};
const fetchMock = vi.fn();
beforeEach(()=>{ vi.stubGlobal("fetch",fetchMock); fetchMock.mockReset(); });
function respond(data:unknown, ok=true) { fetchMock.mockResolvedValueOnce({ok,json:async()=>data}); }
describe("dataset workflow",()=>{
  it("uploads, explicitly maps columns, and renders real report values", async()=>{
    respond(inspection);
    render(<Workspace/>);
    fireEvent.change(screen.getByLabelText("Upload CSV file"),{target:{files:[new File(["When,PV\na,3"],"custom.csv",{type:"text/csv"})]}});
    expect(await screen.findByText("Map your columns.")).toBeVisible();
    const button=screen.getByRole("button",{name:/Validate dataset/});
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Timestamp column/),{target:{value:"When"}});
    fireEvent.change(screen.getByLabelText(/Solar generation column/),{target:{value:"PV"}});
    expect(button).toBeEnabled();
    respond({...inspection,status:"attention",observations:2,start:"2026-01-01T10:00",end:"2026-01-01T10:15",frequency_minutes:15,timezone:null,missing_generation:0,checks:[{code:"timezone",label:"Timezone information",status:"warning",detail:"Timezone not specified"}],points:[],chart_total_points:0,chart_note:"No chart points"});
    respond({eligible:false,blockers:["Insufficient history for backtesting."],warnings:[],frequency_minutes:15,horizons:[{minutes:60,enabled:false,reason:"Insufficient history."}],models:[]});
    fireEvent.click(button);
    expect(await screen.findByText("Dataset overview")).toBeVisible();
    expect(screen.getByText("15 min")).toBeVisible();
    expect(screen.getByText("Timezone not specified")).toBeVisible();
    expect(await screen.findByText("Insufficient history for backtesting.")).toBeVisible();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({timestamp_column:"When",generation_column:"PV"});
  });
  it("shows friendly API errors", async()=>{
    respond({detail:"Unable to read CSV. The file is empty."},false);
    render(<Workspace/>); fireEvent.click(screen.getByRole("button",{name:/Load demo/}));
    expect(await screen.findByRole("alert")).toHaveTextContent("The file is empty");
  });
  it("rejects non-CSV files before sending", async()=>{
    render(<Workspace/>);
    fireEvent.change(screen.getByLabelText("Upload CSV file"),{target:{files:[new File(["test"],"data.txt")]}});
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a .csv file");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("loads a clearly labeled demo and can remove it", async()=>{
    respond({...inspection,demo:true}); render(<Workspace/>);
    fireEvent.click(screen.getByRole("button",{name:/Load demo/}));
    expect(await screen.findByText("Synthetic demo dataset")).toBeVisible();
    respond({}); fireEvent.click(screen.getByRole("button",{name:/Remove & start over/}));
    await waitFor(()=>expect(screen.getByRole("button",{name:/Choose CSV file/})).toBeVisible());
    expect(fetchMock.mock.calls[1][1].method).toBe("DELETE");
  });
});
