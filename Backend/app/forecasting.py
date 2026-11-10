"""Direct h-step forecasting at rolling origins; all value features end at origin.

No model sees labels at/after the first evaluation origin during fitting. The
fitted model stays frozen; new actuals become usable only as time advances.
"""
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Callable

import numpy as np
import pandas as pd
from fastapi import HTTPException

from .forecast_schemas import (
    BacktestResults, BacktestSettings, BacktestPoint, ForecastOptions, HorizonOption,
    ModelFailure, ModelMetric, ModelOption, SplitSummary, TrainRequest,
)
from .services import Dataset, store, validate_dataset

LABELS = {"persistence": "Persistence", "seasonal_naive": "Seasonal naive", "random_forest": "Random Forest", "xgboost": "XGBoost", "lightgbm": "LightGBM"}
NOTES = [
    "Historical rolling-origin holdout, not future forecasts. Models are fitted once and remain frozen during evaluation.",
    "At origin t, generation at t is assumed available immediately. The target is the single observation at t + horizon, not cumulative energy.",
    "Earlier holdout actuals may be used at later origins; values after each origin are never features. This is not a fixed-origin multi-step forecast.",
    "The training fraction places a raw-row boundary. A one-day feature warmup and a horizon-sized origin purge reduce the effective training sample.",
    "Seasonal naive uses the observation exactly 24 elapsed hours before the target (not a DST-adjusted local calendar day).",
    "Skill = 100 × (1 − model RMSE / persistence RMSE). Undefined when persistence RMSE is zero. Bias = prediction − actual.",
    "The best model is ranked by RMSE on this comparison holdout. It is not an unbiased final test score after model selection. No tuning uses this holdout.",
    "All metrics cover every evaluation row and include night-time zeros. No normalization, resampling or weather features are used; negative ML outputs are clipped to zero.",
]


def options(request: BacktestSettings) -> ForecastOptions:
    report = validate_dataset(request)
    blocking_codes = {"duplicates", "ordering", "missing_intervals", "negative_generation"}
    blockers = [c.detail for c in report.checks if c.status == "error" or (c.code in blocking_codes and c.status != "pass")]
    frequency = report.frequency_minutes
    if frequency is None or frequency < 1 or frequency > 60 or not float(frequency).is_integer() or 60 % frequency:
        blockers.append("Backtesting requires a whole-minute cadence of at most 60 minutes that divides one hour exactly.")
    dataset = store.get(request.dataset_id)
    if not blockers:
        # Large values can overflow tree libraries' float32 inputs and squared errors.
        values = pd.to_numeric(dataset.frame[request.generation_column]).to_numpy(dtype=float)
        if np.max(values) > 1e12:
            blockers.append("Generation values exceed the training numeric limit (1e12). Convert to a consistent smaller unit at source.")
    horizons = []
    for minutes in (15, 60, 360, 1440):
        reason = "Resolve dataset issues first." if blockers else None
        training_rows = evaluation_rows = 0
        if not blockers:
            if minutes % frequency:
                reason = "This horizon is not an integer number of sampling intervals."
            else:
                day = int(1440 / frequency)
                h = int(minutes / frequency)
                boundary = int(len(dataset.frame) * request.train_fraction)
                training_rows = max(0, boundary - h - day)
                evaluation_rows = max(0, len(dataset.frame) - boundary - h)
                if training_rows < 2 * day or evaluation_rows < day:
                    reason = "Need at least two days of usable training examples and one full day of evaluation targets after warmup and horizon purge. Add history or adjust the split."
        horizons.append(HorizonOption(minutes=minutes, enabled=reason is None, reason=reason, training_rows=training_rows, evaluation_rows=evaluation_rows))
    warnings = [c.detail for c in report.checks if c.status == "warning" and c.code not in blocking_codes]
    return ForecastOptions(eligible=not blockers and any(h.enabled for h in horizons), blockers=blockers, warnings=warnings,
        frequency_minutes=frequency, horizons=horizons,
        models=[ModelOption(name=name, label=LABELS[name], available=find_spec(module) is not None)
                for name, module in [("random_forest", "sklearn"), ("xgboost", "xgboost"), ("lightgbm", "lightgbm")]])


@dataclass
class PreparedSeries:
    values: np.ndarray
    times: pd.DatetimeIndex
    frequency: float
    day: int
    horizon: int
    boundary: int


def prepare(request: TrainRequest) -> tuple[Dataset, PreparedSeries]:
    config = options(request)
    chosen = next(h for h in config.horizons if h.minutes == request.horizon_minutes)
    if not chosen.enabled:
        raise HTTPException(422, " ".join(config.blockers) or chosen.reason)
    unavailable = [m.label for m in config.models if m.name in request.models and not m.available]
    if unavailable:
        raise HTTPException(422, f"Install the backend ML dependencies before selecting {', '.join(unavailable)}.")
    dataset = store.get(request.dataset_id)
    # Validation already checks ISO parsing and consistent aware/naive representation.
    raw_times = dataset.frame[request.timestamp_column]
    aware = pd.Timestamp(raw_times.iloc[0]).tzinfo is not None
    times = pd.DatetimeIndex(pd.to_datetime(raw_times, format="ISO8601", utc=aware))
    frequency = config.frequency_minutes
    assert frequency is not None
    values = pd.to_numeric(dataset.frame[request.generation_column]).to_numpy(dtype=float, copy=True)
    day = int(1440 / frequency)
    # Independently verify exact cadence on source-order timestamps. Never repair data.
    if not np.all(np.diff(times.asi8) == int(frequency * 60 * 1e9)):
        raise HTTPException(422, "Backtesting requires contiguous, strictly increasing timestamps at one exact cadence.")
    return dataset, PreparedSeries(values, times, frequency, day, int(request.horizon_minutes / frequency), int(len(values) * request.train_fraction))


def make_features(series: PreparedSeries) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Feature rows indexed by origin o; target y[o+h]. No centered windows."""
    origins = np.arange(series.day, len(series.values) - series.horizon)
    return features_at_origins(series, origins), series.values[origins + series.horizon], origins


def features_at_origins(series: PreparedSeries, origins: np.ndarray) -> pd.DataFrame:
    """Shared training/inference features; target calendars need no future values."""
    y = pd.Series(series.values)
    target_times = series.times[origins] + pd.Timedelta(int(series.frequency * series.horizon * 60_000_000_000), unit="ns")
    hour = target_times.hour.to_numpy() + target_times.minute.to_numpy() / 60
    weekday = target_times.dayofweek.to_numpy()
    doy = target_times.dayofyear.to_numpy()
    features = pd.DataFrame({
        "observed_at_origin": y.iloc[origins].to_numpy(),
        "lag_1_interval": y.shift(1).iloc[origins].to_numpy(),
        "lag_1_hour": y.shift(max(1, int(60 / series.frequency))).iloc[origins].to_numpy(),
        "lag_24_hours": y.shift(series.day).iloc[origins].to_numpy(),
        "rolling_hour_mean": y.rolling(max(1, int(60 / series.frequency))).mean().iloc[origins].to_numpy(),
        "rolling_day_mean": y.rolling(series.day).mean().iloc[origins].to_numpy(),
        "rolling_day_std": y.rolling(series.day).std(ddof=0).iloc[origins].to_numpy(),
        "target_hour_sin": np.sin(2 * np.pi * hour / 24),
        "target_hour_cos": np.cos(2 * np.pi * hour / 24),
        "target_weekday_sin": np.sin(2 * np.pi * weekday / 7),
        "target_weekday_cos": np.cos(2 * np.pi * weekday / 7),
        "target_dayofyear_sin": np.sin(2 * np.pi * doy / 365.25),
        "target_dayofyear_cos": np.cos(2 * np.pi * doy / 365.25),
    })
    return features


def split_masks(origins: np.ndarray, series: PreparedSeries):
    # Purge all examples whose target would reach or cross the raw-row boundary.
    return origins + series.horizon < series.boundary, origins >= series.boundary


def baselines(series: PreparedSeries, origins: np.ndarray) -> dict[str, np.ndarray]:
    return {"persistence": series.values[origins].copy(),
            "seasonal_naive": series.values[origins + series.horizon - series.day].copy()}


def score(actual: np.ndarray, predicted: np.ndarray, baseline_rmse: float | None = None) -> dict:
    if len(actual) == 0 or actual.shape != predicted.shape or not np.isfinite(predicted).all():
        raise ValueError("Evaluation requires matching nonempty finite vectors.")
    error = predicted - actual
    mae = float(np.mean(np.abs(error)))
    rmse = float(np.sqrt(np.mean(error ** 2)))
    return {"mae": mae, "rmse": rmse, "bias": float(np.mean(error)),
            "skill": 100 * (1 - rmse / baseline_rmse) if baseline_rmse and baseline_rmse > 0 else None}


def build_estimator(name: str):
    # Fixed, modest CPU budgets; no hyperparameter search or holdout early stopping.
    if name == "random_forest":
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(n_estimators=100, max_depth=12, min_samples_leaf=3, random_state=42, n_jobs=2)
    if name == "xgboost":
        from xgboost import XGBRegressor
        return XGBRegressor(n_estimators=120, max_depth=5, learning_rate=0.05, objective="reg:squarederror", tree_method="hist", random_state=42, n_jobs=2)
    if name == "lightgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(n_estimators=120, num_leaves=24, learning_rate=0.05, verbosity=-1, random_state=42, n_jobs=2, deterministic=True, force_col_wise=True)
    raise ValueError("Unknown model")


@dataclass
class RunOutput:
    results: BacktestResults
    times: list[str]
    origins: list[str]
    actual: np.ndarray
    predictions: dict[str, np.ndarray]
    request: TrainRequest | None = None


def run_backtest(job_id: str, request: TrainRequest, dataset: Dataset, series: PreparedSeries,
                 progress: Callable[[str, int], None]) -> RunOutput:
    import logging

    progress("Generating features", 10)
    features, targets, origins = make_features(series)
    train, test = split_masks(origins, series)
    train_origins, test_origins = origins[train], origins[test]
    actual = targets[test]
    progress("Evaluating baselines", 20)
    predictions = baselines(series, test_origins)
    baseline_rmse = score(actual, predictions["persistence"])["rmse"]
    metrics = [ModelMetric(model=name, label=LABELS[name], **score(actual, pred, baseline_rmse)) for name, pred in predictions.items()]
    failures = []
    for index, name in enumerate(request.models):
        progress(f"Training {LABELS[name]}", 30 + int(index * 50 / max(1, len(request.models))))
        try:
            estimator = build_estimator(name)
            estimator.fit(features.loc[train], targets[train])
            predicted = np.maximum(0.0, np.asarray(estimator.predict(features.loc[test]), dtype=float))
            metric = ModelMetric(model=name, label=LABELS[name], **score(actual, predicted, baseline_rmse))
            predictions[name] = predicted
            metrics.append(metric)
        except Exception:
            logging.getLogger("solarcast").exception("Model %s failed for job %s", name, job_id)
            failures.append(ModelFailure(model=name, message=f"{LABELS[name]} could not train or evaluate. Other model results remain available. Check the local backend log."))
    progress("Comparing holdout performance", 90)
    metrics.sort(key=lambda m: m.rmse)
    timestamps = [series.times[o + series.horizon].isoformat() for o in test_origins]
    origin_times = [series.times[o].isoformat() for o in test_origins]
    split = SplitSummary(training_rows=int(train.sum()), evaluation_rows=int(test.sum()), warmup_rows=series.day,
        purged_origins=series.horizon, first_training_origin=series.times[train_origins[0]].isoformat(),
        last_training_target=series.times[train_origins[-1] + series.horizon].isoformat(),
        first_evaluation_origin=origin_times[0], first_evaluation_target=timestamps[0], last_evaluation_target=timestamps[-1],
        timezone="UTC" if series.times.tz is not None else "Unspecified; original naive clock")
    points = [BacktestPoint(timestamp=timestamps[i], origin=origin_times[i], actual_generation=float(actual[i]),
                predictions={name: float(values[i]) for name, values in predictions.items()}) for i in range(min(len(actual), 2000))]
    result = BacktestResults(job_id=job_id, dataset_id=request.dataset_id, filename=dataset.filename, demo=dataset.demo,
        horizon_minutes=request.horizon_minutes, frequency_minutes=series.frequency, train_fraction=request.train_fraction,
        best_model=metrics[0].model, metrics=metrics, failures=failures, split=split, points=points,
        total_predictions_per_model=len(actual), feature_names=list(features.columns), notes=NOTES)
    return RunOutput(result, timestamps, origin_times, actual, predictions, request.model_copy(deep=True))
