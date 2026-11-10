# Future inference contract

## Scope and meaning of future

Phase 3 forecasts begin strictly after the final timestamp of the selected comparison's uploaded dataset. The cutoff is the forecast origin; it is not a live wall-clock origin. Generated-at time is separately recorded in UTC. For the June 2026 synthetic demo, forecast dates remain in June even if the app is used in November. UI/CSV metadata distinguishes these dates.

Only algorithms that completed successfully in the source comparison can be selected. Supported forecast windows are 15/60/360/1440 minutes when divisible by the data cadence. There is no arbitrary start date, externally provided future actual, weather input, or multi-site inference. The source dataset must remain available and valid.

## Refit and recursion

Phase 2 estimates `y[o+h]` at rolling historical origins with a frozen model. Phase 3 intentionally refits the chosen algorithm for **one-step** prediction on every known labeled example, using fixed algorithm settings. This includes the original comparison holdout because all of its observations are known at the new forecast origin. It does not rewrite or reevaluate Phase 2 metrics.

For an uploaded series of length `N`, 24-hour lag `d`, and one-step lead, refit origins are `d ... N-2`, with targets `y[d+1] ... y[N-1]`. Thus the last fitted label is the final observed value; no unobserved label is used. There are `N-d-1` refit examples.

Inference begins at origin `N-1`. Predict one sampling interval ahead, append that prediction to a private working buffer, then compute the next row from that augmented history. The buffer is only `d+1` values long, enough for all origin-relative lags/windows. It never writes into the dataset store or Phase 2 outputs. Target calendars are derived from the origin plus one known interval, without requiring future generation values.

`features_at_origins` is shared by backtest training, the all-history refit, and future inference. It uses values no later than the current working origin: observation, one-interval/hour/day lags, trailing hour/day means, day standard deviation, and known target-calendar cycles. Tests spy on the exact refit rows/labels and show each generated prediction feeding the next lag/rolling window. Perturbation tests from Phase 2 remain in place.

The selected model's backtest score is **not** an accuracy estimate for this changed refit/recursive strategy. It remains useful only as the original comparison score. Recursive errors may compound; there are no future MAE/RMSE/bias/skill values or uncertainty bands. Future evaluation would require a separately backtested recursive fixed-origin strategy.

## Baselines and physical limits

Persistence holds the final observed generation value for every lead. Seasonal naive repeats the last elapsed 24 hours at the same cadence. Every seasonal source value is observed because windows never exceed 24 hours. These baselines need no fit; their fit-example count is zero.

Aware clocks are analyzed in UTC; naive clocks remain unspecified. Elapsed-day seasonality is not DST-adjusted local-clock alignment. Observation at the cutoff is assumed available immediately. Delayed telemetry/interval-start semantics need an explicit future availability setting.

Generation uses the source unit and represents individual future observations, not integrated energy over the window. Negative ML outputs are clipped to zero. Non-finite or numerically unstable outputs fail the job. No installed-capacity scaling, geographic daylight calculation, or forced nighttime zeros is applied. No weather is assumed known. These are data-driven forecasts with limitations, not physical PV simulations.

## API and lifecycle

`POST /api/forecast/predict` accepts exactly `source_job_id`, `model`, and `horizon_minutes`. It returns HTTP 202 and a job with `kind=future`; clients poll the existing status endpoint. Unexpected input fields are rejected.

`GET /api/forecast/future/{job_id}` returns only after completion: kind, model/method/fit strategy, source comparison ID, dataset metadata, origin, generated-at, cadence/window, observed context, future points with individual lead minutes, refit sample count, and methodology notes. No future actuals/errors are fabricated.

`GET /api/forecast/future/{job_id}/download` streams a distinctly named `solarcast-future-*.csv`. It contains timestamp, predicted generation, model, per-row `forecast_horizon`, fixed origin, full `forecast_window_minutes`, prediction type, method, fit strategy, and generated-at time. Every future point is exported. Backtest CSV remains a different endpoint/file with its original actual/error fields.

Both job types share the single worker, four-task bound, ten-job retention limit, and one-hour lazy expiry. Removing a dataset or source comparison removes/cancels its dependent forecasts. Removing only a future job preserves the source. Parent registration is rechecked under the job lock to cover deletion during preparation. Cancellation is checked between stages and periodically during recursion; a running estimator fit must return before working data is released. Results cannot publish after removal. No model artifacts are saved.

## References and next work

The causal lag-feature approach follows the principles illustrated in [scikit-learn's lagged-feature forecasting example](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html). The refit/recursive origin contract, buffer, job lifecycle, and disclosures are implemented locally.

Next work should evaluate the complete recursive strategy at historical fixed origins, separate model selection from an untouched final test, account for delayed measurements and local solar geometry, and calibrate uncertainty before exposing confidence bands. The existing research project should be reviewed when its source is available; no unavailable research methodology is claimed here.
