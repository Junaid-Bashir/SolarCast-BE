"""Matched direct XGBoost residual models: history control versus history+weather.

Fits frozen 2024 models and scores only January–September 2025 validation.
"""
import argparse
import hashlib
import json
from datetime import date,timedelta
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from .baselines import LAG,VALIDATION_START,VALIDATION_END,load_power,metrics,skill
from .dayahead_schedule import STEP,delay_delta,window_for_day
from .eligibility import validate_range
from .forecasting import build_estimator
from .ml_validation import FIT_CUTOFF,batch_features

PAIR=('xgboost_no_weather','xgboost_weather')
MODELS=('persistence','seasonal_naive_48h',*PAIR)
WEATHER_COLUMNS=[f'forecast_{variable}_{stat}' for variable in ['radiation','cloud','temperature'] for stat in ['mean','min','max']]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_weather(root,solar_hash):
    report=json.loads((root/'summary.json').read_bytes())
    if not report.get('complete') or report.get('alignment_version')!=1 or report.get('model')!='dwd_icon_eu':
        raise ValueError('Require completed ICON EU alignment v1')
    if report.get('solar_input_sha256')!=solar_hash or report.get('nominal_lead_hours')!=48 or report.get('allowance_minutes_assumed')!=370:
        raise ValueError('Weather/solar provenance or availability settings differ')
    if report.get('feature_columns')!=WEATHER_COLUMNS:
        raise ValueError('Weather feature schema differs')
    if 'weather-features.csv' not in report.get('artifacts_sha256',{}):
        raise ValueError('Weather feature checksum is required')
    for name,digest in report['artifacts_sha256'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path)!=digest:
            raise ValueError('Weather artifact checksum mismatch')
    frame=pd.read_csv(root/'weather-features.csv',low_memory=False)
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.timestamp,utc=True))
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('Weather targets must be unique and increasing')
    for key in ['issue_at','latest_nominal_available_at_assumed']:
        frame[key]=pd.to_datetime(frame[key],utc=True)
    if 'target_generation_mw' in frame:
        raise ValueError('Weather feature file must not contain reference labels')
    return frame,report


def window_inputs(power,weather,window):
    """Issue-time predictors only. No target production is read."""
    observed=power.loc[power.index+STEP+delay_delta(window.delay_minutes)<=window.issue_at]
    history_times=pd.date_range(window.observation_origin-96*STEP,window.observation_origin,freq=STEP)
    history=observed.reindex(history_times).to_numpy(dtype=float)
    source_times=window.targets-LAG
    seasonal=observed.reindex(source_times).to_numpy(dtype=float)
    history_ok=bool((np.isfinite(history)&(history>=0)&(history<=1e12)).all())
    seasonal_ok=np.isfinite(seasonal)&(seasonal>=0)&(seasonal<=1e12)
    source_available=(source_times+STEP+delay_delta(window.delay_minutes)<=window.issue_at)
    seasonal_ok &= source_available
    forecast=weather.reindex(window.targets)
    wx=forecast[WEATHER_COLUMNS].to_numpy(dtype=float)
    weather_ok=(forecast.weather_valid.eq(True).to_numpy()&np.isfinite(wx).all(axis=1)
                &(forecast.latest_nominal_available_at_assumed<=window.issue_at).to_numpy()
                &(forecast.issue_at==window.issue_at).to_numpy())
    if history_ok:
        base=batch_features(np.repeat(history[None,:],len(window.targets),axis=0),window.targets)
    else:
        base=batch_features(np.zeros((len(window.targets),97)),window.targets)*np.nan
    base['lead_hours_from_issue']=((window.targets-window.issue_at)/pd.Timedelta(3_600_000_000_000,unit='ns')).to_numpy()
    base['seasonal_naive_48h_mw']=seasonal
    augmented=base.copy()
    for index,name in enumerate(WEATHER_COLUMNS):augmented[name]=wx[:,index]
    eligible=history_ok & seasonal_ok & weather_ok
    return base,augmented,seasonal,eligible,{'history_ok':history_ok,'seasonal_ok':seasonal_ok,'weather_ok':weather_ok}


def prepare_training(power,weather,delay_minutes):
    rows_control,rows_weather,targets,audits=[],[],[],[]
    for offset in range(366):
        day=date(2024,1,1)+timedelta(days=offset)
        window=window_for_day(day,delay_minutes)
        control,augmented,seasonal,input_ok,flags=window_inputs(power,weather,window)
        actual=power.reindex(window.targets).to_numpy(dtype=float)
        target_ok=np.isfinite(actual)&(actual>=0)&(actual<=1e12)
        known=window.targets+STEP+delay_delta(delay_minutes)<=FIT_CUTOFF
        eligible=input_ok&target_ok&known
        for i,target in enumerate(window.targets):
            reasons=[]
            if not flags['history_ok']:reasons.append('incomplete_issue_history')
            if not flags['seasonal_ok'][i]:reasons.append('missing_or_unavailable_seasonal_source')
            if not flags['weather_ok'][i]:reasons.append('missing_or_unavailable_weather')
            if not target_ok[i]:reasons.append('invalid_or_missing_label')
            if not known[i]:reasons.append('label_unavailable_at_fit_cutoff')
            audits.append({'target':target,'issue_at':window.issue_at,'observation_origin':window.observation_origin,
                           'label_available_at_assumed':target+STEP+delay_delta(delay_minutes),
                           'eligible':bool(eligible[i]),'exclusion_reasons':'|'.join(reasons)})
        if eligible.any():
            rows_control.append(control.loc[eligible]);rows_weather.append(augmented.loc[eligible])
            targets.append(actual[eligible]-seasonal[eligible])
    if not targets or sum(len(v) for v in targets)<192:
        raise ValueError('Need at least 192 matched eligible training examples')
    x=pd.concat(rows_control,ignore_index=True);wx=pd.concat(rows_weather,ignore_index=True)
    y=np.concatenate(targets)
    if not np.isfinite(x.to_numpy()).all() or not np.isfinite(wx.to_numpy()).all():
        raise ValueError('Matched training features are not finite')
    pd.testing.assert_frame_equal(x,wx[list(x.columns)])
    return x,wx,y,pd.DataFrame(audits)


def predict_pair(models,power,weather,window):
    control,augmented,seasonal,eligible,flags=window_inputs(power,weather,window)
    if not eligible.all():
        raise ValueError('Incomplete paired issue-time inputs')
    result={};corrections={}
    for name,features in zip(PAIR,[control,augmented]):
        correction=np.asarray(models[name].predict(features),dtype=float)
        if correction.shape!=(len(window.targets),) or not np.isfinite(correction).all() or (np.abs(correction)>1e12).any():
            raise ValueError('Invalid residual prediction')
        predicted=np.maximum(0.0,seasonal+correction)
        if not np.isfinite(predicted).all() or (predicted>1e12).any():raise ValueError('Invalid reconstructed prediction')
        result[name]=predicted;corrections[name]=correction
    result['seasonal_naive_48h']=seasonal.copy()
    result['persistence']=np.full(len(window.targets),float(control.observed_at_origin.iloc[0]))
    return result,corrections


def run(input_path,weather_root,output,start,end,delay_minutes,progress=print):
    validate_range(start,end,delay_minutes)
    if start<VALIDATION_START or end>VALIDATION_END:
        raise ValueError('Only validation dates [2025-01-01,2025-10-01) may be scored; reserved test is locked')
    observations,power=load_power(input_path)
    weather,weather_report=load_weather(weather_root,observations.sha256)
    control,augmented,y,training=prepare_training(power,weather,delay_minutes)
    output.mkdir(parents=True,exist_ok=False);(output/'models').mkdir()
    summary_path=output/'summary.json';summary_path.write_bytes(b'{"complete": false}\n')
    training.to_csv(output/'training-eligibility.csv',index=False,lineterminator='\n')
    models={};timings={};parameters={}
    for name,features in zip(PAIR,[control,augmented]):
        progress(f'Fitting {name}: {len(y)} shared rows, {len(features.columns)} features')
        estimator=build_estimator('xgboost')
        began=perf_counter();estimator.fit(features,y);elapsed=perf_counter()-began
        parameters[name]={k:str(v) if isinstance(v,float) and not np.isfinite(v) else v for k,v in estimator.get_params().items()}
        estimator.set_params(n_jobs=1)
        estimator.save_model(output/'models'/f'{name}.json')
        models[name]=estimator;timings[name]={'fit_seconds':elapsed,'prediction_seconds':0.0}
    ledgers=[];predictions=[];scored=[];daily=[]
    for offset in range((end-start).days):
        day=start+timedelta(days=offset);window=window_for_day(day,delay_minutes)
        failure='';results={};corrections={}
        began=perf_counter()
        try:results,corrections=predict_pair(models,power,weather,window)
        except ValueError as exc:failure=str(exc)
        # Paired path runtime is a cohort wall-time measure, not per-model latency.
        elapsed=perf_counter()-began
        for name in PAIR:timings[name]['prediction_seconds']+=elapsed
        actual=power.reindex(window.targets).to_numpy(dtype=float)
        actual_ok=np.isfinite(actual)&(actual>=0)&(actual<=1e12)
        common=bool(results) and actual_ok.all()
        if not actual_ok.all():failure+='|incomplete_delivery_references'
        ledgers.append({**window.metadata(),'paired_issued':bool(results),'valid_targets':int(actual_ok.sum()),
                        'common_scored_window':bool(common),'exclusion_reasons':failure.strip('|')})
        if not results:continue
        seasonal=results['seasonal_naive_48h']
        for name,values in results.items():
            if common:daily.append({'delivery_date':day.isoformat(),'model':name,**metrics(actual,values)})
            for i,target in enumerate(window.targets):
                row={'delivery_date':day.isoformat(),'issue_at':window.issue_at.isoformat(),
                     'observation_origin':window.observation_origin.isoformat(),'target_timestamp_utc':target.isoformat(),
                     'target_timestamp_local':target.tz_convert('Europe/Brussels').isoformat(),'model':name,
                     'seasonal_starting_point_mw':float(seasonal[i]),
                     'raw_correction_mw':float(corrections[name][i]) if name in corrections else None,
                     'predicted_generation_mw':float(values[i])}
                predictions.append(row)
                if common:scored.append({**row,'actual_generation_mw':float(actual[i]),'error_mw':float(values[i]-actual[i])})
    columns=['delivery_date','issue_at','observation_origin','target_timestamp_utc','target_timestamp_local','model',
             'seasonal_starting_point_mw','raw_correction_mw','predicted_generation_mw']
    scored_frame=pd.DataFrame(scored,columns=[*columns,'actual_generation_mw','error_mw'])
    metrics_by_model={}
    for name in MODELS:
        selected=scored_frame[scored_frame.model==name]
        metrics_by_model[name]=metrics(selected.actual_generation_mw,selected.predicted_generation_mw) if len(selected) else None
    if metrics_by_model[PAIR[0]]:
        for values in metrics_by_model.values():
            values['rmse_skill_vs_seasonal_pct']=skill(values['rmse_mw'],metrics_by_model['seasonal_naive_48h']['rmse_mw'])
        weather_improvement=skill(metrics_by_model[PAIR[1]]['rmse_mw'],metrics_by_model[PAIR[0]]['rmse_mw'])
    else:weather_improvement=None
    ledger=pd.DataFrame(ledgers)
    for name,frame in [('windows.csv',ledger),('predictions.csv',pd.DataFrame(predictions,columns=columns)),
                       ('scored-predictions.csv',scored_frame),
                       ('daily-metrics.csv',pd.DataFrame(daily,columns=['delivery_date','model','observations','mae_mw','rmse_mw','bias_mw']))]:
        frame.to_csv(output/name,index=False,lineterminator='\n')
    selected=training[training.eligible]
    report={'complete':True,'weather_pair_version':1,'period':'validation','start_local':start.isoformat(),
        'end_local_exclusive':end.isoformat(),'solar_input_sha256':observations.sha256,
        'weather_summary_sha256':sha(weather_root/'summary.json'),'fit_cutoff':FIT_CUTOFF.isoformat(),
        'solar_delay_minutes_assumed':delay_minutes,'weather_nominal_lead_hours':48,'weather_allowance_minutes_assumed':370,
        'training_rows_shared':len(y),'first_training_target':str(selected.target.iloc[0]),'last_training_target':str(selected.target.iloc[-1]),
        'control_features':list(control.columns),'weather_features':list(augmented.columns),
        'parameters':parameters,'runtime':timings,'candidate_days':len(ledger),
        'issued_days':int(ledger.paired_issued.sum()),'common_scored_days':int(ledger.common_scored_window.sum()),
        'scored_targets_per_model':metrics_by_model[PAIR[0]]['observations'] if metrics_by_model[PAIR[0]] else 0,
        'metrics':metrics_by_model,'weather_rmse_skill_vs_matched_control_pct':weather_improvement,
        'target_definition':'Direct residual = target production - known 48-hour seasonal prediction. Final output=max(0,seasonal+residual).',
        'reserved_test_scored':False,'historical_weather_publication_times_verified':False,'live_compatibility_verified':False,
        'notes':['Same rows, labels, XGBoost settings and shared features; only the nine weather features differ.',
                 'One frozen fit per model, no validation tuning/early stopping, no recursive feedback or bridge predictions.',
                 'Complete matched days only; missing future references affect scoring, never issuance.',
                 'Fit cutoff matches first validation deadline; initial computational fitting latency is not simulated.',
                 'Training remains seasonally restricted by issue-time production history requirements.',
                 'Prediction runtime is the same measured paired feature+two-model path for each model entry; do not sum the two entries.'],
        'attribution':weather_report['attribution'],'solar_attribution':weather_report.get('solar_attribution'),
        'artifacts_sha256':{str(path.relative_to(output)).replace('\\','/'):sha(path) for path in
            [output/'training-eligibility.csv',output/'windows.csv',output/'predictions.csv',output/'scored-predictions.csv',output/'daily-metrics.csv',
             *[output/'models'/f'{name}.json' for name in PAIR]]}}
    summary_path.write_bytes((json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n').encode())
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solar',required=True,type=Path);parser.add_argument('--weather',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--solar-delay-minutes',required=True,type=int)
    parser.add_argument('--start',type=date.fromisoformat,default=VALIDATION_START)
    parser.add_argument('--end',type=date.fromisoformat,default=VALIDATION_END)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(run(args.solar,args.weather,args.output,args.start,args.end,args.solar_delay_minutes),indent=2))
        return 0
    except (ValueError,OSError,KeyError) as exc:parser.exit(1,f'Weather-pair validation failed: {exc}\n')


if __name__=='__main__':raise SystemExit(main())
