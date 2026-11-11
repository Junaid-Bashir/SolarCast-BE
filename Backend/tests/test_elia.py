import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from app import elia
from app.schemas import MappingRequest
from app.services import inspect_csv, validate_dataset, store


def row(ts, source='ods032', **changes):
    result = {'datetime': elia.iso(ts), 'region': 'Belgium', 'gridconnectiontype': 'Total',
              'measured': 0, 'measured_upscaled': 0, 'monitoredcapacity': 100,
              'dayaheadforecast': 1, 'dayahead11hforecast': 2}
    result.update(changes)
    return result


def snapshot(tmp_path, first, last, transform=lambda records: records):
    def getter(url):
        from urllib.parse import parse_qs, urlsplit
        import re
        source = 'ods209' if 'ods209' in url else 'ods032'
        if not ('/records?' in url or '/exports/' in url):
            return elia.encoded({'fields': [{'name': key} for key in row(first)]})
        where = parse_qs(urlsplit(url).query)['where'][0]
        dates = re.findall("date'([^']+)'", where)
        a, b = map(elia.stamp, dates)
        records = transform([row(a + i * elia.STEP, source)
                             for i in range(int((b - a) / elia.STEP))])
        return elia.encoded({'total_count': len(records)} if '/records?' in url else records)
    path = tmp_path / 'run'
    report = elia.download(first, last, path, getter)
    return path, report


def test_offline_rebuild_integrity_and_upload(tmp_path):
    first = elia.stamp('2025-06-01T00:00:00Z')
    path, report = snapshot(tmp_path, first, first + timedelta(days=1))
    assert report['series_ready']
    assert report['records'] == 96
    original = {p.name: p.read_bytes() for p in path.glob('*') if p.is_file()}
    assert elia.rebuild(path) == report
    assert original == {p.name: p.read_bytes() for p in path.glob('*') if p.is_file()}
    inspection = inspect_csv((path / 'platform.csv').read_bytes(), 'elia.csv')
    try:
        validation = validate_dataset(MappingRequest(dataset_id=inspection.dataset_id,
                                      timestamp_column='timestamp', generation_column='generation_mw'))
        assert validation.status == 'healthy'
        assert validation.frequency_minutes == 15
    finally:
        store.delete(inspection.dataset_id)
    raw = next((path / 'raw').glob('*ods032.json'))
    raw.write_bytes(raw.read_bytes() + b' ')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        elia.rebuild(path)


@pytest.mark.parametrize('day,expected', [('2025-03-30', 92), ('2025-10-26', 100)])
def test_local_dst_day(tmp_path, day, expected):
    from datetime import date, time
    date_value = date.fromisoformat(day)
    first = datetime.combine(date_value, time(), elia.ZONE).astimezone(timezone.utc)
    last = datetime.combine(date_value + timedelta(days=1), time(), elia.ZONE).astimezone(timezone.utc)
    _, report = snapshot(tmp_path, first, last)
    assert report['expected_intervals'] == expected
    assert report['series_ready']


def test_offset_conversion():
    assert elia.iso(elia.stamp('2025-10-26T02:15:00+02:00')) == '2025-10-26T00:15:00Z'
    assert elia.iso(elia.stamp('2025-10-26T02:15:00+01:00')) == '2025-10-26T01:15:00Z'
    with pytest.raises(ValueError, match='no UTC offset'):
        elia.stamp('2025-01-01T00:00:00')


def test_transition_and_new_mapping(tmp_path):
    def transform(records):
        for r in records:
            r['measured'] = 999
            r['measured_upscaled'] = 12
        return records
    path, report = snapshot(tmp_path, elia.SWITCH - elia.STEP, elia.SWITCH + elia.STEP, transform)
    assert report['source_transition']
    assert report['source_records'] == {'ods032': 1, 'ods209': 1}
    lines = (path / 'platform.csv').read_text().splitlines()
    assert lines[1].endswith(',999.0')
    assert lines[2].endswith(',12.0')


@pytest.mark.parametrize('change,key', [
    ({'measured': None}, 'missing_generation'),
    ({'measured': 'nan'}, 'invalid_generation'),
    ({'measured': -1}, 'negative_generation'),
    ({'region': 'Namur'}, 'unexpected_records'),
    ({'datetime': '2025-01-01T00:01:00Z'}, 'off_grid_timestamps'),
])
def test_bad_records_remain_visible(tmp_path, change, key):
    first = elia.stamp('2025-01-01T00:00:00Z')
    def transform(records):
        records[0].update(change)
        return records
    _, report = snapshot(tmp_path, first, first + 2 * elia.STEP, transform)
    assert report['checks'][key] == 1
    assert not report['series_ready']
    if key == 'missing_generation':
        assert report['missing_generation_examples'] == [elia.iso(first)]
        assert report['missing_generation_by_local_year'] == {'2025': 1}
        assert report['missing_generation_by_local_time'] == {'01:00': 1}


def test_duplicates_and_gap_not_repaired(tmp_path):
    first = elia.stamp('2025-01-01T00:00:00Z')
    path, report = snapshot(tmp_path, first, first + 3 * elia.STEP,
                            lambda records: [records[0], records[0], records[2]])
    assert report['checks']['duplicate_timestamps'] == 1
    assert report['checks']['missing_intervals'] == 1
    assert not report['series_ready']
    assert len((path / 'platform.csv').read_text().splitlines()) == 4


def test_new_schema_non_total_rejected(tmp_path):
    first = elia.SWITCH
    def transform(records):
        records[0]['gridconnectiontype'] = 'DSO'
        return records
    _, report = snapshot(tmp_path, first, first + elia.STEP, transform)
    assert report['checks']['unexpected_records'] == 1
    assert not report['series_ready']


def test_retry_bound_and_nonretryable():
    calls, delays = [], []
    def unavailable(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 503, 'Unavailable', {}, None)
    with pytest.raises(HTTPError):
        elia.fetch('https://example.test', opener=unavailable, sleep=delays.append)
    assert len(calls) == 4
    assert delays == [1, 2, 4]
    calls.clear()
    def forbidden(request, timeout):
        calls.append(1)
        raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
    with pytest.raises(HTTPError):
        elia.fetch('https://example.test', opener=forbidden)
    assert len(calls) == 1


def test_truncated_export_is_incomplete(tmp_path):
    def getter(url):
        if '/records?' in url:
            return elia.encoded({'total_count': 2})
        if '/exports/' in url:
            return b'[]'
        return elia.encoded({'fields': [{'name': key} for key in row(elia.SWITCH)]})
    path = tmp_path / 'run'
    with pytest.raises(ValueError, match='export count'):
        elia.download(elia.SWITCH, elia.SWITCH + elia.STEP, path, getter)
    with pytest.raises(ValueError, match='Incomplete'):
        elia.rebuild(path)
    with pytest.raises(FileExistsError):
        elia.download(elia.SWITCH, elia.SWITCH + elia.STEP, path, getter)


def test_windows_bounded_and_cover_range():
    start = elia.SWITCH - timedelta(days=35)
    end = elia.SWITCH + timedelta(days=35)
    chunks = list(elia.windows(start, end))
    assert chunks[0][1] == start and chunks[-1][2] == end
    assert all(b - a <= timedelta(days=28) for _, a, b in chunks)
    assert all(chunks[i][2] == chunks[i+1][1] for i in range(len(chunks)-1))


def test_missing_reference_and_capacity_do_not_invalidate_target(tmp_path):
    first = elia.SWITCH
    _, report = snapshot(tmp_path, first, first + elia.STEP,
                         lambda records: [dict(records[0], monitoredcapacity=None, dayaheadforecast=None)])
    assert report['checks']['missing_capacity'] == 1
    assert report['checks']['missing_reference'] == 1
    assert report['series_ready']
