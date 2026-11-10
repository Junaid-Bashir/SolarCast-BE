"""Forecast strictly beyond the uploaded history; no future actuals or weather."""
import csv
import io
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd
from fastapi import HTTPException

from .forecast_schemas import FutureForecast, FuturePoint, FutureRequest
from .forecasting import LABELS, PreparedSeries, RunOutput, build_estimator, features_at_origins, make_features
from .schemas import Point
from .services import Dataset

NOTES = [
    "Future means strictly after the uploaded dataset's final timestamp, not necessarily after today's date. No live meter feed or weather forecast is used.",
    "The selected algorithm is refitted as a one-step model on all uploaded observations with known labels. Historical backtest models, predictions and scores remain unchanged.",
    "ML forecasts advance one sampling interval at a time. Predicted values feed later lags and rolling statistics; no future actuals are assumed. Errors may accumulate with lead time.",
    "Backtest scores describe fixed-lead rolling-origin holdout evaluation, not this refitted recursive trajectory. No accuracy score or uncertainty band is claimed for unseen targets.",
    "Persistence holds the final observed value. Seasonal naive repeats values from 24 elapsed hours earlier, not a DST-adjusted local calendar day.",
    "Generation at the cutoff is assumed available immediately. Forecasts use source units, predict individual observations rather than horizon energy, and clip negative ML outputs to zero.",
]


def check_request(request: FutureRequest, source: RunOutput, series: PreparedSeries):
    if request.model not in {m.model for m in source.results.metrics}:
        raise HTTPException(422, "Choose a model that completed successfully in this comparison.")
    if request.horizon_minutes % series.frequency:
        raise HTTPException(422, "The forecast window must contain an integer number of sampling intervals.")
    try:
        end = series.times[-1] + pd.Timedelta(request.horizon_minutes * 60_000_000_000, unit="ns")
        _ = end.value  # Enforce the same timestamp range as dataset inspection.
    except (OverflowError, ValueError, pd.errors.OutOfBoundsDatetime):
        raise HTTPException(422, "The future forecast would exceed the supported timestamp range.") from None


def generate_future(forecast_id: str, request: FutureRequest, source: RunOutput,
                    dataset: Dataset, series: PreparedSeries, progress: Callable[[str, int], None]) -> FutureForecast:
    check_request(request, source, series)
    progress("Preparing observed history", 5)
    one_step = replace(series, horizon=1)
    estimator = None
    training_rows = 0
    if request.model not in ("persistence", "seasonal_naive"):
        progress(f"Refitting {LABELS[request.model]} on observed history", 15)
        features, targets, _ = make_features(one_step)
        estimator = build_estimator(request.model)
        estimator.fit(features, targets)
        training_rows = len(targets)
        # Predict many small rows without repeatedly spawning parallel tree workers.
        estimator.set_params(n_jobs=1)
    steps = int(request.horizon_minutes / series.frequency)
    delta = pd.Timedelta(int(series.frequency * 60_000_000_000), unit="ns")
    origin = series.times[-1]
    buffer = series.values[-(series.day + 1):].copy()
    clock = series.times[-(series.day + 1):].copy()
    points = []
    progress("Generating future trajectory", 35)
    for step in range(1, steps + 1):
        if request.model == "persistence":
            predicted = float(series.values[-1])
        elif request.model == "seasonal_naive":
            # Every referenced value is observed: forecast window is at most 24h.
            predicted = float(series.values[len(series.values) - 1 + step - series.day])
        else:
            local = replace(one_step, values=buffer, times=clock)
            row = features_at_origins(local, np.array([len(buffer)-1]))
            raw = float(estimator.predict(row)[0])
            if not np.isfinite(raw) or abs(raw) > 1e12:
                raise ValueError("Recursive prediction is non-finite or outside the model numeric range")
            predicted = max(0.0, raw)
        stamp = origin + step * delta
        points.append(FuturePoint(timestamp=stamp.isoformat(), predicted_generation=predicted, lead_minutes=step*series.frequency))
        if estimator is not None:
            buffer = np.append(buffer[1:], predicted)
            clock = clock[1:].append(pd.DatetimeIndex([stamp]))
        # Also provides cooperative cancellation during long recursive trajectories.
        if step % max(1, steps // 20) == 0 or step == steps:
            progress("Generating future trajectory", 35 + int(60 * step / steps))
    context = [Point(timestamp=t.isoformat(), generation=float(v))
               for t, v in zip(series.times[-series.day:], series.values[-series.day:])]
    return FutureForecast(forecast_id=forecast_id, source_job_id=request.source_job_id, dataset_id=source.results.dataset_id,
        filename=dataset.filename, demo=dataset.demo, model=request.model, model_label=LABELS[request.model],
        method="recursive_one_step" if estimator is not None else request.model,
        fit_strategy="refit_all_observed_history" if estimator is not None else "observed_history_baseline",
        training_rows=training_rows, backtest_horizon_minutes=source.results.horizon_minutes,
        horizon_minutes=request.horizon_minutes, frequency_minutes=series.frequency, origin=origin.isoformat(),
        generated_at=datetime.now(timezone.utc).isoformat(), timezone=source.results.split.timezone,
        context=context, points=points, notes=NOTES if estimator is not None else [NOTES[0],
            "The selected baseline uses only observed history; no estimator refit or future actuals are required. Backtest scores remain unchanged.",
            NOTES[3], NOTES[4], NOTES[5]])


def future_csv(forecast: FutureForecast):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "predicted_generation", "model", "forecast_horizon", "forecast_origin",
                     "forecast_window_minutes", "prediction_type", "method", "fit_strategy", "generated_at"])
    yield buffer.getvalue()
    for point in forecast.points:
        buffer.seek(0); buffer.truncate(0)
        writer.writerow([point.timestamp, point.predicted_generation, forecast.model, f"{point.lead_minutes:g} minutes",
            forecast.origin, forecast.horizon_minutes, forecast.kind, forecast.method, forecast.fit_strategy, forecast.generated_at])
        yield buffer.getvalue()
