"""Export executable day-ahead window plans from observed-only eligible inputs.

The CSV describes forecast steps, not predicted production. Baseline generation
and backtesting consume these plans in subsequent work.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from .dayahead_schedule import window_for_day
from .eligibility import forecast_windows, load_observations


def export_plan(input_path, output, start, end, delay_minutes, profile):
    if profile not in ('history_ml', 'persistence'):
        raise ValueError('Choose history_ml or persistence')
    observations = load_observations(input_path)
    audit = forecast_windows(observations, start, end, delay_minutes)
    output.mkdir(parents=True, exist_ok=False)
    summary_path = output/'summary.json'
    summary_path.write_bytes(b'{"complete": false}\n')
    rows = []
    totals = Counter()
    counts_by_delivery_intervals = Counter()
    with (output/'trajectory.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = None
        for _, flags in audit.iterrows():
            window = window_for_day(date.fromisoformat(flags.delivery_date), delay_minutes)
            input_eligible = bool(flags[f'{profile}_issuable'])
            row = {**window.metadata(), **flags.to_dict(), 'profile': profile,
                   'input_eligible': input_eligible, 'plan_status': 'planned' if input_eligible else 'excluded'}
            reasons = {'origin_outside_input', 'invalid_or_missing_origin'}
            if profile == 'history_ml':
                reasons |= {'insufficient_history_extent', 'invalid_or_missing_history'}
            row['plan_exclusion_reasons'] = '|'.join(r for r in flags.exclusion_reasons.split('|') if r in reasons)
            rows.append(row)
            totals['candidate_windows'] += 1
            if not input_eligible:
                totals['excluded_input_windows'] += 1
                continue
            totals['planned_windows'] += 1
            totals['delivery_intervals'] += len(window.targets)
            totals['bridge_intervals'] += len(window.bridge)
            totals['trajectory_intervals'] += len(window.trajectory)
            totals['planned_windows_with_complete_targets'] += int(flags.complete_delivery_targets)
            counts_by_delivery_intervals[str(len(window.targets))] += 1
            for point in window.points():
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(point), lineterminator='\n')
                    writer.writeheader()
                writer.writerow(point)
        if writer is None:
            # Preserve a meaningful empty-table schema even if every day is excluded.
            example = next(window_for_day(start, delay_minutes).points())
            csv.DictWriter(handle, fieldnames=list(example), lineterminator='\n').writeheader()
    frame = pd.DataFrame(rows)
    # Date/offset-aware fields remain explicit in CSV, including repeated DST hours.
    for name in ('issue_at', 'latest_observation_origin'):
        frame[name] = frame[name].map(lambda value: value.isoformat())
    frame.to_csv(output/'windows.csv', index=False, lineterminator='\n')
    report = {'complete': True, 'schedule_version': 1, 'eligibility_policy_version': 1,
              'profile': profile, 'input_sha256': observations.sha256,
              'start_local': start.isoformat(), 'end_local_exclusive': end.isoformat(),
              'availability_delay_minutes_after_interval_end_assumed': delay_minutes,
              'counts': {key: totals[key] for key in ('candidate_windows','excluded_input_windows','planned_windows',
                        'delivery_intervals','bridge_intervals','trajectory_intervals','planned_windows_with_complete_targets')},
              'planned_windows_by_delivery_intervals': dict(sorted(counts_by_delivery_intervals.items())),
              'artifacts_sha256': {name: hashlib.sha256((output/name).read_bytes()).hexdigest()
                                   for name in ('windows.csv', 'trajectory.csv')},
              'notes': ['Schedule only: no production predictions, scores, models or repaired source values.',
                        'A window is planned from issue-time input eligibility, never from future target completeness.',
                        'Bridge steps advance recursive state and are excluded from delivery scoring.',
                        'A bridge target can precede issue time when its measured value is assumed unpublished.',
                        'Historical publication delay and revision vintages remain unverified.',
                        'No fitting schedule or model-selection/test scoring is executed.']}
    summary_path.write_bytes((json.dumps(report, indent=2, sort_keys=True)+'\n').encode())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New output directory')
    parser.add_argument('--start', required=True, type=date.fromisoformat, help='Inclusive Brussels delivery date')
    parser.add_argument('--end', required=True, type=date.fromisoformat, help='Exclusive Brussels delivery date')
    parser.add_argument('--availability-delay-minutes', required=True, type=int)
    parser.add_argument('--profile', choices=['history_ml','persistence'], default='history_ml')
    args = parser.parse_args(argv)
    try:
        report = export_plan(args.input, args.output, args.start, args.end,
                             args.availability_delay_minutes, args.profile)
        print(json.dumps(report, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Day-ahead planning failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
