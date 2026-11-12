from datetime import date
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from app.dayahead_schedule import STEP, window_for_day
from app.ml_validation import FIT_CUTOFF
from app.weather_pair import PAIR, WEATHER_COLUMNS, load_weather, prepare_training, predict_pair, run, window_inputs


def inputs(day=date(2025,1,3)):
    window=window_for_day(day,15)
    times=pd.date_range(window.issue_at-pd.Timedelta(20,unit='D'),window.delivery_end_exclusive,freq=STEP)
    power=pd.Series(100+np.arange(len(times))%96,index=times,dtype=float)
    weather=pd.DataFrame(20.0,index=times,columns=WEATHER_COLUMNS)
    weather['weather_valid']=True
    weather['issue_at']=window.issue_at
    weather['latest_nominal_available_at_assumed']=window.issue_at-pd.Timedelta(1,unit='h')
    return window,power,weather


class Correction:
    def predict(self,frame):
        return frame['forecast_radiation_mean'].to_numpy() if 'forecast_radiation_mean' in frame else np.full(len(frame),-10.0)


def models():return {name:Correction() for name in PAIR}


def test_shared_features_and_no_future_production():
    window,power,weather=inputs()
    control,augmented,_,eligible,_=window_inputs(power,weather,window)
    assert eligible.all() and len(control.columns)==15 and len(augmented.columns)==24
    pd.testing.assert_frame_equal(control,augmented[control.columns])
    expected,_=predict_pair(models(),power,weather,window)
    power.loc[power.index>window.observation_origin]=np.nan
    actual,_=predict_pair(models(),power,weather,window)
    for name in expected:np.testing.assert_array_equal(actual[name],expected[name])


def test_weather_changes_only_weather_model_and_no_recursive_feedback():
    window,power,weather=inputs()
    first,_=predict_pair(models(),power,weather,window)
    weather.loc[window.targets[7],WEATHER_COLUMNS]=200.0
    second,_=predict_pair(models(),power,weather,window)
    np.testing.assert_array_equal(first[PAIR[0]],second[PAIR[0]])
    assert np.flatnonzero(first[PAIR[1]]!=second[PAIR[1]]).tolist()==[7]


@pytest.mark.parametrize('problem',['missing','late','wrong_issue','history','seasonal'])
def test_unavailable_inputs_reject_both_models(problem):
    window,power,weather=inputs()
    if problem=='missing':weather.loc[window.targets[0],WEATHER_COLUMNS[0]]=np.nan
    if problem=='late':weather.loc[window.targets[0],'latest_nominal_available_at_assumed']=window.issue_at+STEP
    if problem=='wrong_issue':weather.loc[window.targets[0],'issue_at']=window.issue_at-STEP
    if problem=='history':power.loc[window.observation_origin]=np.nan
    if problem=='seasonal':power.loc[window.targets[0]-pd.Timedelta(48,unit='h')]=np.nan
    with pytest.raises(ValueError,match='Incomplete paired'):predict_pair(models(),power,weather,window)


@pytest.mark.parametrize('day,count',[(date(2025,3,30),92),(date(2025,10,26),100)])
def test_direct_dst_delivery_shapes(day,count):
    window,power,weather=inputs(day)
    result,_=predict_pair(models(),power,weather,window)
    assert all(len(values)==count for values in result.values())


def test_residual_clipping_and_invalid_output():
    window,power,weather=inputs()
    class Constant:
        def __init__(self,value):self.value=value
        def predict(self,frame):return np.full(len(frame),self.value)
    result,_=predict_pair({name:Constant(-10000) for name in PAIR},power,weather,window)
    assert (result[PAIR[0]]==0).all()
    with pytest.raises(ValueError,match='Invalid residual'):
        predict_pair({name:Constant(np.nan) for name in PAIR},power,weather,window)


def test_training_is_matched_and_ignores_2025_labels():
    _,power,weather=inputs(date(2025,1,3))
    for day in pd.date_range('2024-12-14','2025-01-03'):
        window=window_for_day(day.date(),15)
        weather.loc[weather.index.intersection(window.targets),'issue_at']=window.issue_at
        weather.loc[weather.index.intersection(window.targets),'latest_nominal_available_at_assumed']=window.issue_at-STEP
    x,wx,y,audit=prepare_training(power,weather,15)
    pd.testing.assert_frame_equal(x,wx[x.columns])
    assert (audit.loc[audit.eligible,'label_available_at_assumed']<=FIT_CUTOFF).all()
    power.loc[power.index.year==2025]=999999
    x2,wx2,y2,_=prepare_training(power,weather,15)
    pd.testing.assert_frame_equal(x,x2);pd.testing.assert_frame_equal(wx,wx2)
    np.testing.assert_array_equal(y,y2)
    selected=audit.loc[audit.eligible,'target'].iloc[0]
    weather.loc[selected,WEATHER_COLUMNS[0]]=np.nan
    x3,wx3,y3,_=prepare_training(power,weather,15)
    assert len(x3)==len(wx3)==len(y3)==len(y)-1


def test_reserved_test_rejected_before_reading_files(tmp_path):
    with pytest.raises(ValueError,match='reserved test is locked'):
        run(tmp_path/'none',tmp_path,tmp_path/'output',date(2025,10,1),date(2025,10,2),15)


def test_weather_provenance_and_tamper_guards(tmp_path):
    _,_,weather=inputs()
    weather.index.name='timestamp'
    path=tmp_path/'weather-features.csv';weather.to_csv(path)
    report={'complete':True,'alignment_version':1,'model':'dwd_icon_eu','solar_input_sha256':'solar',
            'nominal_lead_hours':48,'allowance_minutes_assumed':370,'feature_columns':WEATHER_COLUMNS,
            'artifacts_sha256':{'weather-features.csv':hashlib.sha256(path.read_bytes()).hexdigest()}}
    (tmp_path/'summary.json').write_text(json.dumps(report))
    loaded,_=load_weather(tmp_path,'solar')
    assert len(loaded)==len(weather)
    with pytest.raises(ValueError,match='provenance'):load_weather(tmp_path,'other-solar')
    path.write_text('tampered')
    with pytest.raises(ValueError,match='checksum'):load_weather(tmp_path,'solar')
    report['artifacts_sha256']={}
    (tmp_path/'summary.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='checksum is required'):load_weather(tmp_path,'solar')
