"""Reproducible Elia national solar downloads. Run: python -m app.elia --help."""
import argparse
import csv
import hashlib
import io
import json
import math
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = 'https://opendata.elia.be/api/explore/v2.1/catalog/datasets/'
ZONE = ZoneInfo('Europe/Brussels')
UTC = timezone.utc
STEP = timedelta(minutes=15)
SWITCH = datetime(2026, 10, 26, tzinfo=ZONE).astimezone(UTC)
ATTRIBUTION = ('Source: Elia Transmission Belgium SA, Elia Open Data, ods032/ods209; '
               'https://opendata.elia.be/pages/licence/ (CC BY 4.0). '
               'SolarCast selects national totals, converts timestamps to UTC and renames fields.')


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('Source timestamp has no UTC offset')
    return parsed.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def fetch(url, attempts=4, opener=urlopen, sleep=time.sleep):
    for attempt in range(attempts):
        try:
            request = Request(url, headers={'User-Agent': 'SolarCast-research/1.0'})
            with opener(request, timeout=45) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise
            sleep(min(2 ** attempt, 8))


def windows(start, end):
    """Half-open UTC ranges, at most 28 days, split at the schema boundary."""
    cursor = start
    while cursor < end:
        stop = min(end, cursor + timedelta(days=28))
        if cursor < SWITCH < stop:
            stop = SWITCH
        yield ('ods032' if cursor < SWITCH else 'ods209'), cursor, stop
        cursor = stop


def save_raw(root, name, content, url):
    path = root / 'raw' / name
    path.write_bytes(content)
    return {'path': f'raw/{name}', 'sha256': digest(content), 'url': url,
            'retrieved_at': iso(datetime.now(UTC))}


def download(start, end, root, getter=fetch):
    root.mkdir(parents=True, exist_ok=False)
    (root / 'raw').mkdir()
    manifest = {'version': 1, 'start': iso(start), 'end_exclusive': iso(end),
                'timezone': 'Europe/Brussels', 'attribution': ATTRIBUTION,
                'mappings': {'ods032': {'generation_mw': 'measured', 'region': 'Belgium'},
                             'ods209': {'generation_mw': 'measured_upscaled', 'region': 'Belgium',
                                        'gridconnectiontype': 'Total'}},
                'files': [], 'chunks': [], 'complete': False}
    # Checkpoint every response: an interrupted run never looks complete.
    def checkpoint():
        (root / 'manifest.json').write_bytes(encoded(manifest))
    checkpoint()
    for source in sorted({source for source, _, _ in windows(start, end)}):
        url = BASE + source
        body = getter(url)
        metadata = json.loads(body)
        fields = {field['name'] for field in metadata['fields']}
        required = {'datetime', 'region', 'monitoredcapacity', 'dayaheadforecast',
                    'dayahead11hforecast', 'measured' if source == 'ods032' else 'measured_upscaled'}
        if source == 'ods209':
            required.add('gridconnectiontype')
        if not required <= fields:
            raise ValueError(f'{source}: source schema changed; missing {sorted(required - fields)}')
        manifest['files'].append(save_raw(root, f'{source}-metadata.json', body, url))
        checkpoint()
    for index, (source, first, last) in enumerate(windows(start, end)):
        where = f"region='Belgium' AND datetime >= date'{iso(first)}' AND datetime < date'{iso(last)}'"
        if source == 'ods209':
            where += " AND gridconnectiontype='Total'"
        count_url = BASE + source + '/records?' + urlencode({'where': where, 'limit': 1})
        count_body = getter(count_url)
        expected = json.loads(count_body)['total_count']
        manifest['files'].append(save_raw(root, f'{index:03}-count.json', count_body, count_url))
        checkpoint()
        # Bounded exports avoid offset ceilings and page drift. Check the full
        # exported count against a separate records query; fail on truncation.
        url = BASE + source + '/exports/json?' + urlencode(
            {'where': where, 'order_by': 'datetime', 'limit': -1, 'timezone': 'UTC'})
        body = getter(url)
        file = save_raw(root, f'{index:03}-{source}.json', body, url)
        manifest['files'].append(file)
        checkpoint()
        records = json.loads(body)
        if not isinstance(records, list) or len(records) != expected:
            raise ValueError(f'{source}: export count differs from records query; retry in a new run')
        manifest['chunks'].append({'source': source, 'start': iso(first), 'end_exclusive': iso(last),
                                   'path': file['path'], 'count_path': f'raw/{index:03}-count.json',
                                   'expected_records': expected})
        checkpoint()
    manifest['complete'] = True
    checkpoint()
    return rebuild(root)


def number(value):
    if value is None:
        return None, 'missing'
    if isinstance(value, bool):
        return None, 'invalid'
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None, 'invalid'
    return (result, None) if math.isfinite(result) else (None, 'invalid')


def csv_bytes(headers, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode()


def rebuild(root):
    manifest = json.loads((root / 'manifest.json').read_bytes())
    if manifest.get('version') != 1 or not manifest.get('complete'):
        raise ValueError('Incomplete or unsupported snapshot manifest')
    verified = {}
    for entry in manifest['files']:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to((root / 'raw').resolve()):
            raise ValueError('Raw path escapes snapshot directory')
        body = path.read_bytes()
        if digest(body) != entry['sha256']:
            raise ValueError(f"Checksum mismatch: {entry['path']}")
        verified[entry['path']] = json.loads(body)
    start, end = stamp(manifest['start']), stamp(manifest['end_exclusive'])
    expected_windows = [(s, iso(a), iso(b)) for s, a, b in windows(start, end)]
    actual_windows = [(c['source'], c['start'], c['end_exclusive']) for c in manifest['chunks']]
    if actual_windows != expected_windows:
        raise ValueError('Manifest chunks do not cover requested range exactly')
    counts = dict.fromkeys(['missing_generation', 'invalid_generation', 'negative_generation',
                           'missing_capacity', 'invalid_capacity', 'missing_reference',
                           'invalid_reference', 'unexpected_records', 'duplicate_timestamps',
                           'off_grid_timestamps'], 0)
    rows, references, seen = [], [], set()
    source_counts = {}
    for chunk in manifest['chunks']:
        records = verified[chunk['path']]
        if len(records) != chunk['expected_records'] or len(records) != verified[chunk['count_path']]['total_count']:
            raise ValueError('Snapshot record count mismatch')
        source = chunk['source']
        first, last = stamp(chunk['start']), stamp(chunk['end_exclusive'])
        source_counts[source] = source_counts.get(source, 0) + len(records)
        for record in records:
            if (record.get('region') != 'Belgium' or
                    (source == 'ods209' and record.get('gridconnectiontype') != 'Total')):
                counts['unexpected_records'] += 1
                continue
            try:
                ts = stamp(record['datetime'])
            except (ValueError, TypeError, KeyError):
                counts['unexpected_records'] += 1
                continue
            if not first <= ts < last:
                counts['unexpected_records'] += 1
                continue
            if (ts - start) % STEP:
                counts['off_grid_timestamps'] += 1
            if ts in seen:
                counts['duplicate_timestamps'] += 1
            seen.add(ts)
            key = 'measured' if source == 'ods032' else 'measured_upscaled'
            production, issue = number(record.get(key))
            if issue:
                counts[f'{issue}_generation'] += 1
            elif production < 0:
                counts['negative_generation'] += 1
            capacity, issue = number(record.get('monitoredcapacity'))
            if issue:
                counts[f'{issue}_capacity'] += 1
            elif capacity <= 0:
                counts['invalid_capacity'] += 1
            forecast = []
            for key in ('dayahead11hforecast', 'dayaheadforecast'):
                value, issue = number(record.get(key))
                if issue:
                    counts[f'{issue}_reference'] += 1
                forecast.append(value)
            label = iso(ts)
            rows.append((label, production, capacity, source))
            references.append((label, *forecast, source))
    rows.sort(key=lambda row: row[0])
    references.sort(key=lambda row: row[0])
    expected = int((end - start) / STEP)
    missing = [iso(start + index * STEP) for index in range(expected) if start + index * STEP not in seen]
    missing_production = [row[0] for row in rows if row[1] is None]
    missing_local = [stamp(value).astimezone(ZONE) for value in missing_production]
    counts['missing_intervals'] = len(missing)
    fatal = ('missing_generation', 'invalid_generation', 'negative_generation', 'unexpected_records',
             'duplicate_timestamps', 'off_grid_timestamps', 'missing_intervals')
    ready = bool(rows) and not any(counts[key] for key in fatal)
    report = {'start': iso(start), 'end_exclusive': iso(end), 'expected_intervals': expected,
              'records': len(rows), 'source_records': source_counts, 'checks': counts,
              'missing_interval_examples': missing[:20], 'series_ready': ready,
              'missing_generation_examples': missing_production[:20],
              'missing_generation_by_local_year': dict(sorted(Counter(str(t.year) for t in missing_local).items())),
              'missing_generation_by_local_time': dict(sorted(Counter(t.strftime('%H:%M') for t in missing_local).items())),
              'platform_upload_ready': ready and len(rows) <= 100_000,
              'source_transition': len(source_counts) > 1,
              'availability': 'Retrospective revised data; publication lag is not established.',
              'attribution': ATTRIBUTION}
    products = {
        'normalized.csv': csv_bytes(['timestamp', 'generation_mw', 'monitored_capacity_mw', 'source_dataset'], rows),
        'platform.csv': csv_bytes(['timestamp', 'generation_mw'], [row[:2] for row in rows]),
        'benchmark-reference.csv': csv_bytes(['timestamp', 'elia_dayahead_11h_mw', 'elia_dayahead_18h_mw', 'source_dataset'], references),
        'quality.json': encoded(report),
        'ATTRIBUTION.txt': (ATTRIBUTION + '\n').encode(),
    }
    for name, body in products.items():
        (root / name).write_bytes(body)
    (root / 'artifacts.json').write_bytes(encoded({name: digest(body) for name, body in products.items()}))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('download')
    get.add_argument('--start', required=True, type=date.fromisoformat, help='Inclusive Brussels local date')
    get.add_argument('--end', required=True, type=date.fromisoformat, help='Exclusive Brussels local date')
    get.add_argument('--output', required=True, type=Path, help='New directory; never overwrite a run')
    build = commands.add_parser('rebuild')
    build.add_argument('--input', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'download':
            if args.start >= args.end:
                raise ValueError('--end must be later than --start')
            if (args.end - args.start).days > 1096:
                raise ValueError('Use at most three years per run')
            first = datetime.combine(args.start, datetime.min.time(), ZONE).astimezone(UTC)
            last = datetime.combine(args.end, datetime.min.time(), ZONE).astimezone(UTC)
            report = download(first, last, args.output)
        else:
            report = rebuild(args.input)
        print(json.dumps(report, indent=2))
        return 0 if report['series_ready'] else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'Elia import failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
