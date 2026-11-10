from typing import Literal
from pydantic import BaseModel


class DatasetInspection(BaseModel):
    dataset_id: str
    filename: str
    demo: bool
    columns: list[str]
    row_count: int
    preview: list[dict[str, str]]


class MappingRequest(BaseModel):
    dataset_id: str
    timestamp_column: str
    generation_column: str
    capacity_column: str | None = None


class Check(BaseModel):
    code: str
    label: str
    status: Literal["pass", "warning", "error"]
    detail: str
    count: int | None = None


class Point(BaseModel):
    timestamp: str
    generation: float | None


class ValidationReport(BaseModel):
    dataset_id: str
    filename: str
    demo: bool
    status: Literal["healthy", "attention", "invalid"]
    observations: int
    start: str | None
    end: str | None
    frequency_minutes: float | None
    timezone: str | None
    missing_generation: int
    checks: list[Check]
    points: list[Point]
    chart_total_points: int
    chart_note: str
