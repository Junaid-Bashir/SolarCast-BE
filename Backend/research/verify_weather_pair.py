"""Read-only verification of persisted paired predictions and source references."""
import argparse
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from app.baselines import load_power
from app.dayahead_schedule import window_for_day
from app.weather_pair import PAIR, load_weather, predict_pair, window_inputs


def verify(root,solar,weather_root):
    report=json.loads((root/'summary.json').read_bytes())
    assert report['complete'] and not report['reserved_test_scored']
    for name,digest in report['artifacts_sha256'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
    observations,power=load_power(solar)
    assert observations.sha256==report['solar_input_sha256']
    assert hashlib.sha256((weather_root/'summary.json').read_bytes()).hexdigest()==report['weather_summary_sha256']
    weather,_=load_weather(weather_root,observations.sha256)
    scored=pd.read_csv(root/'scored-predictions.csv')
    times=pd.to_datetime(scored.target_timestamp_utc,utc=True)
    assert (times.dt.tz_convert('Europe/Brussels').dt.year==2025).all()
    assert (pd.to_datetime(scored.delivery_date)<pd.Timestamp('2025-10-01')).all()
    np.testing.assert_array_equal(scored.actual_generation_mw,power.reindex(pd.DatetimeIndex(times)).to_numpy())
    keys=None;verified={}
    for name,group in scored.groupby('model'):
        current=list(zip(group.delivery_date,group.target_timestamp_utc))
        assert len(set(current))==len(current)
        if keys is None:keys=current
        else:assert keys==current
        errors=(group.predicted_generation_mw-group.actual_generation_mw).to_numpy()
        values={'rmse_mw':math.sqrt(math.fsum(float(e)**2 for e in errors)/len(errors)),
                'mae_mw':math.fsum(abs(float(e)) for e in errors)/len(errors)}
        for metric,value in values.items():assert math.isclose(value,report['metrics'][name][metric],rel_tol=1e-12)
        verified[name]=values
    audit=pd.read_csv(root/'training-eligibility.csv')
    selected=audit[audit.eligible]
    assert len(selected)==report['training_rows_shared']
    assert (pd.to_datetime(selected.label_available_at_assumed,utc=True)<=pd.Timestamp(report['fit_cutoff'])).all()
    assert (pd.to_datetime(selected.target,utc=True).dt.tz_convert('Europe/Brussels').dt.year==2024).all()
    models={}
    for name in PAIR:
        model=XGBRegressor();model.load_model(root/'models'/f'{name}.json');model.set_params(n_jobs=1);models[name]=model
    for day in [date(2025,1,1),date(2025,3,30),date(2025,6,9),date(2025,9,30)]:
        window=window_for_day(day,15)
        result,_=predict_pair(models,power,weather,window)
        for name in PAIR:
            saved=scored[(scored.delivery_date==day.isoformat())&(scored.model==name)]
            np.testing.assert_allclose(result[name],saved.predicted_generation_mw,rtol=1e-12,atol=1e-9)
        changed=power.copy();changed.loc[changed.index>window.observation_origin]=np.nan
        causal,_=predict_pair(models,changed,weather,window)
        for name in result:np.testing.assert_array_equal(causal[name],result[name])
        control,_,_,_,_=window_inputs(power,weather,window)
        history=power.loc[window.observation_origin-96*pd.Timedelta(15,unit='min'):window.observation_origin]
        assert len(history)==97
        assert math.isclose(control.rolling_day_mean.iloc[0],float(history.iloc[-96:].mean()),rel_tol=1e-12)
    return {'passed':True,'matched_training_rows':len(selected),'matched_targets_per_model':len(keys),
            'persisted_model_reproduction_days':4,'future_actual_mutation_days':4,'independent_metrics':verified}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--solar',type=Path,required=True)
    parser.add_argument('--weather',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(verify(args.run,args.solar,args.weather),indent=2))
