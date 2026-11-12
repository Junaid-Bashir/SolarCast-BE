"""Frozen 2024 one-step models, recursive day-ahead validation; final test locked."""
import argparse
import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from .baselines import ATTRIBUTION, MODELS as BASELINES, VALIDATION_START, VALIDATION_END, BaselineUnavailable, load_power, metrics, predict_baseline, skill
from .dayahead_schedule import STEP, window_for_day
from .eligibility import CONTEXT, training_examples, validate_range
from .forecasting import PreparedSeries, build_estimator, features_at_origins

ML_MODELS = ('random_forest', 'xgboost', 'lightgbm')
MODELS = (*BASELINES, *ML_MODELS)
FIT_CUTOFF = pd.Timestamp('2024-12-31T18:00:00+01:00').tz_convert('UTC')


def prepare_training(observations, power, delay_minutes):
    audit = training_examples(observations,date(2024,1,1),date(2025,1,1),FIT_CUTOFF,delay_minutes)
    eligible = audit[audit.eligible]
    if len(eligible) < 192:
        raise ValueError('Need at least 192 eligible 2024 training examples; no values are filled')
    # Restrict the feature source itself to the fitting deadline, in addition to
    # masking label availability. No 2025 values enter feature construction.
    cutoff = window_for_day(VALIDATION_START,delay_minutes).observation_origin
    clock = observations.times[observations.times <= cutoff]
    values = power.reindex(clock).to_numpy(dtype=float)
    origins = clock.get_indexer(pd.DatetimeIndex(eligible.origin))
    if (origins < CONTEXT-1).any():
        raise ValueError('Training origin does not have the required context')
    series = PreparedSeries(values,clock,15,96,1,0)
    features = features_at_origins(series,origins)
    targets = power.reindex(pd.DatetimeIndex(eligible.target)).to_numpy(dtype=float)
    if not np.isfinite(features.to_numpy()).all() or not np.isfinite(targets).all():
        raise ValueError('Eligibility and feature construction disagree')
    return features,targets,audit


def batch_features(buffers, target_times):
    """Same one-step features as forecasting.features_at_origins, across origins.

    Each row contains exactly 97 private observations/predictions. Target calendar
    is UTC, matching training and the existing app; no target actual is read.
    """
    if buffers.ndim != 2 or buffers.shape[1] != CONTEXT or not np.isfinite(buffers).all():
        raise ValueError('Require finite 97-value histories')
    hour = target_times.hour.to_numpy()+target_times.minute.to_numpy()/60
    weekday = target_times.dayofweek.to_numpy()
    doy = target_times.dayofyear.to_numpy()
    return pd.DataFrame({
        'observed_at_origin':buffers[:,-1], 'lag_1_interval':buffers[:,-2],
        'lag_1_hour':buffers[:,-5], 'lag_24_hours':buffers[:,0],
        'rolling_hour_mean':buffers[:,-4:].mean(axis=1),
        'rolling_day_mean':buffers[:,-96:].mean(axis=1),
        'rolling_day_std':buffers[:,-96:].std(axis=1,ddof=0),
        'target_hour_sin':np.sin(2*np.pi*hour/24), 'target_hour_cos':np.cos(2*np.pi*hour/24),
        'target_weekday_sin':np.sin(2*np.pi*weekday/7), 'target_weekday_cos':np.cos(2*np.pi*weekday/7),
        'target_dayofyear_sin':np.sin(2*np.pi*doy/365.25), 'target_dayofyear_cos':np.cos(2*np.pi*doy/365.25)})


def recursive_batch(estimator, windows, contexts):
    if not windows:
        return {},{},0
    buffers = np.asarray(contexts,dtype=float).copy()
    if buffers.shape != (len(windows),CONTEXT):
        raise ValueError('One 97-value context is required per window')
    lengths = np.array([len(w.trajectory) for w in windows])
    outputs = [np.empty(n) for n in lengths]
    failed, clipped = {},0
    for step in range(1,int(lengths.max())+1):
        active = [i for i in range(len(windows)) if lengths[i]>=step and i not in failed]
        if not active:
            continue
        clock = pd.DatetimeIndex([windows[i].observation_origin+step*STEP for i in active])
        rows = batch_features(buffers[active],clock)
        predicted = np.asarray(estimator.predict(rows),dtype=float)
        if predicted.shape != (len(active),):
            raise ValueError('Estimator returned the wrong prediction shape')
        for position,i in enumerate(active):
            raw = predicted[position]
            if not np.isfinite(raw) or abs(raw)>1e12:
                failed[i] = 'unstable_recursive_prediction'
                continue
            clipped += int(raw<0)
            value = max(0.0,float(raw))
            outputs[i][step-1] = value
            buffers[i,:-1] = buffers[i,1:].copy()
            buffers[i,-1] = value
    return ({w.delivery_date:outputs[i] for i,w in enumerate(windows) if i not in failed},
            {windows[i].delivery_date:reason for i,reason in failed.items()},clipped)


def run_validation(input_path,output,start,end,delay_minutes,progress=lambda message: None):
    validate_range(start,end,delay_minutes)
    if start < VALIDATION_START or end > VALIDATION_END:
        raise ValueError('Only validation dates [2025-01-01,2025-10-01) may be scored; reserved test is locked')
    observations,power = load_power(input_path)
    features,labels,training = prepare_training(observations,power,delay_minutes)
    output.mkdir(parents=True,exist_ok=False)
    summary_path = output/'summary.json'
    summary_path.write_bytes(b'{"complete": false}\n')
    training.to_csv(output/'training-eligibility.csv',index=False,lineterminator='\n')
    windows = [window_for_day(start+timedelta(days=i),delay_minutes) for i in range((end-start).days)]
    ready,contexts = [],[]
    forecasts = {name:{} for name in MODELS}
    failures = {name:{} for name in MODELS}
    runtime = {name:{'fit_seconds':0.0,'prediction_seconds':0.0} for name in MODELS}
    for w in windows:
        valid,_ = observations.context(pd.DatetimeIndex([w.observation_origin]))
        if valid[0]:
            ready.append(w)
            clock = pd.date_range(w.observation_origin-96*STEP,w.observation_origin,freq=STEP)
            contexts.append(power.reindex(clock).to_numpy(dtype=float))
        else:
            for name in ML_MODELS:
                failures[name][w.delivery_date] = 'incomplete_ml_history'
        for name in BASELINES:
            began = perf_counter()
            try:
                forecasts[name][w.delivery_date] = predict_baseline(w,power,name).predicted
            except BaselineUnavailable as exc:
                failures[name][w.delivery_date] = str(exc)
            runtime[name]['prediction_seconds'] += perf_counter()-began
    fit_details = {}
    for name in ML_MODELS:
        progress(f'Fitting {name} on {len(labels)} eligible 2024 examples')
        estimator = build_estimator(name)
        began = perf_counter()
        estimator.fit(features,labels)
        runtime[name]['fit_seconds'] = perf_counter()-began
        fit_details[name] = {key:(str(value) if isinstance(value,float) and not np.isfinite(value) else value)
                             for key,value in estimator.get_params().items()}
        estimator.set_params(n_jobs=1)
        began = perf_counter()
        predicted,failed,clipped = recursive_batch(estimator,ready,contexts)
        runtime[name]['prediction_seconds'] = perf_counter()-began
        forecasts[name].update(predicted)
        failures[name].update(failed)
        runtime[name]['negative_predictions_clipped'] = clipped
        progress(f'Completed {name}: {len(predicted)} issued windows')
    # Future reference values are consulted only after predictions are frozen.
    window_rows,daily = [],[]
    pooled = {name:[[],[]] for name in MODELS}
    point_columns = list(next(windows[0].points()))
    prediction_columns = [*point_columns,'model','predicted_generation_mw']
    score_columns = [*prediction_columns,'actual_generation_mw','error_mw']
    with (output/'predictions.csv').open('w',newline='',encoding='utf-8') as pfile, \
         (output/'scored-predictions.csv').open('w',newline='',encoding='utf-8') as sfile:
        pwriter,cwriter = csv.DictWriter(pfile,prediction_columns,lineterminator='\n'),csv.DictWriter(sfile,score_columns,lineterminator='\n')
        pwriter.writeheader(); cwriter.writeheader()
        for w in windows:
            valid,_,_ = observations.lookup(w.targets)
            complete = bool(valid.all())
            common = complete and all(w.delivery_date in forecasts[name] for name in MODELS)
            reasons = [f'{name}:{issues[w.delivery_date]}' for name,issues in failures.items() if w.delivery_date in issues]
            if not complete:
                reasons.append('incomplete_delivery_targets')
            window_rows.append({**w.metadata(),'valid_reference_targets':int(valid.sum()),
                                **{f'{name}_issued':w.delivery_date in forecasts[name] for name in MODELS},
                                'common_scored_window':common,'exclusion_reasons':'|'.join(reasons)})
            points = list(w.points())
            bridge_count = len(w.bridge)
            actual = power.reindex(w.targets).to_numpy(dtype=float) if common else None
            for name in MODELS:
                if w.delivery_date not in forecasts[name]:
                    continue
                values = forecasts[name][w.delivery_date]
                delivery = values[bridge_count:]
                if common:
                    pooled[name][0].append(actual.copy()); pooled[name][1].append(delivery.copy())
                    daily.append({'delivery_date':w.delivery_date.isoformat(),'model':name,**metrics(actual,delivery)})
                for i,point in enumerate(points):
                    row = {**point,'model':name,'predicted_generation_mw':float(values[i])}
                    pwriter.writerow(row)
                    if common and point['role']=='delivery':
                        reference = float(actual[i-bridge_count])
                        cwriter.writerow({**row,'actual_generation_mw':reference,'error_mw':float(values[i])-reference})
    aggregate = {name:metrics(np.concatenate(values[0]),np.concatenate(values[1])) if values[0] else None
                 for name,values in pooled.items()}
    if aggregate['persistence']:
        for result in aggregate.values():
            result['rmse_skill_vs_persistence_pct'] = skill(result['rmse_mw'],aggregate['persistence']['rmse_mw'])
            result['rmse_skill_vs_seasonal_48h_pct'] = skill(result['rmse_mw'],aggregate['seasonal_naive_48h']['rmse_mw'])
    ledger = pd.DataFrame(window_rows)
    ledger.to_csv(output/'windows.csv',index=False,lineterminator='\n')
    pd.DataFrame(daily,columns=['delivery_date','model','observations','mae_mw','rmse_mw','bias_mw']).to_csv(output/'daily-metrics.csv',index=False,lineterminator='\n')
    eligible = training[training.eligible]
    report = {'complete':True,'ml_validation_version':1,'input_sha256':observations.sha256,
              'period':'validation','start_local':start.isoformat(),'end_local_exclusive':end.isoformat(),
              'publication_delay_after_interval_end_minutes_assumed':delay_minutes,'fit_cutoff':FIT_CUTOFF.isoformat(),
              'training_examples':len(labels),'first_training_target':str(eligible.target.iloc[0]),
              'last_training_target':str(eligible.target.iloc[-1]),'feature_names':list(features.columns),
              'models':list(MODELS),'parameters':fit_details,'runtime':runtime,
              'candidate_days':len(windows),'common_scored_days':int(ledger.common_scored_window.sum()),
              'excluded_days':int((~ledger.common_scored_window).sum()),
              'scored_intervals_per_model':aggregate['persistence']['observations'] if aggregate['persistence'] else 0,
              'issued_windows_by_model':{name:len(values) for name,values in forecasts.items()},
              'metrics':aggregate,'reserved_test_scored':False,'attribution':ATTRIBUTION,
              'notes':['Fixed model settings and seed 42; one fit per ML model, no validation tuning or early stopping.',
                       'Each issue uses only its 97-observation context; recursive predictions feed every subsequent step, including bridges.',
                       'All five models are scored on identical complete delivery windows; no bridge is scored.',
                       'Calendar features use UTC, matching the existing feature implementation.',
                       'Training is concentrated in autumn/winter; no future weather, capacity scaling or physical night constraint.',
                       'Historical publication delay and revision vintages remain assumptions.',
                       'Runtime is measured wall time, includes batch prediction only (not export), and is not deterministic.'],
              'artifacts_sha256':{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in
                  ['training-eligibility.csv','windows.csv','predictions.csv','scored-predictions.csv','daily-metrics.csv']}}
    summary_path.write_bytes((json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n').encode())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--availability-delay-minutes',required=True,type=int)
    parser.add_argument('--start',type=date.fromisoformat,default=VALIDATION_START)
    parser.add_argument('--end',type=date.fromisoformat,default=VALIDATION_END)
    args = parser.parse_args(argv)
    try:
        report = run_validation(args.input,args.output,args.start,args.end,args.availability_delay_minutes,progress=print)
        print(json.dumps(report,indent=2))
        return 0
    except (ValueError,OSError,KeyError) as exc:
        parser.exit(1,f'ML validation failed: {exc}\n')


if __name__=='__main__':
    raise SystemExit(main())
