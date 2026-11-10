# Chronological backtesting contract

## Data availability and forecast target

A contiguous series has `N` observations, sampling interval `f` minutes, and `d = 1440/f` observations per elapsed 24 hours. Horizon `H` is `h = H/f` steps, with `1 <= h <= d`. Forecast issued at origin `o` predicts the single observation `y[o+h]`, not cumulative horizon energy. Observation `y[o]` is assumed available immediately. Values after `o` are unavailable.

Interval-start/end conventions and delayed telemetry matter: this release assumes immediate availability at the supplied timestamp. Weather and capacity columns are not predictors. Naive timestamps preserve the unspecified source clock. Aware timestamps use UTC for analysis and calendar features. Seasonal periods are 24 elapsed hours, not DST-adjusted local-calendar days. Original offsets remain in the source preview.

## Exact split

For training fraction `p`, raw-row boundary is `b = floor(N*p)`.

- Create feature rows at origins `d <= o < N-h`.
- Train on origins where `o+h < b`.
- Evaluate on origins where `o >= b`.
- Purge origins from `b-h` through `b-1` (exactly `h` origins).

The last training target is `b-1`, strictly earlier than first evaluation origin `b`. First scored target is `b+h`, not `b`. Usable training count is `b-h-d`; evaluation count is `N-b-h`. Require at least `2d` training and `d` evaluation examples. Source rows are not removed: warmup/purge affect only supervised examples. Responses expose counts and split timestamps.

Each estimator is fitted once and frozen through evaluation. Successive origins may use actual observations that have become available, including earlier holdout actuals. This is **rolling-origin historical holdout evaluation without refitting**, not a fixed-origin multi-step forecast or multi-fold cross-validation. No random split, holdout early stopping, or tuning on holdout labels is performed. The lowest holdout RMSE identifies the best model on this sample; a separate untouched test set is needed for an unbiased score after model selection.

## Baselines first

Persistence: `prediction[o+h] = y[o]`.

Seasonal naive: `prediction[o+h] = y[o+h-d]`.

Because `h <= d`, the seasonal source never lies after the origin. At a 24-hour horizon, these baselines coincide by definition. Every successful model is scored on exactly the same target rows.

## Causal features and estimators

Value features are observation at origin; lags of one interval, one hour, and 24 hours relative to origin; trailing one-hour mean; trailing 24-hour mean and population standard deviation. Windows include the current origin and end there. No centered windows, resampling, imputation, or forward/backward filling are used. Target calendar uses known sine/cosine pairs of hour, weekday, and day of year. Cadence must be whole minutes dividing an hour, so hourly windows are exact.

No learned transform or feature selection is fitted on the full series. Feature generation can run over the full sequence because each row is causal. Tests mutate every value after an origin and assert that earlier features/baselines do not change. Separate tests mutate the holdout and verify training features/labels cannot change, and spy on the actual estimator fit/predict boundaries.

| Model | Fixed settings |
|---|---|
| Random Forest | 100 trees, max depth 12, min leaf 3 |
| XGBoost | 120 trees, max depth 5, learning rate .05, squared-error objective, histogram trees |
| LightGBM | 120 trees, 24 leaves, learning rate .05, deterministic CPU mode, column-wise histograms |

All estimators use seed 42 and at most two CPU threads. Negative ML predictions are clipped to zero before scoring and export; uploaded values remain untouched. Library versions are pinned in `requirements-lock.txt`; cross-platform/version bitwise identity is not guaranteed.

Primary references: [scikit-learn chronological feature engineering](https://scikit-learn.org/stable/auto_examples/applications/plot_cyclical_feature_engineering.html), [XGBoost estimators](https://xgboost.readthedocs.io/en/latest/python/python_api.html), [LightGBM regressor](https://lightgbm.readthedocs.io/en/latest/pythonapi/lightgbm.LGBMRegressor.html). The explicit origin/target purge above is implemented locally; a library splitter alone would not establish feature causality.

## Metrics and predictions

For error `e = prediction - actual`, MAE is `mean(abs(e))`, RMSE is `sqrt(mean(e²))`, and bias is `mean(e)`. All errors use source units. Skill against persistence is `100 * (1 - model_RMSE / persistence_RMSE)`; positive is better, negative worse. When persistence RMSE is zero, every skill is null rather than infinity or an invented zero. Nighttime zeros are included and can materially affect performance.

Metrics cover every evaluation target. The chart shows the first 2,000 consecutive scored targets. CSV includes all successful models, all targets, forecast origins, horizons, actuals, predictions, and signed errors. No future generation, confidence interval, or aggregated energy prediction is implied.

## Job lifecycle

`JobManager` is the replaceable queue boundary: one worker, four outstanding tasks, and ten retained jobs. A semaphore counts cancelled-but-not-yet-released tasks, so repeated cancellation cannot bypass the outstanding-work limit. Progress describes real stages, not invented per-tree percentages. Individual model failures preserve valid baseline/model results; whole-job failures are friendly and keep tracebacks server-side.

Stores have one-hour logical expiry, lazy purge, and no durability. Deletion/cancellation takes effect at stage boundaries; an estimator already fitting finishes its call before its working data is released and output discarded. Dataset deletion cascades to jobs. An active download can finish after deletion. Restart clears all state. Production queues, hard execution deadlines, user isolation, durable models, tuning, multiple folds, and future inference are separate extensions.
