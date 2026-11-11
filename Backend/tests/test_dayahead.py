from datetime import date, timedelta
import csv
import json

import pandas as pd
import pytest

from app.dayahead import export_plan, main
from app.dayahead_schedule import STEP, window_for_day


@pytest.mark.parametrize('day,targets,steps,last_issue_lead,last_origin_lead', [
    (date(2025,1,15),96,121,1785,1815),
    (date(2025,3,30),92,117,1725,1755),
    (date(2025,10,26),100,125,1845,1875),
])
def test_complete_trajectory_and_true_leads(day, targets, steps, last_issue_lead, last_origin_lead):
    window = window_for_day(day,15)
    metadata = window.metadata()
    assert metadata['delivery_intervals'] == targets
    assert metadata['recursive_steps'] == steps
    assert metadata['bridge_intervals'] == 25
    assert metadata['last_delivery_lead_from_issue_minutes'] == last_issue_lead
    assert metadata['last_delivery_lead_from_origin_minutes'] == last_origin_lead
    assert metadata['first_delivery_lead_from_issue_minutes'] == 360
    assert window.issue_at.tz_convert('Europe/Brussels').hour == 18
    assert window.issue_at.tz_convert('Europe/Brussels').date() == day-timedelta(days=1)
    assert window.observation_origin.tz_convert('Europe/Brussels').strftime('%H:%M') == '17:30'
    assert window.trajectory.equals(window.bridge.append(window.targets))
    assert not window.trajectory.has_duplicates
    assert all(window.trajectory[1:]-window.trajectory[:-1] == STEP)
    assert window.trajectory[-1]+STEP == window.delivery_end_exclusive
    assert all(window.targets.tz_convert('Europe/Brussels').date == day)


def test_autumn_repeated_hour_retains_offset_and_unique_utc_keys():
    points = list(window_for_day(date(2025,10,26),15).points())
    labels = [p['target_timestamp_local'] for p in points]
    assert '2025-10-26T02:00:00+02:00' in labels
    assert '2025-10-26T02:00:00+01:00' in labels
    assert len({p['target_timestamp_utc'] for p in points}) == len(points)
    assert len(set(labels)) == len(labels)


def test_spring_has_no_invented_local_hour():
    points = list(window_for_day(date(2025,3,30),15).points())
    assert not any(p['target_timestamp_local'].startswith('2025-03-30T02:') for p in points)


@pytest.mark.parametrize('delay,bridge', [(0,24),(7,25),(15,25),(60,28)])
def test_bridge_publication_gap_and_role_separation(delay, bridge):
    window=window_for_day(date(2025,1,15),delay)
    points=list(window.points())
    assert len(window.bridge) == bridge
    assert [p['forecast_step'] for p in points] == list(range(1,len(points)+1))
    assert all(p['role']=='bridge' for p in points[:bridge])
    assert all(p['role']=='delivery' for p in points[bridge:])
    assert pd.Timestamp(points[0]['target_timestamp_utc']) == window.observation_origin+STEP
    assert pd.Timestamp(points[bridge]['target_timestamp_utc']) == window.delivery_start
    if delay==15:
        assert points[0]['lead_from_issue_minutes'] == -15
        assert points[0]['lead_from_origin_minutes'] == 15
        assert points[bridge]['lead_from_origin_minutes'] == 390


@pytest.mark.parametrize('day', [date(2024,2,29),date(2025,1,1),date(2025,12,31)])
def test_leap_day_and_year_boundaries(day):
    window=window_for_day(day,15)
    assert len(window.targets)==96
    assert window.targets[0].tz_convert('Europe/Brussels').date()==day
    assert window.delivery_end_exclusive.tz_convert('Europe/Brussels').date()==day+timedelta(days=1)


def write_history(path, missing_target=False, missing_history=False, future_only=False):
    times = pd.date_range('2024-01-01T00:00Z',periods=4*96,freq=STEP)
    frame = pd.DataFrame({'timestamp':times,'generation_mw':0.0})
    if missing_target:
        frame.loc[frame.timestamp==pd.Timestamp('2024-01-03T22:45Z'),'generation_mw']=float('nan')
    if missing_history:
        frame.loc[frame.timestamp==pd.Timestamp('2024-01-01T22:45Z'),'generation_mw']=float('nan')
    if future_only:
        frame = frame[frame.timestamp <= pd.Timestamp('2024-01-02T16:30Z')]
    frame.to_csv(path,index=False)


def test_planning_is_not_filtered_by_future_target_completeness(tmp_path):
    source=tmp_path/'input.csv'
    write_history(source,missing_target=True)
    original=source.read_bytes()
    report=export_plan(source,tmp_path/'plan',date(2024,1,3),date(2024,1,4),15,'history_ml')
    assert report['counts']['planned_windows']==1
    assert report['counts']['planned_windows_with_complete_targets']==0
    assert report['counts']['delivery_intervals']==96
    points=pd.read_csv(tmp_path/'plan'/'trajectory.csv')
    assert len(points)==121
    assert not any('generation' in name for name in points.columns)
    assert source.read_bytes()==original
    windows=pd.read_csv(tmp_path/'plan'/'windows.csv')
    assert windows.iloc[0].input_eligible
    assert 'incomplete_delivery_targets' in windows.iloc[0].exclusion_reasons
    assert windows.iloc[0].plan_status=='planned'


def test_future_day_plans_without_any_future_actuals(tmp_path):
    source=tmp_path/'input.csv'
    write_history(source,future_only=True)
    report=export_plan(source,tmp_path/'plan',date(2024,1,3),date(2024,1,4),15,'history_ml')
    assert report['counts']['planned_windows']==1
    assert report['counts']['planned_windows_with_complete_targets']==0
    windows=pd.read_csv(tmp_path/'plan'/'windows.csv')
    assert windows.iloc[0].valid_targets==0


def test_profiles_and_empty_export_have_explicit_exclusions(tmp_path):
    source=tmp_path/'input.csv'
    write_history(source,missing_history=True)
    ml=export_plan(source,tmp_path/'ml',date(2024,1,3),date(2024,1,4),15,'history_ml')
    persistence=export_plan(source,tmp_path/'persistence',date(2024,1,3),date(2024,1,4),15,'persistence')
    assert ml['counts']['planned_windows']==0
    assert ml['counts']['excluded_input_windows']==1
    assert persistence['counts']['planned_windows']==1
    with (tmp_path/'ml'/'trajectory.csv').open() as handle:
        reader=csv.DictReader(handle)
        assert reader.fieldnames and list(reader)==[]
    excluded=pd.read_csv(tmp_path/'ml'/'windows.csv').iloc[0]
    assert excluded.plan_exclusion_reasons=='invalid_or_missing_history'
    included=pd.read_csv(tmp_path/'persistence'/'windows.csv').iloc[0]
    assert pd.isna(included.plan_exclusion_reasons)


def test_reproducible_outputs_and_no_overwrite(tmp_path):
    source=tmp_path/'input.csv'
    write_history(source)
    for name in ['first','second']:
        export_plan(source,tmp_path/name,date(2024,1,3),date(2024,1,4),15,'history_ml')
    for filename in ['summary.json','windows.csv','trajectory.csv']:
        assert (tmp_path/'first'/filename).read_bytes()==(tmp_path/'second'/filename).read_bytes()
    with pytest.raises(FileExistsError):
        export_plan(source,tmp_path/'first',date(2024,1,3),date(2024,1,4),15,'history_ml')


def test_multiple_issues_keep_overlapping_target_events_separate(tmp_path):
    source=tmp_path/'input.csv'
    write_history(source)
    export_plan(source,tmp_path/'plan',date(2024,1,3),date(2024,1,5),15,'history_ml')
    points=pd.read_csv(tmp_path/'plan'/'trajectory.csv')
    assert len(points)==242
    assert not points.duplicated(['issue_at','target_timestamp_utc']).any()
    assert points.target_timestamp_utc.duplicated().sum()==25
    delivery=points[points.role=='delivery']
    assert len(delivery)==192 and not delivery.target_timestamp_utc.duplicated().any()


@pytest.mark.parametrize('delay', [-1,True,1.5])
def test_invalid_delay_rejected(delay):
    with pytest.raises(ValueError):
        window_for_day(date(2025,1,1),delay)


def test_cli(tmp_path):
    source=tmp_path/'input.csv'
    write_history(source)
    assert main(['--input',str(source),'--output',str(tmp_path/'plan'),'--start','2024-01-03',
                 '--end','2024-01-04','--availability-delay-minutes','15'])==0
    assert json.loads((tmp_path/'plan'/'summary.json').read_bytes())['complete']
