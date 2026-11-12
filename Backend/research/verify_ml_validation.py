"""Independently check ML artifacts and reproduce selected single-origin rollouts.

From Backend: python research/verify_ml_validation.py RUN_DIR INPUT.csv
Refits fixed settings for verification only; no tuning or final-test scoring.
"""
import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.baselines import load_power
from app.dayahead_schedule import STEP,window_for_day
from app.forecasting import PreparedSeries,build_estimator,features_at_origins
from app.ml_validation import ML_MODELS,prepare_training


def verify(run,input_path):
    report=json.loads((run/'summary.json').read_bytes())
    assert report['complete'] and not report['reserved_test_scored']
    assert hashlib.sha256(input_path.read_bytes()).hexdigest()==report['input_sha256']
    for name,digest in report['artifacts_sha256'].items():
        assert hashlib.sha256((run/name).read_bytes()).hexdigest()==digest,name
    observations,power=load_power(input_path)
    scored=pd.read_csv(run/'scored-predictions.csv')
    assert scored.role.eq('delivery').all()
    assert scored.delivery_date.min()>='2025-01-01' and scored.delivery_date.max()<'2025-10-01'
    np.testing.assert_array_equal(scored.actual_generation_mw,power.reindex(pd.to_datetime(scored.target_timestamp_utc,utc=True)))
    keys=[]
    for name,rows in scored.groupby('model'):
        error=rows.predicted_generation_mw.to_numpy()-rows.actual_generation_mw.to_numpy()
        n=len(error)
        expected=report['metrics'][name]
        assert math.isclose(math.fsum(abs(float(e)) for e in error)/n,expected['mae_mw'],rel_tol=1e-12)
        assert math.isclose(math.sqrt(math.fsum(float(e)**2 for e in error)/n),expected['rmse_mw'],rel_tol=1e-12)
        assert math.isclose(math.fsum(float(e) for e in error)/n,expected['bias_mw'],rel_tol=1e-12)
        keys.append(list(zip(rows.issue_at,rows.target_timestamp_utc)))
    assert all(k==keys[0] for k in keys)
    features,labels,_=prepare_training(observations,power,report['publication_delay_after_interval_end_minutes_assumed'])
    predictions=pd.read_csv(run/'predictions.csv')
    checked=[]
    for model in ML_MODELS:
        estimator=build_estimator(model)
        estimator.fit(features,labels)
        estimator.set_params(n_jobs=1)
        for day in [date(2025,1,1),date(2025,6,9)]:
            window=window_for_day(day,report['publication_delay_after_interval_end_minutes_assumed'])
            clock=pd.date_range(window.observation_origin-96*STEP,window.observation_origin,freq=STEP)
            values=power.reindex(clock).to_numpy().copy()
            expected=predictions[(predictions.model==model)&(predictions.delivery_date==day.isoformat())].predicted_generation_mw.to_numpy()
            actual=[]
            # Use the original pandas feature implementation, independently of
            # the new batched NumPy feature/rollout implementation.
            for target in window.trajectory:
                series=PreparedSeries(values,clock,15,96,1,0)
                row=features_at_origins(series,np.array([96]))
                value=max(0.0,float(estimator.predict(row)[0]))
                actual.append(value)
                values=np.append(values[1:],value)
                clock=clock[1:].append(pd.DatetimeIndex([target]))
            np.testing.assert_allclose(actual,expected,rtol=1e-10,atol=1e-7)
            checked.append({'model':model,'delivery_date':day.isoformat(),'points':len(actual)})
    return {'verified':True,'paired_scored_rows':len(scored),'single_origin_checks':checked,
            'reserved_test_scored':False,'input_sha256':report['input_sha256']}


if __name__=='__main__':
    print(json.dumps(verify(Path(sys.argv[1]),Path(sys.argv[2])),indent=2))
