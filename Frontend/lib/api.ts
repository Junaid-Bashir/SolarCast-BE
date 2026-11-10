export type Inspection = { dataset_id: string; filename: string; demo: boolean; columns: string[]; row_count: number; preview: Record<string, string>[] };
export type Mapping = { dataset_id: string; timestamp_column: string; generation_column: string; capacity_column: string | null };
export type Check = { code: string; label: string; status: "pass" | "warning" | "error"; detail: string; count: number | null };
export type Report = { dataset_id: string; filename: string; demo: boolean; status: "healthy" | "attention" | "invalid"; observations: number; start: string | null; end: string | null; frequency_minutes: number | null; timezone: string | null; missing_generation: number; checks: Check[]; points: { timestamp: string; generation: number | null }[]; chart_total_points: number; chart_note: string };
export type MLModel = "random_forest" | "xgboost" | "lightgbm";
export type ModelName = MLModel | "persistence" | "seasonal_naive";
export type ForecastOptions = { eligible: boolean; blockers: string[]; warnings: string[]; frequency_minutes: number | null; horizons: { minutes: number; enabled: boolean; reason: string | null; training_rows: number; evaluation_rows: number }[]; models: { name: MLModel; label: string; available: boolean }[] };
export type Job = { job_id: string; dataset_id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled"; progress: number; stage: string; error: string | null };
export type ModelMetric = { model: ModelName; label: string; mae: number; rmse: number; bias: number; skill: number | null };
export type BacktestPoint = { timestamp: string; origin: string; actual_generation: number; predictions: Record<string, number> };
export type BacktestResults = { job_id: string; dataset_id: string; filename: string; demo: boolean; horizon_minutes: number; frequency_minutes: number; train_fraction: number; evaluation: "rolling_origin_holdout"; best_model: ModelName; metrics: ModelMetric[]; failures: { model: string; message: string }[]; split: { training_rows: number; evaluation_rows: number; warmup_rows: number; purged_origins: number; first_training_origin: string; last_training_target: string; first_evaluation_origin: string; first_evaluation_target: string; last_evaluation_target: string; timezone: string }; points: BacktestPoint[]; total_predictions_per_model: number; feature_names: string[]; notes: string[] };
export type FutureForecast = { forecast_id: string; source_job_id: string; dataset_id: string; filename: string; demo: boolean; kind: "future_forecast"; model: ModelName; model_label: string; method: "recursive_one_step" | "persistence" | "seasonal_naive"; fit_strategy: "refit_all_observed_history" | "observed_history_baseline"; training_rows: number; backtest_horizon_minutes: number; horizon_minutes: number; frequency_minutes: number; origin: string; generated_at: string; timezone: string; context: { timestamp: string; generation: number | null }[]; points: { timestamp: string; predicted_generation: number; lead_minutes: number }[]; notes: string[] };
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api${path}`, init); }
  catch { throw new Error("Cannot reach the local service. Start the backend on port 8000 and try again."); }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "The local service could not process this request. Check your inputs and that the backend is running.");
  }
  return response.json();
}
