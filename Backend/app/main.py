import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .schemas import DatasetInspection, MappingRequest, ValidationReport
from .services import MAX_BYTES, inspect_csv, store, validate_dataset
from .forecast_schemas import BacktestResults, BacktestSettings, ForecastOptions, FutureForecast, FutureRequest, JobStatus, TrainRequest
from .future import future_csv
from .forecasting import options
from .jobs import jobs, prediction_csv


@asynccontextmanager
async def lifespan(app):
    yield
    await run_in_threadpool(jobs.close)


app = FastAPI(title="SolarCast Platform", version="0.3.0", description="Local solar dataset inspection, chronological comparison and future forecasting.", lifespan=lifespan)
SAMPLE = Path(__file__).resolve().parents[2] / "Shared" / "sample_data" / "demo_solar.csv"


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    logging.getLogger("solarcast").exception("Unhandled API failure")
    return JSONResponse(status_code=500, content={"detail": "The local service could not complete this request. Please try again."})


@app.get("/api/health")
def health():
    return {"status": "ok", "phase": 3}


@app.post("/api/datasets/upload", response_model=DatasetInspection)
async def upload(file: UploadFile = File(...)):
    try:
        content = await file.read(MAX_BYTES + 1)
        return inspect_csv(content, file.filename or "upload.csv")
    finally:
        await file.close()


@app.post("/api/datasets/sample", response_model=DatasetInspection)
def sample():
    return inspect_csv(SAMPLE.read_bytes(), "demo_solar.csv", demo=True)


@app.get("/api/datasets/sample/download")
def sample_download():
    return FileResponse(SAMPLE, media_type="text/csv", filename="demo_solar.csv")


@app.get("/api/datasets/{dataset_id}", response_model=DatasetInspection)
def inspection(dataset_id: str):
    data = store.get(dataset_id)
    return DatasetInspection(dataset_id=dataset_id, filename=data.filename, demo=data.demo,
        columns=list(data.frame.columns), row_count=len(data.frame), preview=data.frame.head(6).to_dict(orient="records"))


@app.delete("/api/datasets/{dataset_id}", status_code=204)
def remove(dataset_id: str):
    store.delete(dataset_id)
    jobs.remove_dataset(dataset_id)


@app.post("/api/datasets/validate", response_model=ValidationReport)
def validate(request: MappingRequest):
    return validate_dataset(request)


@app.post("/api/forecast/options", response_model=ForecastOptions)
def forecast_options(request: BacktestSettings):
    return options(request)


@app.post("/api/forecast/train", response_model=JobStatus, status_code=202)
def train(request: TrainRequest):
    return jobs.submit(request)


@app.get("/api/forecast/status/{job_id}", response_model=JobStatus)
def status(job_id: str):
    return jobs.get(job_id)


@app.get("/api/models/results/{job_id}", response_model=BacktestResults)
def results(job_id: str):
    return jobs.output(job_id).results


@app.get("/api/forecast/download/{job_id}")
def download(job_id: str):
    return StreamingResponse(prediction_csv(jobs.output(job_id)), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="solarcast-backtest-{job_id}.csv"'})


@app.delete("/api/forecast/jobs/{job_id}", status_code=204)
def remove_job(job_id: str):
    jobs.remove(job_id)


@app.post("/api/forecast/predict", response_model=JobStatus, status_code=202)
def predict(request: FutureRequest):
    return jobs.submit_future(request)


@app.get("/api/forecast/future/{job_id}", response_model=FutureForecast)
def future_results(job_id: str):
    return jobs.future(job_id)


@app.get("/api/forecast/future/{job_id}/download")
def future_download(job_id: str):
    return StreamingResponse(future_csv(jobs.future(job_id)), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="solarcast-future-{job_id}.csv"'})
