import io
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services import store, TTL_SECONDS

client = TestClient(app)


@pytest.fixture(autouse=True)
def empty_store():
    store.items.clear()
    yield
    store.items.clear()


def upload(text, filename="site.csv"):
    return client.post("/api/datasets/upload", files={"file": (filename, text.encode(), "text/csv")})


def validate(text, **kwargs):
    response = upload(text)
    assert response.status_code == 200, response.text
    return client.post("/api/datasets/validate", json={"dataset_id": response.json()["dataset_id"], "timestamp_column": "when", "generation_column": "power", **kwargs})


def checks(response):
    assert response.status_code == 200, response.text
    return {c["code"]: c for c in response.json()["checks"]}


@pytest.mark.parametrize("text,filename", [("", "data.csv"), ("when,power", "data.csv"), ("a,a\n1,2", "data.csv"), ("a,b\n1,2,3", "data.csv"), ("a,b\n1,2", "data.txt"), ("a,\n1,2", "data.csv"), ('a,b\n"unclosed,1', "data.csv")])
def test_invalid_csv(text, filename):
    assert upload(text, filename).status_code == 400


def test_custom_mapping_and_original_preview():
    response = upload("Measured at,PV output,Capacity\n2026-01-01T10:00:00Z,2.5,10\n2026-01-01T10:15:00Z,4,10")
    data = response.json()
    assert data["columns"] == ["Measured at", "PV output", "Capacity"]
    result = client.post("/api/datasets/validate", json={"dataset_id": data["dataset_id"], "timestamp_column": "Measured at", "generation_column": "PV output", "capacity_column": "Capacity"})
    assert result.status_code == 200
    assert result.json()["frequency_minutes"] == 15
    assert result.json()["points"][0]["generation"] == 2.5
    assert client.get(f'/api/datasets/{data["dataset_id"]}').json()["preview"] == data["preview"]


def test_frequency_and_missing_intervals():
    result = validate("when,power\n2026-01-01T10:00,1\n2026-01-01T10:15,2\n2026-01-01T10:45,3\n2026-01-01T11:00,4")
    assert result.json()["frequency_minutes"] == 15
    assert checks(result)["missing_intervals"]["count"] == 1


def test_issues_are_reported_without_repair():
    result = validate("when,power\n2026-01-01T10:30,-2\n2026-01-01T10:00,\n2026-01-01T10:00,nope\nwrong,inf")
    c = checks(result)
    for code in ["ordering", "duplicates", "timestamps", "missing_generation", "negative_generation"]:
        assert c[code]["count"] == 1
    assert c["numeric_generation"]["count"] == 2
    assert result.json()["status"] == "invalid"
    assert result.json()["points"][0]["generation"] == -2
    assert result.json()["points"][1]["generation"] is None


def test_naive_times_remain_naive():
    result = validate("when,power\n2026-01-01T10:00,1\n2026-01-01T10:15,2")
    assert result.json()["timezone"] is None
    assert result.json()["points"][0]["timestamp"] == "2026-01-01T10:00:00"
    assert checks(result)["timezone"]["status"] == "warning"


def test_mixed_timezone_is_error():
    result = validate("when,power\n2026-01-01T10:00Z,1\n2026-01-01T10:15,2")
    assert checks(result)["mixed_timezone"]["status"] == "error"
    assert result.json()["points"] == []


def test_offsets_normalized_for_duplicates():
    result = validate("when,power\n2026-01-01T10:00+01:00,1\n2026-01-01T09:00Z,2")
    assert checks(result)["duplicates"]["count"] == 1


def test_ambiguous_dates_rejected():
    assert checks(validate("when,power\n01/02/2026 10:00,1"))["timestamps"]["status"] == "error"


def test_irregular_frequency():
    assert validate("when,power\n2026-01-01T10:00,1\n2026-01-01T10:15,2\n2026-01-01T10:35,3").json()["frequency_minutes"] is None


@pytest.mark.parametrize("mapping", [{"timestamp_column":"absent"}, {"generation_column":"when"}, {"capacity_column":"power"}])
def test_mapping_errors(mapping):
    assert validate("when,power\n2026-01-01T10:00,1", **mapping).status_code == 422


def test_capacity_validation():
    result = validate("when,power,cap\n2026-01-01T10:00,1,-10", capacity_column="cap")
    assert checks(result)["capacity"]["status"] == "error"


def test_sample_delete_and_expiration():
    response = client.post("/api/datasets/sample")
    assert response.status_code == 200
    data = response.json()
    assert data["demo"] and data["row_count"] == 1344
    assert client.get("/api/datasets/sample/download").status_code == 200
    store.items[data["dataset_id"]].created = time.monotonic() - TTL_SECONDS - 1
    assert client.get(f'/api/datasets/{data["dataset_id"]}').status_code == 404
    data = client.post("/api/datasets/sample").json()
    assert client.delete(f'/api/datasets/{data["dataset_id"]}').status_code == 204
    assert client.get(f'/api/datasets/{data["dataset_id"]}').status_code == 404


def test_upload_size_limit():
    assert client.post("/api/datasets/upload", files={"file": ("big.csv", io.BytesIO(b"a" * (10*1024*1024+1)))}).status_code == 413


def test_unknown_dataset():
    assert client.post("/api/datasets/validate", json={"dataset_id":"unknown", "timestamp_column":"x", "generation_column":"y"}).status_code == 404


@pytest.mark.parametrize("value", ["9999-01-01T10:00:00", "not-a-date", ""])
def test_invalid_timestamp_range_is_reported(value):
    result = validate(f"when,power\n{value},2")
    assert checks(result)["timestamps"]["count"] == 1
    assert result.json()["start"] is None


@pytest.mark.parametrize("minutes", [1, 15, 60])
def test_supported_inspection_cadences(minutes):
    from datetime import datetime, timedelta
    start = datetime(2026,1,1)
    rows = [f"{(start + timedelta(minutes=i*minutes)).isoformat()},{i}" for i in range(4)]
    assert validate("when,power\n" + "\n".join(rows)).json()["frequency_minutes"] == minutes
