from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import MappingRequest, Point

MLModel = Literal["random_forest", "xgboost", "lightgbm"]
ModelName = Literal["persistence", "seasonal_naive", "random_forest", "xgboost", "lightgbm"]


class BacktestSettings(MappingRequest):
    train_fraction: float = Field(default=0.8, ge=0.6, le=0.9)


class TrainRequest(BacktestSettings):
    horizon_minutes: Literal[15, 60, 360, 1440] = 60
    models: list[MLModel] = Field(default_factory=lambda: ["random_forest", "xgboost", "lightgbm"], max_length=3)

    @field_validator("models")
    @classmethod
    def unique_models(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Select each model only once.")
        return value


class HorizonOption(BaseModel):
    minutes: int
    enabled: bool
    reason: str | None = None
    training_rows: int = 0
    evaluation_rows: int = 0


class ModelOption(BaseModel):
    name: MLModel
    label: str
    available: bool


class ForecastOptions(BaseModel):
    eligible: bool
    blockers: list[str]
    warnings: list[str]
    frequency_minutes: float | None
    horizons: list[HorizonOption]
    models: list[ModelOption]


class SplitSummary(BaseModel):
    training_rows: int
    evaluation_rows: int
    warmup_rows: int
    purged_origins: int
    first_training_origin: str
    last_training_target: str
    first_evaluation_origin: str
    first_evaluation_target: str
    last_evaluation_target: str
    timezone: str


class ModelMetric(BaseModel):
    model: ModelName
    label: str
    mae: float
    rmse: float
    bias: float
    skill: float | None


class BacktestPoint(BaseModel):
    timestamp: str
    origin: str
    actual_generation: float
    predictions: dict[str, float]


class ModelFailure(BaseModel):
    model: MLModel
    message: str


class BacktestResults(BaseModel):
    job_id: str
    dataset_id: str
    filename: str
    demo: bool
    horizon_minutes: int
    frequency_minutes: float
    train_fraction: float
    evaluation: Literal["rolling_origin_holdout"] = "rolling_origin_holdout"
    best_model: ModelName
    metrics: list[ModelMetric]
    failures: list[ModelFailure]
    split: SplitSummary
    points: list[BacktestPoint]
    total_predictions_per_model: int
    feature_names: list[str]
    notes: list[str]


class JobStatus(BaseModel):
    job_id: str
    dataset_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    stage: str
    error: str | None = None
    kind: Literal["backtest", "future"] = "backtest"


class FutureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_job_id: str
    model: ModelName
    horizon_minutes: Literal[15, 60, 360, 1440] = 1440


class FuturePoint(BaseModel):
    timestamp: str
    predicted_generation: float = Field(ge=0, allow_inf_nan=False)
    lead_minutes: float


class FutureForecast(BaseModel):
    forecast_id: str
    source_job_id: str
    dataset_id: str
    filename: str
    demo: bool
    kind: Literal["future_forecast"] = "future_forecast"
    model: ModelName
    model_label: str
    method: Literal["recursive_one_step", "persistence", "seasonal_naive"]
    fit_strategy: Literal["refit_all_observed_history", "observed_history_baseline"]
    training_rows: int
    backtest_horizon_minutes: int
    horizon_minutes: int
    frequency_minutes: float
    origin: str
    generated_at: str
    timezone: str
    context: list[Point]
    points: list[FuturePoint]
    notes: list[str]
