from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.baselines import load_power
from app.dayahead_schedule import STEP, window_for_day
from app.forecasting import PreparedSeries,features_at_origins
from app.ml_validation import FIT_CUTOFF,ML_MODELS,MODELS,batch_features,prepare_training,recursive_batch,run_validation


def write_power(path,change=None):
    times = pd.date_range('2024-12-15T00:00Z','2025-01-08T00:00Z',freq=STEP)
    values = 100+np.arange(len(times))%96
    series = pd.Series(values.astype(float),index=times)
    if change:
        change(series)
    pd.DataFrame({'timestamp':times,'generation_mw':series.to_numpy()}).to_csv(path,index=False)


class Increment:
    def predict(self,rows):
        return rows.observed_at_origin.to_numpy()+1


class Stub(Increment):
    def fit(self,features,targets):
        self.features,self.targets = features.copy(),targets.copy()
        return self
    def get_params(self):
        return {'missing':float('nan'),'n_jobs':2}
    def set_params(self,**kwargs):
        return self


def test_batch_features_match_existing_training_features():
    rng = np.random.default_rng(42)
    contexts = rng.uniform(0,7000,(4,97))
    targets = pd.DatetimeIndex(['2025-01-01T00:00Z','2025-03-30T01:15Z','2025-10-26T01:15Z','2024-02-29T12:00Z'])
    actual = batch_features(contexts,targets)
    expected=[]
    for values,target in zip(contexts,targets):
        times = pd.date_range(target-97*STEP,periods=97,freq=STEP)
        series = PreparedSeries(values,times,15,96,1,0)
        expected.append(features_at_origins(series,np.array([96])))
    expected=pd.concat(expected,ignore_index=True)
    assert list(actual.columns)==list(expected.columns)
    np.testing.assert_allclose(actual,expected,rtol=1e-11,atol=1e-9)


def test_rollout_feedback_bridge_and_different_dst_lengths():
    windows=[window_for_day(day,15) for day in [date(2025,1,3),date(2025,3,30),date(2025,10,26)]]
    contexts=np.zeros((3,97))
    before=contexts.copy()
    result,failed,clipped=recursive_batch(Increment(),windows,contexts)
    assert not failed and clipped==0
    for window in windows:
        np.testing.assert_array_equal(result[window.delivery_date],np.arange(1,len(window.trajectory)+1))
        assert result[window.delivery_date][len(window.bridge)]==26
    np.testing.assert_array_equal(contexts,before)


def test_unstable_row_failure_does_not_discard_other_windows():
    windows=[window_for_day(date(2025,1,day),15) for day in [3,4]]
    class Bad:
        def predict(self,rows):
            return np.where(rows.observed_at_origin.to_numpy()>0,float('nan'),-1)
    context=np.zeros((2,97));context[1,-1]=1
    outputs,failed,clipped=recursive_batch(Bad(),windows,context)
    assert len(outputs)==1 and list(failed)==[date(2025,1,4)]
    assert clipped==121 and (outputs[date(2025,1,3)]==0).all()


def test_empty_cohort_is_valid():
    assert recursive_batch(Increment(),[],[])==({},{},0)


def test_training_ignores_all_2025_values_and_respects_cutoff(tmp_path):
    first,second=tmp_path/'a.csv',tmp_path/'b.csv'
    write_power(first)
    def change(series):
        series.loc[series.index.year==2025]=999999
    write_power(second,change)
    obs,power=load_power(first)
    x,y,audit=prepare_training(obs,power,15)
    obs2,power2=load_power(second)
    x2,y2,_=prepare_training(obs2,power2,15)
    pd.testing.assert_frame_equal(x,x2)
    np.testing.assert_array_equal(y,y2)
    selected=audit[audit.eligible]
    assert selected.target_available_at_assumed.max()<=FIT_CUTOFF
    assert selected.target.dt.year.eq(2024).all()
    assert len(x)==len(y)==len(selected)


def test_training_rejects_too_little_eligible_data(tmp_path):
    path=tmp_path/'input.csv'
    write_power(path,lambda series:series.mask(series.index.year==2024,inplace=True))
    obs,power=load_power(path)
    with pytest.raises(ValueError,match='192'):
        prepare_training(obs,power,15)


def test_full_pipeline_identical_scoring_keys_and_frozen_fits(tmp_path,monkeypatch):
    path=tmp_path/'input.csv';write_power(path)
    before=path.read_bytes()
    estimators=[]
    def factory(name):
        estimator=Stub();estimators.append(estimator);return estimator
    monkeypatch.setattr('app.ml_validation.build_estimator',factory)
    report=run_validation(path,tmp_path/'run',date(2025,1,3),date(2025,1,5),15)
    assert len(estimators)==3
    assert all(len(e.targets)==report['training_examples'] for e in estimators)
    assert report['common_scored_days']==2 and report['scored_intervals_per_model']==192
    scored=pd.read_csv(tmp_path/'run'/'scored-predictions.csv')
    assert len(scored)==5*192 and scored.role.eq('delivery').all()
    keys=[list(zip(group.issue_at,group.target_timestamp_utc)) for _,group in scored.groupby('model')]
    assert all(k==keys[0] for k in keys)
    assert not report['reserved_test_scored']
    assert path.read_bytes()==before


def test_missing_future_actuals_change_scores_not_issued_predictions(tmp_path,monkeypatch):
    monkeypatch.setattr('app.ml_validation.build_estimator',lambda name:Stub())
    for name in ['a','b']:
        path=tmp_path/f'{name}.csv'
        def change(series):
            series.loc[series.index>pd.Timestamp('2025-01-02T16:30Z')]=float('nan')
        write_power(path,change if name=='b' else None)
        report=run_validation(path,tmp_path/name,date(2025,1,3),date(2025,1,4),15)
        if name=='b':
            assert report['common_scored_days']==0 and all(v is None for v in report['metrics'].values())
    assert (tmp_path/'a'/'predictions.csv').read_bytes()==(tmp_path/'b'/'predictions.csv').read_bytes()


def test_missing_one_model_source_excludes_all_models_from_scores(tmp_path,monkeypatch):
    monkeypatch.setattr('app.ml_validation.build_estimator',lambda name:Stub())
    path=tmp_path/'input.csv'
    def change(series):
        series.loc[pd.Timestamp('2025-01-01T12:00Z')]=float('nan')
    write_power(path,change)
    report=run_validation(path,tmp_path/'run',date(2025,1,3),date(2025,1,4),15)
    assert report['common_scored_days']==0
    assert report['issued_windows_by_model']['seasonal_naive_48h']==0
    assert report['issued_windows_by_model']['random_forest']==1


@pytest.mark.parametrize('start,end',[(date(2024,12,31),date(2025,1,2)),(date(2025,9,30),date(2025,10,2))])
def test_reserved_test_guard_before_input_load(tmp_path,start,end):
    with pytest.raises(ValueError,match='reserved test is locked'):
        run_validation(tmp_path/'absent.csv',tmp_path/'run',start,end,15)
    assert not (tmp_path/'run').exists()


def test_real_estimator_smoke(tmp_path):
    path=tmp_path/'input.csv';write_power(path)
    report=run_validation(path,tmp_path/'run',date(2025,1,3),date(2025,1,4),15)
    assert report['common_scored_days']==1
    assert all(report['metrics'][model]['observations']==96 for model in MODELS)
