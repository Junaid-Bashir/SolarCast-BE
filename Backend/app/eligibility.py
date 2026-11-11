"""Observed-only eligibility audit, not a forecast or a data repair.

python -m app.eligibility --help
"""
import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .dayahead_schedule import STEP, delay_delta, utc, local_midnight, targets_for_day, validate_delay, window_for_day
CONTEXT = 97  # origin, lag at origin-96, and the intervening rolling observations


@dataclass
class Observations:
    times: pd.DatetimeIndex
    valid: np.ndarray
    input_rows: int
    absent_intervals: int
    missing_values: int
    invalid_values: int
    sha256: str

    def lookup(self, stamps):
        indices = self.times.get_indexer(stamps)
        in_range = indices >= 0
        valid = np.zeros(len(stamps), dtype=bool)
        valid[in_range] = self.valid[indices[in_range]]
        return valid, in_range, indices

    def context(self, stamps):
        _, origin_in_range, indices = self.lookup(stamps)
        extent = origin_in_range & (indices >= CONTEXT - 1)
        valid = np.zeros(len(stamps), dtype=bool)
        invalid_prefix = np.concatenate(([0], np.cumsum(~self.valid)))
        positions = indices[extent]
        valid[extent] = (invalid_prefix[positions + 1] - invalid_prefix[positions + 1 - CONTEXT]) == 0
        return valid, extent


def load_observations(path):
    body = path.read_bytes()
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not {'timestamp', 'generation_mw'} <= set(frame.columns) or frame.empty:
        raise ValueError('Require a nonempty CSV with timestamp and generation_mw')
    # Reject naive or malformed clocks instead of having pandas assume UTC.
    parsed = [utc(datetime.fromisoformat(value.replace('Z', '+00:00'))) for value in frame.timestamp]
    times = pd.DatetimeIndex(parsed)
    if times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError('Timestamps must be unique and increasing; nothing is sorted or deduplicated')
    if np.any(times.asi8 % STEP.value):
        raise ValueError('Timestamps must be aligned to quarter-hour UTC boundaries')
    if times[-1] - times[0] > pd.Timedelta(1097 * 86_400_000_000_000, unit='ns'):
        raise ValueError('Audit at most three years per input')
    values = pd.to_numeric(frame.generation_mw, errors='coerce').to_numpy(dtype=float)
    valid = np.isfinite(values) & (values >= 0) & (values <= 1e12)
    missing = frame.generation_mw.str.strip().eq('').to_numpy()
    # Internal membership grid only. Missing timestamps get FALSE validity;
    # no generation values are inserted, inferred, or exported.
    grid = pd.date_range(times[0], times[-1], freq=STEP)
    validity = np.zeros(len(grid), dtype=bool)
    validity[grid.get_indexer(times)] = valid
    return Observations(grid, validity, len(frame), len(grid)-len(frame),
                        int(missing.sum()), int((~valid & ~missing).sum()),
                        hashlib.sha256(body).hexdigest())


def training_examples(observations, start, end, fit_cutoff, delay_minutes):
    """One-step ML labels within local [start,end), known by fit_cutoff.

    Context is evaluated at observation origin, not at its interval-start
    publication time. A label's end+delay is later than every feature's end+delay.
    """
    validate_range(start, end, delay_minutes)
    fit_cutoff = utc(fit_cutoff)
    targets = pd.date_range(local_midnight(start), local_midnight(end), freq=STEP, inclusive='left')
    origins = targets - STEP
    history, history_extent = observations.context(origins)
    labels, target_extent, _ = observations.lookup(targets)
    available = targets + STEP + delay_delta(delay_minutes) <= fit_cutoff
    eligible = history & labels & available
    reasons = []
    for i in range(len(targets)):
        excluded = []
        if not history_extent[i]:
            excluded.append('insufficient_history_extent')
        elif not history[i]:
            excluded.append('invalid_or_missing_history')
        if not target_extent[i]:
            excluded.append('target_outside_input')
        elif not labels[i]:
            excluded.append('invalid_or_missing_target')
        if not available[i]:
            excluded.append('label_unavailable_by_fit_cutoff')
        reasons.append('|'.join(excluded))
    return pd.DataFrame({'origin': origins, 'target': targets,
                         'target_available_at_assumed': targets + STEP + delay_delta(delay_minutes),
                         'complete_history': history, 'valid_target': labels,
                         'label_known_by_fit_cutoff': available, 'eligible': eligible, 'exclusion_reasons': reasons})


def validate_range(start, end, delay_minutes):
    if start >= end or (end - start).days > 1096:
        raise ValueError('Require an increasing local date range of at most three years')
    validate_delay(delay_minutes)


def forecast_windows(observations, start, end, delay_minutes):
    """Separate issue-time inputs from future reference-label completeness."""
    validate_range(start, end, delay_minutes)
    rows = []
    for offset in range((end-start).days):
        day = start + timedelta(days=offset)
        window = window_for_day(day, delay_minutes)
        issue, origin = window.issue_at, window.observation_origin
        index = pd.DatetimeIndex([origin])
        persistence, origin_extent, _ = observations.lookup(index)
        history, history_extent = observations.context(index)
        targets = window.targets
        labels, _, _ = observations.lookup(targets)
        complete = bool(labels.all())
        reasons = []
        if not origin_extent[0]:
            reasons.append('origin_outside_input')
        elif not persistence[0]:
            reasons.append('invalid_or_missing_origin')
        if not history_extent[0]:
            reasons.append('insufficient_history_extent')
        elif not history[0]:
            reasons.append('invalid_or_missing_history')
        if not complete:
            reasons.append('incomplete_delivery_targets')
        rows.append({'delivery_date': day.isoformat(), 'issue_at': issue, 'latest_observation_origin': origin,
                     'expected_targets': len(targets), 'valid_targets': int(labels.sum()),
                     'complete_delivery_targets': complete,
                     'persistence_issuable': bool(persistence[0]),
                     'history_ml_issuable': bool(history[0]),
                     'persistence_scoreable_window': bool(persistence[0]) and complete,
                     'history_ml_scoreable_window': bool(history[0]) and complete,
                     'common_scoreable_window': bool(persistence[0] and history[0]) and complete,
                     'exclusion_reasons': '|'.join(reasons)})
    return pd.DataFrame(rows)


def summarize(training, windows, observations, fit_cutoff, delay_minutes, start, end):
    reason_counts = {}
    for reason in training.exclusion_reasons:
        for item in reason.split('|'):
            if item:
                reason_counts[item] = reason_counts.get(item, 0) + 1
    periods = [('training_2024', '2024-01-01', '2025-01-01'),
               ('validation_2025', '2025-01-01', '2025-10-01'),
               ('reserved_test_2025', '2025-10-01', '2026-01-01')]
    def window_summary(frame):
        return {'candidate_days': len(frame), **{key: int(frame[key].sum()) for key in
            ['expected_targets', 'valid_targets', 'complete_delivery_targets', 'persistence_issuable',
             'history_ml_issuable', 'persistence_scoreable_window', 'history_ml_scoreable_window', 'common_scoreable_window']}}
    eligible = training[training.eligible]
    return {'policy_version': 1, 'input_sha256': observations.sha256,
            'interval_minutes': 15, 'history_context_observations': CONTEXT,
            'training_lead_minutes': 15, 'issue_local_time': '18:00 Europe/Brussels',
            'window_start_local': windows.delivery_date.iloc[0],
            'window_end_local_exclusive': (date.fromisoformat(windows.delivery_date.iloc[-1])+timedelta(days=1)).isoformat(),
            'input_rows': observations.input_rows, 'absent_intervals': observations.absent_intervals,
            'missing_values': observations.missing_values, 'invalid_values': observations.invalid_values,
            'publication_delay_minutes_after_interval_end_assumed': delay_minutes,
            'fit_cutoff': utc(fit_cutoff).isoformat(), 'training_start_local': start.isoformat(),
            'training_end_local_exclusive': end.isoformat(),
            'training': {'candidate_examples': len(training), 'eligible_examples': int(training.eligible.sum()),
                         'excluded_examples': int((~training.eligible).sum()),
                         'exclusion_reason_counts_overlapping': reason_counts,
                         'first_eligible_target': str(eligible.target.iloc[0]) if len(eligible) else None,
                         'last_eligible_target': str(eligible.target.iloc[-1]) if len(eligible) else None},
            'windows': window_summary(windows),
            'protocol_period_windows': {name: window_summary(windows[(windows.delivery_date >= a) & (windows.delivery_date < b)])
                                        for name, a, b in periods},
            'limitations': ['Assumed availability, not observed historical publication times.',
                           'Input eligibility does not establish sufficient fit samples or predictive accuracy.',
                           'Window masks do not validate a per-origin model-fitting schedule; 2024 windows are diagnostic only.',
                           'No generation values filled; no model fitted; no final-test errors inspected.',
                           'Local-day seasonal baseline eligibility is deferred to the baseline design.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New directory; existing data is never overwritten')
    parser.add_argument('--availability-delay-minutes', required=True, type=int,
                        help='Assumed delay AFTER the 15-minute interval completes; not a measured latency')
    parser.add_argument('--fit-cutoff', required=True, help='Offset-aware last permissible label availability time')
    parser.add_argument('--training-start', type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument('--training-end', type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument('--window-start', type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument('--window-end', type=date.fromisoformat, default=date(2026, 1, 1))
    args = parser.parse_args(argv)
    try:
        data = load_observations(args.input)
        training = training_examples(data, args.training_start, args.training_end,
                                      args.fit_cutoff, args.availability_delay_minutes)
        windows = forecast_windows(data, args.window_start, args.window_end, args.availability_delay_minutes)
        report = summarize(training, windows, data, args.fit_cutoff, args.availability_delay_minutes,
                           args.training_start, args.training_end)
        args.output.mkdir(parents=True, exist_ok=False)
        training.to_csv(args.output/'training-eligibility.csv', index=False, lineterminator='\n')
        windows.to_csv(args.output/'window-eligibility.csv', index=False, lineterminator='\n')
        (args.output/'summary.json').write_bytes((json.dumps(report, indent=2, sort_keys=True)+'\n').encode())
        print(json.dumps(report, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Eligibility audit failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
