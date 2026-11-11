"""Read-only snapshot investigation; standard library only, no network or fitting.

python Shared/docs/research/summarize-missing-production.py Shared/data/elia/study-2024-2025
"""
import hashlib
import csv
import io
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def summarize(root, evidence=None):
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if not manifest.get('complete'):
        raise ValueError('Snapshot is incomplete')
    raw = {}
    for file in manifest['files']:
        path = (root / file['path']).resolve()
        if not path.is_relative_to((root / 'raw').resolve()):
            raise ValueError('Raw path escapes snapshot')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != file['sha256']:
            raise ValueError('Raw checksum mismatch: ' + file['path'])
        raw[file['path']] = json.loads(content)
    rows = []
    for chunk in manifest['chunks']:
        records = raw[chunk['path']]
        if len(records) != chunk['expected_records']:
            raise ValueError('Source count mismatch')
        if chunk['source'] != 'ods032':
            raise ValueError('This investigation is scoped to legacy ods032')
        for record in records:
            if record['region'] != 'Belgium':
                raise ValueError('Unexpected region')
            dt = datetime.fromisoformat(record['datetime'].replace('Z', '+00:00'))
            if dt.utcoffset() is None:
                raise ValueError('Naive source timestamp')
            rows.append((dt, record))
    rows.sort(key=lambda pair: pair[0])
    if len({t for t, _ in rows}) != len(rows):
        raise ValueError('Duplicate source timestamp')
    zone = ZoneInfo('Europe/Brussels')
    missing = [index for index, (_, r) in enumerate(rows) if r['measured'] is None]
    dates = {rows[i][0].astimezone(zone).date() for i in missing}
    neighbors = Counter()
    consecutive_neighbors = 0
    for i in missing:
        if i == 0 or i + 1 == len(rows):
            neighbors['outside_snapshot'] += 1
            continue
        before, current, after = rows[i-1:i+2]
        if current[0] - before[0] == after[0] - current[0] == timedelta(minutes=15):
            consecutive_neighbors += 1
        neighbors[f"{before[1]['measured']} / {after[1]['measured']}"] += 1
    first, last = (min(dates), max(dates)) if dates else (None, None)
    span = [] if first is None else [first + timedelta(days=i) for i in range((last-first).days+1)]
    result = {
        'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'source_records': len(rows),
        'raw_null_production': len(missing),
        'affected_local_dates': len(dates),
        'first_missing_local_date': str(first),
        'last_missing_local_date': str(last),
        'unaffected_dates_within_missing_span': [str(day) for day in span if day not in dates],
        'missing_by_local_year': dict(sorted(Counter(str(rows[i][0].astimezone(zone).year) for i in missing).items())),
        'missing_by_local_time': dict(sorted(Counter(rows[i][0].astimezone(zone).strftime('%H:%M') for i in missing).items())),
        'missing_by_utc_time': dict(sorted(Counter(rows[i][0].astimezone(ZoneInfo('UTC')).strftime('%H:%M') for i in missing).items())),
        'adjacent_measured_mw_before_after': dict(sorted(neighbors.items())),
        'missing_with_both_neighbors_15_minutes_apart': consecutive_neighbors,
        'missing_with_reference_and_capacity_populated': sum(all(rows[i][1].get(key) is not None for key in
            ('dayahead11hforecast', 'dayaheadforecast', 'monitoredcapacity')) for i in missing),
        'attribution': 'Elia Transmission Belgium SA, ods032, CC BY 4.0; filtered and summarized by SolarCast.',
    }
    if evidence is not None:
        result['live_recheck'] = verify_evidence(evidence, rows, missing)
    return result


def verify_evidence(evidence, rows, missing):
    manifest = json.loads((evidence / 'manifest.json').read_bytes())
    files = {}
    for file in manifest['files']:
        path = (evidence / file['path']).resolve()
        if not path.is_relative_to(evidence.resolve()):
            raise ValueError('Evidence path escapes directory')
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != file['sha256']:
            raise ValueError('Evidence checksum mismatch: ' + file['path'])
        files[file['path']] = body
    def stamp(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    nulls = json.loads(files['all-missing-records.json'])
    expected_nulls = {rows[i][0] for i in missing}
    if (len(nulls) != len(expected_nulls) or {stamp(r['datetime']) for r in nulls} != expected_nulls
            or any(r['region'] != 'Belgium' or r['measured'] is not None for r in nulls)):
        raise ValueError('Live missing set differs from saved study')
    source = dict(rows)
    national = json.loads(files['national-records.json'])
    records = national['results']
    if len(records) != national['total_count']:
        raise ValueError('National records response is incomplete')
    for record in records:
        original = source[stamp(record['datetime'])]
        if any(original[key] != value for key, value in record.items() if key != 'datetime'):
            raise ValueError('Live national sample differs from saved study')
    exported = list(csv.DictReader(io.StringIO(files['national-export.csv'].decode('utf-8-sig'))))
    if len(exported) != len(records):
        raise ValueError('CSV/JSON count differs')
    csv_map = {stamp(r['datetime']): r for r in exported}
    for record in records:
        other = csv_map[stamp(record['datetime'])]
        if other['region'] != record['region']:
            raise ValueError('CSV/JSON region differs')
        for key in ('measured', 'monitoredcapacity', 'dayahead11hforecast', 'dayaheadforecast'):
            value = None if other[key] == '' else float(other[key])
            if value != record[key]:
                raise ValueError('CSV/JSON values differ')
    regional = json.loads(files['regional-records.json'])
    if len(regional['results']) != regional['total_count']:
        raise ValueError('Regional response is incomplete')
    by_date = {}
    for record in regional['results']:
        day = str(stamp(record['datetime']).astimezone(ZoneInfo('Europe/Brussels')).date())
        counts = by_date.setdefault(day, {'records': 0, 'null_production': 0, 'zero_production': 0})
        counts['records'] += 1
        counts['null_production'] += record['measured'] is None
        counts['zero_production'] += record['measured'] == 0
    return {'batch_started_at_utc': manifest['checked_at_utc'],
            'missing_set_matches_original_snapshot': True,
            'national_records_match_original_snapshot': len(records),
            'csv_records_match_json': len(exported),
            'regional_2345_samples': by_date}


if __name__ == '__main__':
    evidence = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(summarize(Path(sys.argv[1]), evidence), indent=2, sort_keys=True))
