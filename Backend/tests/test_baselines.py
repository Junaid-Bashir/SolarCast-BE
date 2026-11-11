from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.baselines import BaselineUnavailable, MODELS, metrics, predict_baseline, run_validation, skill
from app.dayahead_schedule import STEP, window_for_day


def power_series():
    times=pd.date_range('2024-12-20T00:00Z', '2025-01-10T00:00Z',freq=STEP)
    return pd.Series(np.arange(len(times),dtype=float),index=times)


def save_source(path, series=None):
    values=power_series() if series is None else series
    pd.DataFrame({'timestamp':values.index,'generation_mw':values.to_numpy()}).to_csv(path,index=False)


def test_persistence_uses_latest_admissible_observation():
    power=power_series()
    window=window_for_day(date(2025,1,3),15)
    result=predict_baseline(window,power,'persistence')
    assert len(result.predicted)==121
    assert (result.predicted==power.loc[window.observation_origin]).all()
    assert (result.sources==window.observation_origin).all()


def test_seasonal_lag_is_exactly_48_elapsed_hours():
    power=power_series()
    window=window_for_day(date(2025,1,3),15)
    result=predict_baseline(window,power,'seasonal_naive_48h')
    assert (result.targets-result.sources==pd.Timedelta(172_800_000_000_000,unit='ns')).all()
    np.testing.assert_array_equal(result.predicted,power.reindex(result.sources))
    assert (result.sources+2*STEP <= window.issue_at).all()


@pytest.mark.parametrize('model',MODELS)
def test_every_post_issue_actual_can_change_without_changing_predictions(model):
    power=power_series()
    window=window_for_day(date(2025,1,3),15)
    before=power.copy()
    expected=predict_baseline(window,power,model)
    changed=power.copy()
    changed.loc[changed.index>window.observation_origin]=float('nan')
    actual=predict_baseline(window,changed,model)
    np.testing.assert_array_equal(expected.predicted,actual.predicted)
    pd.testing.assert_series_equal(power,before)


def test_missing_source_has_no_older_value_fallback():
    power=power_series()
    window=window_for_day(date(2025,1,3),15)
    selected=window.targets[0]-pd.Timedelta(172_800_000_000_000,unit='ns')
    power.loc[selected]=float('nan')
    with pytest.raises(BaselineUnavailable,match='missing_or_invalid_source'):
        predict_baseline(window,power,'seasonal_naive_48h')
    assert np.isnan(power.loc[selected])


def test_even_bridge_source_gaps_are_explicitly_ineligible():
    power=power_series()
    window=window_for_day(date(2025,1,3),15)
    power.loc[window.bridge[0]-pd.Timedelta(172_800_000_000_000,unit='ns')]=float('nan')
    with pytest.raises(BaselineUnavailable):
        predict_baseline(window,power,'seasonal_naive_48h')


def test_unavailable_seasonal_source_is_not_used_under_large_delay():
    window=window_for_day(date(2025,1,3),1440)
    with pytest.raises(BaselineUnavailable,match='source_unavailable_at_issue'):
        predict_baseline(window,power_series(),'seasonal_naive_48h')


def test_metrics_and_undefined_skill():
    result=metrics([0,2],[1,0])
    assert result['mae_mw']==1.5
    assert result['rmse_mw']==pytest.approx(np.sqrt(2.5))
    assert result['bias_mw']==-.5
    assert skill(1,2)==50
    assert skill(0,0) is None
    for actual,pred in [([],[]),([1],[1,2]),([float('nan')],[0])]:
        with pytest.raises(ValueError):
            metrics(actual,pred)


def test_paired_complete_windows_and_delivery_only_scoring(tmp_path):
    path=tmp_path/'input.csv'
    save_source(path)
    original=path.read_bytes()
    report=run_validation(path,tmp_path/'run',date(2025,1,3),date(2025,1,5),15)
    assert report['common_scored_days']==2
    assert report['scored_delivery_intervals_per_model']==192
    scored=pd.read_csv(tmp_path/'run'/'scored-predictions.csv')
    assert len(scored)==384 and (scored.role=='delivery').all()
    keys=[]
    for model in MODELS:
        subset=scored[scored.model==model]
        keys.append(list(zip(subset.issue_at,subset.target_timestamp_utc)))
        assert report['metrics'][model]=={**metrics(subset.actual_generation_mw,subset.predicted_generation_mw),
            'rmse_skill_vs_persistence_pct':report['metrics'][model]['rmse_skill_vs_persistence_pct']}
    assert keys[0]==keys[1]
    assert path.read_bytes()==original
    predictions=pd.read_csv(tmp_path/'run'/'predictions.csv')
    assert 'actual_generation_mw' not in predictions
    assert (pd.to_datetime(predictions.source_available_at_assumed,utc=True)<=pd.to_datetime(predictions.issue_at,utc=True)).all()


def test_missing_actual_excludes_whole_day_but_forecasts_still_issue(tmp_path):
    source=power_series()
    source.loc[pd.Timestamp('2025-01-03T12:00Z')]=float('nan')
    path=tmp_path/'input.csv'
    save_source(path,source)
    report=run_validation(path,tmp_path/'run',date(2025,1,3),date(2025,1,5),15)
    assert report['common_scored_days']==1 and report['excluded_days']==1
    assert report['issued_windows_by_model']=={model:2 for model in MODELS}
    predictions=pd.read_csv(tmp_path/'run'/'predictions.csv')
    assert len(predictions)==484
    scored=pd.read_csv(tmp_path/'run'/'scored-predictions.csv')
    assert set(scored.delivery_date)=={'2025-01-04'}


def test_missing_one_model_input_excludes_both_from_common_scores(tmp_path):
    source=power_series()
    source.loc[pd.Timestamp('2025-01-01T12:00Z')]=float('nan')
    path=tmp_path/'input.csv'
    save_source(path,source)
    report=run_validation(path,tmp_path/'run',date(2025,1,3),date(2025,1,5),15)
    assert report['common_scored_days']==1
    assert report['issued_windows_by_model']=={'persistence':2,'seasonal_naive_48h':1}
    for model in MODELS:
        assert report['metrics'][model]['observations']==96


def test_zero_error_and_no_scoreable_days_are_json_safe(tmp_path):
    source=power_series()*0
    path=tmp_path/'input.csv'
    save_source(path,source)
    report=run_validation(path,tmp_path/'zeros',date(2025,1,3),date(2025,1,4),15)
    assert all(report['metrics'][model]['rmse_skill_vs_persistence_pct'] is None for model in MODELS)
    source.loc[pd.Timestamp('2025-01-03T12:00Z')]=float('nan')
    save_source(path,source)
    report=run_validation(path,tmp_path/'none',date(2025,1,3),date(2025,1,4),15)
    assert report['metrics']=={model:None for model in MODELS}
    assert report['common_scored_days']==0
    assert pd.read_csv(tmp_path/'none'/'scored-predictions.csv').empty


@pytest.mark.parametrize('start,end', [(date(2024,12,31),date(2025,1,2)),
                                     (date(2025,9,30),date(2025,10,2))])
def test_reserved_and_outside_validation_ranges_rejected_before_loading(tmp_path,start,end):
    with pytest.raises(ValueError,match='reserved test is locked'):
        run_validation(tmp_path/'absent.csv',tmp_path/'run',start,end,15)
    assert not (tmp_path/'run').exists()


def test_dst_spring_scoring_counts_all_92_intervals(tmp_path):
    times=pd.date_range('2025-03-25T00:00Z','2025-03-31T00:00Z',freq=STEP)
    path=tmp_path/'input.csv'
    save_source(path,pd.Series(0.0,index=times))
    report=run_validation(path,tmp_path/'run',date(2025,3,30),date(2025,3,31),15)
    assert report['scored_delivery_intervals_per_model']==92
    assert all(report['metrics'][model]['observations']==92 for model in MODELS)


def test_elapsed_seasonality_is_not_silently_local_clock_aligned():
    times=pd.date_range('2025-03-25T00:00Z','2025-03-31T00:00Z',freq=STEP)
    power=pd.Series(np.arange(len(times),dtype=float),index=times)
    result=predict_baseline(window_for_day(date(2025,3,30),15),power,'seasonal_naive_48h')
    index=result.targets.get_loc(pd.Timestamp('2025-03-30T01:00Z'))
    assert result.targets[index].tz_convert('Europe/Brussels').strftime('%H:%M')=='03:00'
    assert result.sources[index].tz_convert('Europe/Brussels').strftime('%H:%M')=='02:00'


def test_reproducible_artifacts_and_overwrite_refused(tmp_path):
    path=tmp_path/'input.csv'
    save_source(path)
    for name in ['a','b']:
        run_validation(path,tmp_path/name,date(2025,1,3),date(2025,1,4),15)
    for file in (tmp_path/'a').iterdir():
        assert file.read_bytes()==(tmp_path/'b'/file.name).read_bytes()
    with pytest.raises(FileExistsError):
        run_validation(path,tmp_path/'a',date(2025,1,3),date(2025,1,4),15)
