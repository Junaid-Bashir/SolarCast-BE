"""Causal day-ahead baseline forecasts and validation-only historical evaluation."""
import argparse
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .dayahead_schedule import STEP, delay_delta, utc, window_for_day
from .eligibility import load_observations, validate_range

MODELS = ('persistence', 'seasonal_naive_48h')
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 1)
LAG = pd.Timedelta(172_800_000_000_000, unit='ns')
ATTRIBUTION = 'Source: Elia Transmission Belgium SA, Elia Open Data ods032, CC BY 4.0. Filtered, time-normalized and evaluated independently by SolarCast.'


class BaselineUnavailable(ValueError):
    pass


@dataclass
class BaselineForecast:
    model: str
    targets: pd.DatetimeIndex
    sources: pd.DatetimeIndex
    predicted: np.ndarray


def predict_baseline(window, power, model):
    """No reference targets are read; every source must be valid at issue time.

    power is an explicitly UTC-indexed observed series. Even if the caller holds
    future observations, the view passed to lookup is truncated at issue time.
    Missing chosen sources invalidate the model; no older-value fallback occurs.
    """
    if model not in MODELS:
        raise ValueError('Unknown baseline')
    if power.index.tz is None or power.index.has_duplicates or not power.index.is_monotonic_increasing:
        raise ValueError('Require unique increasing offset-aware observation timestamps')
    targets = window.trajectory
    sources = (pd.DatetimeIndex([window.observation_origin]*len(targets)) if model == 'persistence'
               else targets-LAG)
    availability = sources+STEP+delay_delta(window.delay_minutes)
    if not (availability <= window.issue_at).all():
        raise BaselineUnavailable('source_unavailable_at_issue')
    observed = power.loc[power.index+STEP+delay_delta(window.delay_minutes) <= window.issue_at]
    values = observed.reindex(sources).to_numpy(dtype=float)
    if not (np.isfinite(values) & (values >= 0) & (values <= 1e12)).all():
        raise BaselineUnavailable('missing_or_invalid_source')
    return BaselineForecast(model, targets, sources, values.copy())


def load_power(path):
    observations = load_observations(path)
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != observations.sha256:
        raise ValueError('Input changed while loading')
    frame = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False)
    times = pd.DatetimeIndex([utc(value) for value in frame.timestamp])
    values = pd.to_numeric(frame.generation_mw, errors='coerce').to_numpy(dtype=float)
    return observations, pd.Series(values, index=times)


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual,dtype=float), np.asarray(predicted,dtype=float)
    if actual.shape != predicted.shape or len(actual)==0 or not (np.isfinite(actual).all() and np.isfinite(predicted).all()):
        raise ValueError('Require matching, nonempty finite reference/prediction vectors')
    error = predicted-actual
    return {'observations': len(actual), 'mae_mw': float(np.mean(np.abs(error))),
            'rmse_mw': float(np.sqrt(np.mean(error**2))), 'bias_mw': float(np.mean(error))}


def skill(rmse, baseline_rmse):
    return None if baseline_rmse == 0 else 100*(1-rmse/baseline_rmse)


def run_validation(input_path, output, start, end, delay_minutes):
    validate_range(start,end,delay_minutes)
    if start < VALIDATION_START or end > VALIDATION_END:
        raise ValueError('This command scores only validation delivery dates [2025-01-01,2025-10-01); reserved test is locked')
    observations, power = load_power(input_path)
    output.mkdir(parents=True,exist_ok=False)
    summary_path = output/'summary.json'
    summary_path.write_bytes(b'{"complete": false}\n')
    windows, predictions, scored = [], [], []
    daily = []
    for offset in range((end-start).days):
        day = start+timedelta(days=offset)
        window = window_for_day(day,delay_minutes)
        forecasts, failures = {}, {}
        # Generate first: future reference completeness cannot affect forecasts.
        for model in MODELS:
            try:
                forecasts[model] = predict_baseline(window,power,model)
            except BaselineUnavailable as exc:
                failures[model] = str(exc)
        target_valid, _, _ = observations.lookup(window.targets)
        complete = bool(target_valid.all())
        common = complete and len(forecasts)==len(MODELS)
        context, _ = observations.context(pd.DatetimeIndex([window.observation_origin]))
        reasons = [f'{name}:{reason}' for name,reason in failures.items()]
        if not complete:
            reasons.append('incomplete_delivery_targets')
        windows.append({**window.metadata(), 'valid_reference_targets': int(target_valid.sum()),
                        'complete_reference_targets': complete, 'history_ml_input_eligible': bool(context[0]),
                        **{f'{model}_issued': model in forecasts for model in MODELS},
                        'common_scored_window': common, 'exclusion_reasons': '|'.join(reasons)})
        points = list(window.points())
        for model,forecast in forecasts.items():
            for i,point in enumerate(points):
                predicted = float(forecast.predicted[i])
                row = {**point,'model': model,'source_timestamp': forecast.sources[i].isoformat(),
                       'source_available_at_assumed': (forecast.sources[i]+STEP+delay_delta(delay_minutes)).isoformat(),
                       'predicted_generation_mw': predicted}
                predictions.append(row)
                if common and point['role']=='delivery':
                    actual = float(power.loc[forecast.targets[i]])
                    scored.append({**row, 'actual_generation_mw': actual,'error_mw': predicted-actual})
            if common:
                delivery = forecast.targets >= window.delivery_start
                actual = power.reindex(window.targets).to_numpy(dtype=float)
                daily.append({'delivery_date':day.isoformat(),'model':model,
                              **metrics(actual,forecast.predicted[delivery])})
    window_frame = pd.DataFrame(windows)
    prediction_columns = [*next(window_for_day(start,delay_minutes).points()).keys(), 'model',
                          'source_timestamp','source_available_at_assumed','predicted_generation_mw']
    predicted_frame = pd.DataFrame(predictions,columns=prediction_columns)
    score_frame = pd.DataFrame(scored,columns=[*prediction_columns,'actual_generation_mw','error_mw'])
    daily_frame = pd.DataFrame(daily,columns=['delivery_date','model','observations','mae_mw','rmse_mw','bias_mw'])
    aggregate = {}
    for model in MODELS:
        subset = score_frame[score_frame.model==model]
        aggregate[model] = metrics(subset.actual_generation_mw,subset.predicted_generation_mw) if len(subset) else None
    base_rmse = aggregate['persistence']['rmse_mw'] if aggregate['persistence'] else None
    for metric in aggregate.values():
        if metric is not None:
            metric['rmse_skill_vs_persistence_pct'] = skill(metric['rmse_mw'],base_rmse)
    for filename,frame in [('windows.csv',window_frame),('predictions.csv',predicted_frame),
                           ('scored-predictions.csv',score_frame),('daily-metrics.csv',daily_frame)]:
        frame.to_csv(output/filename,index=False,lineterminator='\n')
    scored_days = int(window_frame.common_scored_window.sum())
    report = {'complete':True,'baseline_version':1,'schedule_version':1,'eligibility_policy_version':1,
              'input_sha256': observations.sha256,'period':'validation',
              'start_local':start.isoformat(),'end_local_exclusive':end.isoformat(),
              'availability_delay_minutes_after_interval_end_assumed':delay_minutes,
              'candidate_days':len(windows),'common_scored_days':scored_days,
              'excluded_days':len(windows)-scored_days,
              'coverage_days_pct':100*scored_days/len(windows),
              'candidate_delivery_intervals':int(window_frame.delivery_intervals.sum()),
              'scored_delivery_intervals_per_model':len(score_frame)//len(MODELS),
              'issued_windows_by_model':{model:int(window_frame[f'{model}_issued'].sum()) for model in MODELS},
              'models':list(MODELS),'metrics':aggregate,
              'reserved_test_scored':False,'attribution':ATTRIBUTION,
              'baseline_definitions':{'persistence':'Hold the latest admissible observation across the complete trajectory.',
                                      'seasonal_naive_48h':'Use source t-48 elapsed hours for each trajectory point; every chosen source must exist, be valid and available at issue. No fallback.'},
              'notes':['Both models are scored on identical complete delivery windows; bridge steps are excluded.',
                       'Metrics are pooled quarter-hour MW errors, not interval energy or means of daily RMSE.',
                       'Nighttime intervals are included; persistence is a deliberately simple reference.',
                       'The 48-hour elapsed lag can shift the local solar profile by an hour around DST.',
                       'Neither baseline is fitted. Previously observed validation data may be used at later issues.',
                       'Historical publication latency and revisions are assumed, not reconstructed.',
                       'No comparison with Elia forecasts or claims of commercial performance.'],
              'artifacts_sha256':{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in
                                   ['windows.csv','predictions.csv','scored-predictions.csv','daily-metrics.csv']}}
    summary_path.write_bytes((json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n').encode())
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='New directory; never overwrite a run')
    parser.add_argument('--start',type=date.fromisoformat,default=VALIDATION_START)
    parser.add_argument('--end',type=date.fromisoformat,default=VALIDATION_END)
    parser.add_argument('--availability-delay-minutes',type=int,required=True)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(run_validation(args.input,args.output,args.start,args.end,args.availability_delay_minutes),indent=2))
        return 0
    except (ValueError,OSError,KeyError) as exc:
        parser.exit(1,f'Baseline validation failed: {exc}\n')


if __name__=='__main__':
    raise SystemExit(main())
