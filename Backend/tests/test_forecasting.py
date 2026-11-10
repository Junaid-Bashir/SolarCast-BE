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

from app.forecast_schemas import BacktestSettings, TrainRequest
from app.forecasting import baselines, make_features, options, prepare, run_backtest, score, split_masks
from app.jobs import JobManager, jobs, prediction_csv
from app.main import app
from app.services import TTL_SECONDS, inspect_csv, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_jobs_and_data():
    store.items.clear()
    yield
    for key in list(jobs.items):
        jobs.remove(key)
    store.items.clear()


def dataset(days=12, frequency=15, naive=False, values=None):
    count = int(days * 1440 / frequency)
    times = pd.date_range("2026-06-01", periods=count, freq=f"{frequency}min", tz=None if naive else "UTC")
    y = values if values is not None else np.maximum(0, np.sin(np.arange(count) * 2 * np.pi / (1440/frequency))) * 100
    frame = pd.DataFrame({"when": [t.isoformat() for t in times], "power": y})
    uploaded = inspect_csv(frame.to_csv(index=False).encode(), "custom.csv")
    return uploaded, frame


def request(uploaded, **kwargs):
    return TrainRequest(dataset_id=uploaded.dataset_id, timestamp_column="when", generation_column="power", models=[], **kwargs)


@pytest.mark.parametrize("horizon", [15, 60, 360, 1440])
def test_origin_features_cannot_see_future_values(horizon):
    uploaded, _ = dataset()
    req = request(uploaded, horizon_minutes=horizon)
    _, series = prepare(req)
    x, targets, origins = make_features(series)
    origin = int(origins[len(origins)//2])
    changed = series.values.copy()
    changed[origin+1:] += 10000
    altered_x, altered_targets, _ = make_features(replace(series, values=changed))
    # Changing every value AFTER the origin must not change any feature at/before it.
    pd.testing.assert_frame_equal(x.loc[origins <= origin], altered_x.loc[origins <= origin])
    assert not np.array_equal(targets, altered_targets)
    base = baselines(series, np.array([origin]))
    altered_base = baselines(replace(series, values=changed), np.array([origin]))
    for name in base:
        np.testing.assert_array_equal(base[name], altered_base[name])


@pytest.mark.parametrize("horizon", [15, 60, 360, 1440])
def test_train_targets_are_strictly_before_evaluation_origins(horizon):
    uploaded, _ = dataset()
    _, series = prepare(request(uploaded, horizon_minutes=horizon))
    x, y, origins = make_features(series)
    train, test = split_masks(origins, series)
    assert max(origins[train] + series.horizon) < min(origins[test])
    assert int((~(train | test)).sum()) == series.horizon
    mutated = series.values.copy()
    mutated[series.boundary:] = 8888
    x2, y2, origins2 = make_features(replace(series, values=mutated))
    pd.testing.assert_frame_equal(x.loc[train], x2.loc[train])
    np.testing.assert_array_equal(y[train], y2[train])
    np.testing.assert_array_equal(origins, origins2)


def test_features_and_baselines_have_exact_origin_alignment():
    uploaded, _ = dataset(values=np.arange(12*96, dtype=float))
    _, series = prepare(request(uploaded, horizon_minutes=360))
    x, y, origins = make_features(series)
    first = origins[0]
    assert first == 96
    assert x.iloc[0]["observed_at_origin"] == first
    assert x.iloc[0]["lag_1_interval"] == first-1
    assert x.iloc[0]["lag_1_hour"] == first-4
    assert x.iloc[0]["lag_24_hours"] == first-96
    assert x.iloc[0]["rolling_hour_mean"] == np.mean(series.values[first-3:first+1])
    assert x.iloc[0]["rolling_day_mean"] == np.mean(series.values[first-95:first+1])
    assert y[0] == first+24
    base = baselines(series, np.array([first]))
    assert base["persistence"][0] == first
    assert base["seasonal_naive"][0] == first+24-96


def test_metric_definitions_and_zero_baseline():
    actual = np.array([1., 2., 3.])
    predicted = np.array([2., 2., 5.])
    result = score(actual, predicted, 2)
    assert result["mae"] == 1
    assert result["bias"] == 1
    assert result["rmse"] == pytest.approx(np.sqrt(5/3))
    assert result["skill"] == pytest.approx(100*(1-np.sqrt(5/3)/2))
    assert score(actual, actual, 0)["skill"] is None
    assert score(actual, actual, 2)["skill"] == 100
    assert score(actual, predicted, .1)["skill"] < 0


@pytest.mark.parametrize("bad", ["gap", "negative", "duplicate", "unordered", "missing", "text", "large"])
def test_training_blocks_unrepaired_bad_data(bad):
    uploaded, frame = dataset()
    if bad == "gap": frame = frame.drop(100)
    elif bad == "negative": frame.loc[100,"power"] = -1
    elif bad == "duplicate": frame.loc[100,"when"] = frame.loc[99,"when"]
    elif bad == "unordered": frame = frame.iloc[::-1]
    elif bad == "missing": frame.loc[100,"power"] = np.nan
    elif bad == "text": frame["power"] = frame["power"].astype(str); frame.loc[100,"power"] = "invalid"
    elif bad == "large": frame.loc[100,"power"] = 1e20
    uploaded = inspect_csv(frame.to_csv(index=False).encode(), "bad.csv")
    config = options(request(uploaded))
    assert not config.eligible and config.blockers
    with pytest.raises(HTTPException) as exc:
        prepare(request(uploaded))
    assert exc.value.status_code == 422


def test_frequency_and_history_disable_horizons():
    uploaded, _ = dataset(frequency=60)
    config = options(request(uploaded))
    assert not config.horizons[0].enabled
    assert config.horizons[1].enabled
    uploaded, _ = dataset(days=2)
    assert not options(request(uploaded)).eligible
    uploaded, _ = dataset(frequency=40)
    assert not options(request(uploaded)).eligible


def test_zero_series_and_naive_time_are_supported():
    uploaded, _ = dataset(naive=True, values=np.zeros(12*96))
    req = request(uploaded)
    data, series = prepare(req)
    output = run_backtest("job", req, data, series, lambda *_: None)
    assert all(m.rmse == 0 and m.skill is None for m in output.results.metrics)
    assert output.results.split.timezone.startswith("Unspecified")
    assert "+00:00" not in output.times[0]


@pytest.mark.parametrize("model", ["random_forest", "xgboost", "lightgbm"])
def test_real_model_training_and_same_timestamps(model):
    uploaded, _ = dataset()
    req = request(uploaded)
    req.models = [model]
    data, series = prepare(req)
    output = run_backtest("job", req, data, series, lambda *_: None)
    assert not output.results.failures
    assert set(output.predictions) == {"persistence", "seasonal_naive", model}
    assert all(len(p) == len(output.actual) for p in output.predictions.values())
    assert all(m.mae >= 0 and np.isfinite(m.rmse) for m in output.results.metrics)
    assert np.all(output.predictions[model] >= 0)


def test_fitting_receives_only_training_rows_and_no_holdout_eval_set(monkeypatch):
    uploaded, _ = dataset()
    req = request(uploaded)
    req.models = ["random_forest"]
    data, series = prepare(req)
    x, y, origins = make_features(series)
    train, test = split_masks(origins, series)
    class Spy:
        def fit(self, actual_x, actual_y):
            pd.testing.assert_frame_equal(actual_x, x.loc[train])
            np.testing.assert_array_equal(actual_y, y[train])
        def predict(self, actual_x):
            pd.testing.assert_frame_equal(actual_x, x.loc[test])
            return np.zeros(test.sum())
    monkeypatch.setattr("app.forecasting.build_estimator", lambda _: Spy())
    output = run_backtest("job", req, data, series, lambda *_: None)
    assert len(output.results.metrics) == 3 and not output.results.failures


def test_individual_model_failure_keeps_baselines(monkeypatch):
    uploaded, _ = dataset()
    req = request(uploaded); req.models = ["random_forest"]
    data, series = prepare(req)
    def fail(_): raise RuntimeError("PRIVATE TRACE")
    monkeypatch.setattr("app.forecasting.build_estimator", fail)
    output = run_backtest("job", req, data, series, lambda *_: None)
    assert len(output.results.metrics) == 2
    assert output.results.failures[0].model == "random_forest"
    assert "PRIVATE TRACE" not in output.results.model_dump_json()


def wait_for_job(key):
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        state = client.get(f"/api/forecast/status/{key}").json()
        if state["status"] in ("completed", "failed"):
            return state
        time.sleep(.02)
    pytest.fail("Job did not finish before deadline")


def test_job_api_and_full_prediction_download():
    uploaded, _ = dataset()
    req = request(uploaded)
    config = client.post("/api/forecast/options", json=req.model_dump())
    assert config.status_code == 200 and config.json()["eligible"]
    response = client.post("/api/forecast/train", json=req.model_dump())
    assert response.status_code == 202
    key = response.json()["job_id"]
    assert wait_for_job(key)["status"] == "completed"
    result = client.get(f"/api/models/results/{key}")
    assert result.status_code == 200
    data = result.json()
    assert data["evaluation"] == "rolling_origin_holdout"
    assert data["split"]["last_training_target"] < data["split"]["first_evaluation_origin"]
    csv_response = client.get(f"/api/forecast/download/{key}")
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(rows) == 2*data["total_predictions_per_model"]
    assert float(rows[0]["forecast_error"]) == pytest.approx(float(rows[0]["predicted_generation"])-float(rows[0]["actual_generation"]))
    assert rows[0]["forecast_horizon"] == "60 minutes"
    assert client.delete(f"/api/datasets/{uploaded.dataset_id}").status_code == 204
    assert client.get(f"/api/models/results/{key}").status_code == 404


def test_pending_results_duplicate_job_and_removal(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = run_backtest
    def delayed(*args):
        entered.set()
        release.wait(timeout=10)
        return original(*args)
    monkeypatch.setattr("app.jobs.run_backtest", delayed)
    uploaded, _ = dataset()
    response = client.post("/api/forecast/train", json=request(uploaded).model_dump())
    key = response.json()["job_id"]
    try:
        assert entered.wait(timeout=5)
        assert client.get(f"/api/models/results/{key}").status_code == 409
        assert client.get(f"/api/forecast/download/{key}").status_code == 409
        assert client.post("/api/forecast/train", json=request(uploaded).model_dump()).status_code == 409
        assert client.delete(f"/api/forecast/jobs/{key}").status_code == 204
        assert client.get(f"/api/forecast/status/{key}").status_code == 404
    finally:
        release.set()


def test_failed_job_has_friendly_status(monkeypatch):
    def fail(*_): raise RuntimeError("sensitive internal failure")
    monkeypatch.setattr("app.jobs.run_backtest", fail)
    uploaded, _ = dataset()
    key = client.post("/api/forecast/train", json=request(uploaded).model_dump()).json()["job_id"]
    state = wait_for_job(key)
    assert state["status"] == "failed"
    assert "sensitive" not in state["error"]


def test_request_schema_rejects_invalid_configuration():
    uploaded, _ = dataset()
    payload = request(uploaded).model_dump()
    for update in [{"models":["unknown"]}, {"models":["xgboost", "xgboost"]}, {"train_fraction":.99}, {"horizon_minutes":45}]:
        assert client.post("/api/forecast/train", json={**payload, **update}).status_code == 422
    assert client.get("/api/forecast/status/unknown").status_code == 404


def test_job_expiry():
    uploaded, _ = dataset()
    key = jobs.submit(request(uploaded)).job_id
    assert wait_for_job(key)["status"] == "completed"
    jobs.items[key].created -= TTL_SECONDS+1
    assert client.get(f"/api/forecast/status/{key}").status_code == 404


def test_24_hour_baselines_coincide():
    uploaded, _ = dataset()
    _, series = prepare(request(uploaded, horizon_minutes=1440))
    _, _, origins = make_features(series)
    predicted = baselines(series, origins)
    np.testing.assert_array_equal(predicted["persistence"], predicted["seasonal_naive"])


def test_missing_selected_dependency_is_a_friendly_configuration_error(monkeypatch):
    from app.forecasting import find_spec as original
    monkeypatch.setattr("app.forecasting.find_spec", lambda name: None if name == "xgboost" else original(name))
    uploaded, _ = dataset()
    req = request(uploaded); req.models = ["xgboost"]
    assert not next(m for m in options(req).models if m.name == "xgboost").available
    with pytest.raises(HTTPException) as exc:
        prepare(req)
    assert exc.value.status_code == 422 and "Install" in exc.value.detail


def test_full_metrics_and_csv_are_not_limited_to_chart_prefix():
    uploaded, frame = dataset(days=160)
    before = store.get(uploaded.dataset_id).frame.copy(deep=True)
    req = request(uploaded)
    data, series = prepare(req)
    output = run_backtest("job", req, data, series, lambda *_: None)
    assert len(output.results.points) == 2000
    assert output.results.total_predictions_per_model > 2000
    rows = list(csv.DictReader(io.StringIO("".join(prediction_csv(output)))))
    assert len(rows) == 2 * len(output.actual)
    assert rows[len(output.actual)-1]["timestamp"] == output.results.split.last_evaluation_target
    pd.testing.assert_frame_equal(store.get(uploaded.dataset_id).frame, before)
    for metric in output.results.metrics:
        expected = score(output.actual, output.predictions[metric.model])
        assert metric.mae == expected["mae"] and metric.rmse == expected["rmse"]


def test_cancelled_tasks_still_count_toward_worker_capacity(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = run_backtest
    def delayed(*args):
        entered.set(); release.wait(timeout=10)
        return original(*args)
    monkeypatch.setattr("app.jobs.run_backtest", delayed)
    manager = JobManager()
    try:
        keys=[]
        for _ in range(4):
            uploaded, _ = dataset()
            keys.append(manager.submit(request(uploaded)).job_id)
        assert entered.wait(timeout=5)
        for key in keys:
            manager.remove(key)
        uploaded, _ = dataset()
        with pytest.raises(HTTPException) as exc:
            manager.submit(request(uploaded))
        assert exc.value.status_code == 429
    finally:
        release.set()
        manager.close()


def test_dataset_deleted_during_preparation_cannot_enqueue_a_job(monkeypatch):
    original = prepare
    def deleted_during_prepare(req):
        prepared = original(req)
        store.delete(req.dataset_id)
        return prepared
    monkeypatch.setattr("app.jobs.prepare", deleted_during_prepare)
    uploaded, _ = dataset()
    manager = JobManager()
    try:
        with pytest.raises(HTTPException) as exc:
            manager.submit(request(uploaded))
        assert exc.value.status_code == 404
        assert not manager.items
    finally:
        manager.close()
