import csv
import io
import threading
import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.forecast_schemas import FutureRequest, TrainRequest
from app.forecasting import make_features, prepare, run_backtest
from app.future import check_request, future_csv, generate_future
from app.jobs import jobs
from app.main import app
from app.services import TTL_SECONDS, inspect_csv, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_state():
    store.items.clear()
    yield
    for key in list(jobs.items):
        jobs.remove(key)
    store.items.clear()


def source_data(model=None, frequency=15, naive=False, zero=False):
    n = int(12*1440/frequency)
    clock = pd.date_range("2026-06-01", periods=n, freq=f"{frequency}min", tz=None if naive else "UTC")
    values = np.zeros(n) if zero else np.arange(n, dtype=float)+1
    data = pd.DataFrame({"clock":[t.isoformat() for t in clock],"pv":values})
    upload = inspect_csv(data.to_csv(index=False).encode(),"source.csv")
    req = TrainRequest(dataset_id=upload.dataset_id,timestamp_column="clock",generation_column="pv",models=[model] if model else [])
    dataset, series = prepare(req)
    output = run_backtest("source",req,dataset,series,lambda *_:None)
    return req, dataset, series, output


def future_request(model="persistence", horizon=1440, source="source"):
    return FutureRequest(source_job_id=source,model=model,horizon_minutes=horizon)


def wait_job(key):
    deadline = time.monotonic()+30
    while time.monotonic()<deadline:
        state = jobs.get(key)
        if state.status in ("completed","failed"):
            return state
        time.sleep(.02)
    pytest.fail("Job timed out")


@pytest.mark.parametrize("horizon",[15,60,360,1440])
def test_future_points_begin_after_cutoff_at_exact_cadence(horizon):
    req, data, series, source = source_data()
    output = generate_future("future",future_request(horizon=horizon),source,data,series,lambda *_:None)
    assert output.kind == "future_forecast"
    assert len(output.points) == horizon/15
    stamps = pd.DatetimeIndex([p.timestamp for p in output.points])
    assert stamps[0] == series.times[-1]+pd.Timedelta(15*60_000_000_000,unit="ns")
    assert stamps[-1] == series.times[-1]+pd.Timedelta(horizon*60_000_000_000,unit="ns")
    assert (stamps>series.times[-1]).all()
    assert output.points[-1].lead_minutes == horizon
    assert output.context[-1].timestamp == output.origin
    assert not any("actual_generation" in p.model_dump() for p in output.points)
    assert pd.Timestamp(output.generated_at).tzinfo is not None


def test_baselines_use_only_last_observation_and_observed_previous_day():
    _, data, series, source = source_data()
    persistent = generate_future("f",future_request(),source,data,series,lambda *_:None)
    assert all(p.predicted_generation == series.values[-1] for p in persistent.points)
    seasonal = generate_future("f",future_request("seasonal_naive"),source,data,series,lambda *_:None)
    np.testing.assert_array_equal([p.predicted_generation for p in seasonal.points],series.values[-series.day:])
    assert seasonal.fit_strategy == "observed_history_baseline" and seasonal.training_rows == 0


@pytest.mark.parametrize("model",["random_forest","xgboost","lightgbm"])
def test_real_selected_model_can_refit_and_forecast(model):
    _, data, series, source = source_data(model=model)
    before = source.results.model_dump_json()
    raw = data.frame.copy(deep=True)
    output = generate_future("f",future_request(model,60),source,data,series,lambda *_:None)
    assert output.method == "recursive_one_step"
    assert output.model == model and len(output.points) == 4
    assert output.training_rows == len(series.values)-series.day-1
    assert all(np.isfinite(p.predicted_generation) and p.predicted_generation>=0 for p in output.points)
    assert source.results.model_dump_json() == before
    pd.testing.assert_frame_equal(data.frame,raw)


def test_refit_uses_only_known_labels_and_recursion_feeds_predictions(monkeypatch):
    _, data, series, source = source_data()
    # The model must have succeeded in the source comparison; use a controlled
    # estimator here to prove inference mechanics independently of tree behavior.
    source.results.metrics.append(source.results.metrics[0].model_copy(update={"model":"random_forest","label":"Random Forest"}))
    expected_x, expected_y, _ = make_features(replace(series,horizon=1))
    calls=[]
    class Spy:
        def fit(self,x,y):
            pd.testing.assert_frame_equal(x,expected_x)
            np.testing.assert_array_equal(y,expected_y)
            assert y[-1] == series.values[-1]
        def set_params(self,**kwargs): return self
        def predict(self,x):
            calls.append(x.copy())
            return np.array([x.iloc[0]["observed_at_origin"]+1])
    monkeypatch.setattr("app.future.build_estimator",lambda _:Spy())
    original = series.values.copy()
    output = generate_future("f",future_request("random_forest",60),source,data,series,lambda *_:None)
    last = series.values[-1]
    assert [p.predicted_generation for p in output.points] == [last+1,last+2,last+3,last+4]
    assert calls[0].iloc[0]["observed_at_origin"] == last
    assert calls[1].iloc[0]["observed_at_origin"] == last+1
    assert calls[1].iloc[0]["lag_1_interval"] == last
    assert calls[1].iloc[0]["rolling_hour_mean"] == pytest.approx(np.mean([*original[-3:],last+1]))
    np.testing.assert_array_equal(series.values,original)


@pytest.mark.parametrize("value",[-10,float("nan"),float("inf"),1e20])
def test_bad_recursive_outputs_are_clipped_or_rejected(monkeypatch,value):
    _, data, series, source = source_data()
    source.results.metrics.append(source.results.metrics[0].model_copy(update={"model":"random_forest","label":"Random Forest"}))
    class Estimator:
        def fit(self,*_): pass
        def set_params(self,**_): return self
        def predict(self,_): return [value]
    monkeypatch.setattr("app.future.build_estimator",lambda _:Estimator())
    if value == -10:
        output = generate_future("f",future_request("random_forest",15),source,data,series,lambda *_:None)
        assert output.points[0].predicted_generation == 0
    else:
        with pytest.raises(ValueError):
            generate_future("f",future_request("random_forest",15),source,data,series,lambda *_:None)


@pytest.mark.parametrize("naive",[False,True])
def test_timestamp_semantics_and_zero_values(naive):
    _, data, series, source = source_data(naive=naive,zero=True)
    output = generate_future("f",future_request(),source,data,series,lambda *_:None)
    assert all(p.predicted_generation == 0 for p in output.points)
    assert (pd.Timestamp(output.points[0].timestamp).tzinfo is None) == naive
    assert (pd.Timestamp(output.origin).tzinfo is None) == naive


def test_invalid_model_and_fractional_interval_are_rejected():
    _, data, series, source = source_data(frequency=60)
    for req in [future_request("xgboost"),future_request(horizon=15)]:
        with pytest.raises(HTTPException) as exc:
            check_request(req,source,series)
        assert exc.value.status_code == 422


def test_csv_has_forecast_metadata_but_no_fake_actuals_or_errors():
    _, data, series, source = source_data()
    output = generate_future("f",future_request(horizon=60),source,data,series,lambda *_:None)
    rows = list(csv.DictReader(io.StringIO("".join(future_csv(output)))))
    assert len(rows) == 4
    assert "actual_generation" not in rows[0] and "forecast_error" not in rows[0]
    assert rows[0]["prediction_type"] == "future_forecast"
    assert rows[0]["forecast_horizon"] == "15 minutes"
    assert rows[-1]["forecast_horizon"] == "60 minutes"
    assert rows[0]["forecast_origin"] == output.origin


def source_job():
    req,_,_,_ = source_data()
    key = jobs.submit(req).job_id
    assert wait_job(key).status == "completed"
    return key,req


def test_end_to_end_future_api_keeps_backtest_results_unchanged():
    source,req = source_job()
    before = client.get(f"/api/models/results/{source}").json()
    response = client.post("/api/forecast/predict",json=future_request(source=source,horizon=60).model_dump())
    assert response.status_code == 202 and response.json()["kind"] == "future"
    key = response.json()["job_id"]
    assert wait_job(key).status == "completed"
    result = client.get(f"/api/forecast/future/{key}")
    assert result.status_code == 200 and len(result.json()["points"]) == 4
    download = client.get(f"/api/forecast/future/{key}/download")
    assert download.status_code == 200 and "solarcast-future-" in download.headers["content-disposition"]
    assert len(list(csv.DictReader(io.StringIO(download.text)))) == 4
    assert client.get(f"/api/models/results/{source}").json() == before
    assert client.get(f"/api/models/results/{key}").status_code == 409
    assert client.get(f"/api/forecast/future/{source}").status_code == 409
    client.delete(f"/api/datasets/{req.dataset_id}")
    assert client.get(f"/api/forecast/future/{key}").status_code == 404


def test_future_request_schema_and_missing_source():
    for payload in [{"source_job_id":"missing","model":"unknown"},{"source_job_id":"missing","model":"persistence","horizon_minutes":45},{"source_job_id":"missing","model":"persistence","future_actuals":[1,2]}]:
        assert client.post("/api/forecast/predict",json=payload).status_code == 422
    assert client.post("/api/forecast/predict",json=future_request(source="missing").model_dump()).status_code == 404


def test_pending_future_and_parent_removal_prevent_result_publication(monkeypatch):
    entered,release = threading.Event(),threading.Event()
    source,_ = source_job()
    original = generate_future
    def delayed(*args):
        entered.set();release.wait(timeout=10)
        return original(*args)
    monkeypatch.setattr("app.jobs.generate_future",delayed)
    key = jobs.submit_future(future_request(source=source)).job_id
    try:
        assert entered.wait(timeout=5)
        assert client.get(f"/api/forecast/future/{key}").status_code == 409
        assert client.get(f"/api/forecast/future/{key}/download").status_code == 409
        assert client.post("/api/forecast/predict",json=future_request(source=source).model_dump()).status_code == 409
        assert client.delete(f"/api/forecast/jobs/{source}").status_code == 204
        assert client.get(f"/api/forecast/status/{key}").status_code == 404
        assert client.get(f"/api/forecast/future/{key}").status_code == 404
    finally:
        release.set()


def test_future_failure_is_friendly_and_preserves_source(monkeypatch):
    source,_ = source_job()
    def fail(*_): raise RuntimeError("private internal traceback")
    monkeypatch.setattr("app.jobs.generate_future",fail)
    key = jobs.submit_future(future_request(source=source)).job_id
    state = wait_job(key)
    assert state.status == "failed" and "private" not in state.error
    assert jobs.output(source).results.job_id == source
    assert client.get(f"/api/forecast/future/{key}").status_code == 409


def test_source_expiry_removes_dependent_forecast():
    source,_ = source_job()
    key = jobs.submit_future(future_request(source=source)).job_id
    assert wait_job(key).status == "completed"
    jobs.items[source].created -= TTL_SECONDS+1
    assert client.get(f"/api/forecast/future/{key}").status_code == 404


def test_deleted_source_during_future_preparation_cannot_enqueue(monkeypatch):
    source,_ = source_job()
    original = prepare
    def removed(req):
        prepared = original(req)
        jobs.remove(source)
        return prepared
    monkeypatch.setattr("app.jobs.prepare",removed)
    with pytest.raises(HTTPException) as exc:
        jobs.submit_future(future_request(source=source))
    assert exc.value.status_code == 404
    assert not jobs.items
