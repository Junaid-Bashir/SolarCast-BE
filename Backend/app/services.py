"""Bounded, ephemeral local storage. Uploaded values are never repaired."""
import csv
import io
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import pandas as pd
from fastapi import HTTPException

from .schemas import Check, DatasetInspection, MappingRequest, Point, ValidationReport

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
TTL_SECONDS = 3600
MAX_DATASETS = 20


@dataclass
class Dataset:
    filename: str
    demo: bool
    frame: pd.DataFrame
    created: float


class DatasetStore:
    def __init__(self):
        self.items: dict[str, Dataset] = {}
        self.lock = threading.Lock()

    def purge(self):
        now = time.monotonic()
        for key in list(self.items):
            if now - self.items[key].created > TTL_SECONDS:
                del self.items[key]

    def put(self, dataset: Dataset) -> str:
        with self.lock:
            self.purge()
            if len(self.items) >= MAX_DATASETS:
                raise HTTPException(429, "Local dataset storage is full. Remove a dataset or restart the backend.")
            key = str(uuid4())
            self.items[key] = dataset
            return key

    def get(self, key: str) -> Dataset:
        with self.lock:
            self.purge()
            if key not in self.items:
                raise HTTPException(404, "Dataset expired or was removed. Please upload it again.")
            return self.items[key]

    def delete(self, key: str):
        with self.lock:
            self.items.pop(key, None)


store = DatasetStore()


def inspect_csv(content: bytes, filename: str, demo: bool = False) -> DatasetInspection:
    if not filename.lower().endswith(".csv"):
        raise HTTPException(400, "Choose a CSV file with a .csv extension.")
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "The CSV exceeds the 10 MB local upload limit.")
    try:
        decoded = content.decode("utf-8-sig")
        if "\x00" in decoded:
            raise ValueError("Binary data")
        reader = csv.reader(io.StringIO(decoded), strict=True)
        columns = next(reader)
        if len(columns) < 2 or any(not c.strip() for c in columns) or len(set(columns)) != len(columns):
            raise ValueError("Use at least two non-empty, unique column headers")
        if len(columns) > 100:
            raise ValueError("Use at most 100 columns")
        rows = []
        for row in reader:
            if len(row) != len(columns):
                raise ValueError("Every row must have the same number of fields as the header")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValueError("Use at most 100,000 rows")
        if not rows:
            raise ValueError("The CSV has no observations")
    except (UnicodeError, csv.Error, ValueError, StopIteration) as exc:
        raise HTTPException(400, f"Unable to read CSV. {str(exc) or 'The file is empty.'} Use UTF-8 text and comma-separated columns.") from None
    frame = pd.DataFrame(rows, columns=columns)
    dataset_id = store.put(Dataset(filename, demo, frame, time.monotonic()))
    return DatasetInspection(dataset_id=dataset_id, filename=filename, demo=demo, columns=columns,
                             row_count=len(frame), preview=frame.head(6).to_dict(orient="records"))


def validate_dataset(request: MappingRequest) -> ValidationReport:
    dataset = store.get(request.dataset_id)
    frame = dataset.frame
    mapped = [request.timestamp_column, request.generation_column]
    if request.capacity_column:
        mapped.append(request.capacity_column)
    if any(col not in frame.columns for col in mapped):
        raise HTTPException(422, "A mapped column is missing. Choose columns from this dataset.")
    if len(set(mapped)) != len(mapped):
        raise HTTPException(422, "Each mapped field must use a different column.")
    checks: list[Check] = []

    def add(code, label, count, problem, good, severity="error"):
        checks.append(Check(code=code, label=label, count=int(count), status=severity if count else "pass",
                            detail=problem if count else good))

    # Parse each value without imposing a timezone or guessing ambiguous day/month dates.
    parsed = []
    zones = set()
    for raw in frame[request.timestamp_column]:
        try:
            value = raw.strip()
            # Restrict to unambiguous ISO 8601. Other formats need an explicit future format setting.
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            timestamp = pd.Timestamp(stamp)
            # Ensure values fit the nanosecond range used by the analysis index.
            _ = timestamp.value
            zones.add("offset-aware" if stamp.utcoffset() is not None else "naive")
            parsed.append(timestamp)
        except (ValueError, TypeError, OverflowError):
            parsed.append(pd.NaT)
    invalid_times = sum(pd.isna(t) for t in parsed)
    add("timestamps", "Timestamp parsing", invalid_times, f"{invalid_times:,} timestamps cannot be parsed. Use ISO 8601 (YYYY-MM-DD HH:MM).", "All timestamps parse successfully.")
    mixed = len(zones) > 1
    add("mixed_timezone", "Consistent timezone", mixed, "Timezone-aware and timezone-naive timestamps are mixed. Supply a consistent timezone.", "Timestamp timezone representation is consistent.")
    aware = zones == {"offset-aware"}
    timezone = "Explicit UTC offsets (analyzed in UTC)" if aware else None
    add("timezone", "Timezone information", not aware, "Timezone is not specified. No timezone has been assumed.", "Explicit UTC offsets detected.", "warning")
    times = pd.DatetimeIndex(pd.to_datetime(parsed, utc=True)) if not mixed else pd.DatetimeIndex([])
    valid = times.dropna()
    duplicates = int(valid.duplicated().sum())
    add("duplicates", "Duplicate timestamps", duplicates, f"{duplicates:,} duplicate timestamps. Resolve them before training.", "No duplicate timestamps.")
    unordered = len(valid) > 1 and not valid.is_monotonic_increasing
    add("ordering", "Chronological order", unordered, "Rows are out of chronological order. The uploaded data has not been sorted.", "Rows are in chronological order.")
    # Sorted distinct times are used ONLY to inspect cadence, never to replace source rows.
    unique = valid.unique().sort_values()
    diffs = pd.Series(unique[1:] - unique[:-1]).dt.total_seconds()
    frequency = None
    missing = 0
    irregular = False
    if not diffs.empty:
        cadence = float(diffs.mode().min())
        multiples = diffs / cadence
        irregular = bool(((multiples - multiples.round()).abs() > 1e-6).any())
        if cadence >= 60 and not irregular:
            frequency = cadence / 60
            missing = int((multiples.round() - 1).clip(lower=0).sum())
    add("frequency", "Sampling frequency", frequency is None, "A regular cadence of at least one minute could not be established.", f"{frequency:g}-minute inferred cadence." if frequency else "", "warning")
    add("missing_intervals", "Missing timestamps", missing if frequency else 1, f"{missing:,} missing intervals at the inferred cadence. No rows were inserted." if frequency else "Missing intervals cannot be determined without a regular cadence.", "No missing intervals detected.", "warning")
    target = frame[request.generation_column].str.strip()
    numbers = pd.to_numeric(target, errors="coerce")
    missing_target = int(target.eq("").sum())
    invalid_numbers = int((numbers.isna() & target.ne("")).sum() + numbers.isin([float("inf"), float("-inf")]).sum())
    negative = int(numbers.lt(0).sum())
    add("missing_generation", "Missing generation", missing_target, f"{missing_target:,} generation values are missing. No values were filled.", "No missing generation values.")
    add("numeric_generation", "Numeric generation", invalid_numbers, f"{invalid_numbers:,} generation values are non-numeric or non-finite.", "All supplied generation values are finite numbers.")
    add("negative_generation", "Non-negative generation", negative, f"{negative:,} negative generation values. Confirm the measurement convention.", "No negative generation values.", "warning")
    add("history", "Dataset size", len(frame) < 96, "Fewer than 96 observations. More history will be needed for meaningful model evaluation.", f"{len(frame):,} observations available; training eligibility is checked separately for each horizon and split.", "warning")
    if request.capacity_column:
        capacity = pd.to_numeric(frame[request.capacity_column], errors="coerce")
        bad = int((capacity.isna() | capacity.le(0) | capacity.isin([float("inf"), float("-inf")])).sum())
        add("capacity", "Installed capacity", bad, f"{bad:,} capacity values are missing, non-numeric, non-finite or not positive.", "Capacity values are positive finite numbers.")
    points = []
    if not mixed:
        for stamp, number in zip(times, numbers):
            if not pd.isna(stamp):
                label = stamp.isoformat() if aware else stamp.tz_localize(None).isoformat()
                points.append(Point(timestamp=label, generation=float(number) if pd.notna(number) and math.isfinite(number) else None))
    total = len(points)
    # Contiguous preview preserves gaps and source ordering, unlike stride downsampling.
    points = points[:2000]
    status = "invalid" if any(c.status == "error" for c in checks) else "attention" if any(c.status == "warning" for c in checks) else "healthy"
    def display(stamp):
        return stamp.isoformat() if aware else stamp.tz_localize(None).isoformat()
    return ValidationReport(dataset_id=request.dataset_id, filename=dataset.filename, demo=dataset.demo,
        status=status, observations=len(frame), start=display(valid.min()) if len(valid) else None,
        end=display(valid.max()) if len(valid) else None, frequency_minutes=frequency, timezone=timezone,
        missing_generation=missing_target, checks=checks, points=points, chart_total_points=total,
        chart_note=f"First {min(total, 2000):,} of {total:,} parseable rows, in source order. Gaps are not filled. Values use the source unit.")
