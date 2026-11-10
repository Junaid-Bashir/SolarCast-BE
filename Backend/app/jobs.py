"""Single-worker, bounded local job queue; replace this boundary for deployment."""
import csv
import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import HTTPException

from .forecast_schemas import FutureForecast, FutureRequest, JobStatus, TrainRequest
from .forecasting import RunOutput, prepare, run_backtest
from .future import check_request, generate_future
from .services import TTL_SECONDS, store


@dataclass
class Job:
    state: JobStatus
    created: float = field(default_factory=time.monotonic)
    output: RunOutput | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    future_output: FutureForecast | None = None
    source_job_id: str | None = None


class JobCancelled(Exception):
    pass


class JobManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, Job] = {}
        self.slots = threading.BoundedSemaphore(4)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solarcast-training")

    def _purge(self):
        expired = {key for key, job in self.items.items() if time.monotonic() - job.created > TTL_SECONDS}
        for key, job in list(self.items.items()):
            if key in expired or job.source_job_id in expired:
                job.cancelled.set()
                del self.items[key]

    def _reserve(self, dataset_id, kind="backtest", source_job_id=None):
        """Called while holding the job lock; both job kinds share the same limits."""
        active = [j for j in self.items.values() if j.state.status in ("queued", "running")]
        if any(j.state.dataset_id == dataset_id for j in active):
            raise HTTPException(409, "This dataset already has a queued or running job. Wait for it to finish.")
        if len(active) >= 4 or len(self.items) >= 10:
            raise HTTPException(429, "The local training queue is full. Remove an old job or wait for jobs to finish.")
        if not self.slots.acquire(blocking=False):
            raise HTTPException(429, "The local worker is still releasing cancelled jobs. Try again shortly.")
        key = str(uuid4())
        job = Job(JobStatus(job_id=key, dataset_id=dataset_id, status="queued", progress=0,
            stage="Queued for future forecasting" if kind == "future" else "Queued for local training", kind=kind), source_job_id=source_job_id)
        self.items[key] = job
        return key, job, job.state.model_copy(deep=True)

    def submit(self, request: TrainRequest) -> JobStatus:
        dataset, series = prepare(request)  # Reject invalid input before enqueuing.
        with self.lock:
            # Deletion clears the dataset before acquiring this job lock. Recheck
            # under the lock so a concurrent removal cannot miss a newly queued job.
            store.get(request.dataset_id)
            self._purge()
            key, job, initial = self._reserve(request.dataset_id)
        try:
            self.executor.submit(self._run, key, job, request, dataset, series)
        except RuntimeError:
            with self.lock:
                self.items.pop(key, None)
            self.slots.release()
            raise HTTPException(503, "The local training worker is shutting down. Restart the backend.") from None
        return initial

    def submit_future(self, request: FutureRequest) -> JobStatus:
        source = self.output(request.source_job_id)
        if source.request is None:
            raise HTTPException(409, "This comparison has no retained input mapping. Run a new comparison first.")
        # Check the same unmodified source and all eligibility rules, independent
        # of failed/uninstalled models that are not selected for this forecast.
        dataset, series = prepare(source.request.model_copy(update={"models": []}))
        check_request(request, source, series)
        with self.lock:
            self._purge()
            parent = self.items.get(request.source_job_id)
            if parent is None or parent.output is not source:
                raise HTTPException(404, "The source comparison expired or was removed. Run a new comparison.")
            store.get(source.results.dataset_id)
            key, job, initial = self._reserve(source.results.dataset_id, "future", request.source_job_id)
        try:
            self.executor.submit(self._run_future, key, job, request, source, dataset, series)
        except RuntimeError:
            with self.lock:
                self.items.pop(key, None)
            self.slots.release()
            raise HTTPException(503, "The local worker is shutting down. Restart the backend.") from None
        return initial

    def _run_future(self, key, job, request, source, dataset, series):
        def progress(stage, percentage):
            with self.lock:
                if job.cancelled.is_set() or key not in self.items:
                    raise JobCancelled()
                job.state.status = "running"
                job.state.stage = stage
                job.state.progress = percentage
        try:
            output = generate_future(key, request, source, dataset, series, progress)
            with self.lock:
                if job.cancelled.is_set() or key not in self.items:
                    return
                job.future_output = output
                job.state = JobStatus(job_id=key, dataset_id=source.results.dataset_id, status="completed",
                    progress=100, stage="Future forecast ready", kind="future")
        except JobCancelled:
            return
        except Exception:
            logging.getLogger("solarcast").exception("Future forecast failed for job %s", key)
            with self.lock:
                if key in self.items and not job.cancelled.is_set():
                    job.state.status = "failed"
                    job.state.stage = "Future forecasting stopped"
                    job.state.error = "Future forecasting could not complete. Check the selected model and retry. Details are in the local backend log."
        finally:
            self.slots.release()

    def _run(self, key, job, request, dataset, series):
        def progress(stage, percentage):
            with self.lock:
                if job.cancelled.is_set() or key not in self.items:
                    raise JobCancelled()
                job.state.status = "running"
                job.state.stage = stage
                job.state.progress = percentage
        try:
            progress("Preparing dataset", 5)
            output = run_backtest(key, request, dataset, series, progress)
            with self.lock:
                if job.cancelled.is_set() or key not in self.items:
                    return
                job.output = output
                job.state = JobStatus(job_id=key, dataset_id=request.dataset_id, status="completed", progress=100, stage="Comparison complete")
        except JobCancelled:
            return
        except Exception:
            logging.getLogger("solarcast").exception("Backtest failed for job %s", key)
            with self.lock:
                if key in self.items and not job.cancelled.is_set():
                    job.state.status = "failed"
                    job.state.stage = "Training stopped"
                    job.state.error = "Training could not complete. Check your dataset and try again. Details are available in the local backend log."
        finally:
            self.slots.release()

    def get(self, key: str) -> JobStatus:
        with self.lock:
            self._purge()
            job = self.items.get(key)
            if job is None:
                raise HTTPException(404, "Training job expired or was removed. Start a new comparison.")
            return job.state.model_copy(deep=True)

    def output(self, key: str) -> RunOutput:
        with self.lock:
            self._purge()
            job = self.items.get(key)
            if job is None:
                raise HTTPException(404, "Training job expired or was removed. Start a new comparison.")
            if job.state.status != "completed" or job.output is None:
                raise HTTPException(409, "Results are available after training completes successfully.")
            return job.output

    def remove(self, key: str):
        with self.lock:
            for child_key, job in list(self.items.items()):
                if child_key == key or job.source_job_id == key:
                    job.cancelled.set()
                    del self.items[child_key]

    def future(self, key: str) -> FutureForecast:
        with self.lock:
            self._purge()
            job = self.items.get(key)
            if job is None:
                raise HTTPException(404, "Future forecast expired or was removed. Generate a new forecast.")
            if job.state.kind != "future" or job.state.status != "completed" or job.future_output is None:
                raise HTTPException(409, "Future forecast results are available after a future forecasting job completes.")
            return job.future_output

    def remove_dataset(self, dataset_id: str):
        with self.lock:
            for key, job in list(self.items.items()):
                if job.state.dataset_id == dataset_id:
                    job.cancelled.set()
                    del self.items[key]

    def close(self):
        with self.lock:
            for job in self.items.values():
                job.cancelled.set()
        self.executor.shutdown(wait=True, cancel_futures=True)


jobs = JobManager()


def prediction_csv(output: RunOutput):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "forecast_origin", "predicted_generation", "model", "forecast_horizon", "actual_generation", "forecast_error"])
    yield buffer.getvalue()
    for name, values in output.predictions.items():
        for index, predicted in enumerate(values):
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow([output.times[index], output.origins[index], float(predicted), name,
                             f"{output.results.horizon_minutes} minutes", float(output.actual[index]), float(predicted - output.actual[index])])
            yield buffer.getvalue()
