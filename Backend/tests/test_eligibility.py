from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.eligibility import (
    STEP, Observations, delay_delta, forecast_windows, load_observations,
    main, targets_for_day, training_examples,
)


def series(start='2024-01-01T00:00Z', days=5):
    times = pd.date_range(start, periods=days*96, freq=STEP)
    return Observations(times, np.ones(len(times), dtype=bool), len(times), 0, 0, 0, 'fixture')


def test_context_matches_actual_feature_dependencies():
    from app.forecasting import PreparedSeries, features_at_origins
    data = series()
    origin = 200
    for gap in range(origin-97, origin+2):
        values = np.zeros(len(data.times))
        values[gap] = np.nan
        data.valid[:] = np.isfinite(values)
        actual = features_at_origins(PreparedSeries(values, data.times, 15, 96, 1, 300), np.array([origin]))
        context, extent = data.context(data.times[[origin]])
        assert extent[0]
        assert bool(context[0]) == bool(actual.notna().all(axis=None)), gap


def test_gap_invalidates_exactly_97_origins():
    data = series()
    data.valid[200] = False
    valid, extent = data.context(data.times)
    rejected = np.flatnonzero(extent & ~valid)
    assert rejected.tolist() == list(range(200, 297))
    assert valid[297]


def test_label_availability_boundary_and_no_input_mutation():
    data = series()
    before = data.valid.copy()
    result = training_examples(data, date(2024, 1, 2), date(2024, 1, 3),
                               '2024-01-02T01:30Z', 15)
    at_boundary = result[result.target == pd.Timestamp('2024-01-02T01:00Z')].iloc[0]
    after = result[result.target == pd.Timestamp('2024-01-02T01:15Z')].iloc[0]
    assert at_boundary.eligible
    assert not after.eligible
    assert after.exclusion_reasons == 'label_unavailable_by_fit_cutoff'
    assert np.array_equal(data.valid, before)


def test_target_gap_and_history_gap_are_separate():
    data = series()
    label = pd.Timestamp('2024-01-02T12:00Z')
    data.valid[data.times.get_loc(label)] = False
    result = training_examples(data, date(2024, 1, 2), date(2024, 1, 3),
                               '2024-01-04T00:00Z', 0)
    row = result[result.target == label].iloc[0]
    assert row.complete_history and not row.valid_target
    assert row.exclusion_reasons == 'invalid_or_missing_target'
    next_row = result[result.target == label+STEP].iloc[0]
    assert next_row.valid_target and not next_row.complete_history
    assert next_row.exclusion_reasons == 'invalid_or_missing_history'


@pytest.mark.parametrize('day,count', [(date(2025,3,30),92), (date(2025,10,26),100), (date(2025,6,1),96)])
def test_delivery_day_dst(day, count):
    targets = targets_for_day(day)
    assert len(targets) == count
    assert not targets.has_duplicates
    assert all(np.diff(targets.asi8) == STEP.value)
    assert targets.tz_convert('Europe/Brussels').date.tolist() == [day]*count


@pytest.mark.parametrize('day,count', [(date(2025,3,30),92),(date(2025,10,26),100)])
def test_dst_window_has_complete_inputs_and_all_targets(day, count):
    start = str(day-timedelta(days=2))+'T00:00Z'
    data = series(start=start, days=5)
    result = forecast_windows(data, day, day+timedelta(days=1), 15).iloc[0]
    assert result.expected_targets == count and result.valid_targets == count
    assert result.common_scoreable_window
    assert result.issue_at.tz_convert('Europe/Brussels').hour == 18


@pytest.mark.parametrize('delay,expected', [(0,'2024-01-02T16:45Z'),(15,'2024-01-02T16:30Z'),
                                         (7,'2024-01-02T16:30Z'),(60,'2024-01-02T15:45Z')])
def test_only_completed_available_intervals_at_issue(delay, expected):
    data = series()
    result = forecast_windows(data, date(2024,1,3), date(2024,1,4), delay).iloc[0]
    assert result.issue_at == pd.Timestamp('2024-01-02T17:00Z')
    assert result.latest_observation_origin == pd.Timestamp(expected)
    assert result.latest_observation_origin+STEP+delay_delta(delay) <= result.issue_at
    assert result.latest_observation_origin+2*STEP+delay_delta(delay) > result.issue_at


def test_future_targets_do_not_determine_issuability():
    data = series()
    first = forecast_windows(data, date(2024,1,3), date(2024,1,4), 15).iloc[0]
    data.valid[data.times.get_loc(pd.Timestamp('2024-01-03T12:00Z'))] = False
    second = forecast_windows(data, date(2024,1,3), date(2024,1,4), 15).iloc[0]
    assert first.common_scoreable_window
    assert second.history_ml_issuable and second.persistence_issuable
    assert not second.common_scoreable_window
    assert second.valid_targets == 95


def test_persistence_can_issue_when_ml_cannot():
    data = series()
    data.valid[data.times.get_loc(pd.Timestamp('2024-01-01T22:45Z'))] = False
    result = forecast_windows(data, date(2024,1,3), date(2024,1,4), 15).iloc[0]
    assert result.persistence_issuable and result.persistence_scoreable_window
    assert not result.history_ml_issuable
    assert result.exclusion_reasons == 'invalid_or_missing_history'


def test_csv_missing_timestamp_is_false_membership_not_filled(tmp_path):
    path = tmp_path/'input.csv'
    body = b'timestamp,generation_mw\n2024-01-01T00:00Z,0\n2024-01-01T00:30Z,\n2024-01-01T00:45Z,-1\n2024-01-01T01:00Z,inf\n'
    path.write_bytes(body)
    data = load_observations(path)
    assert data.input_rows == 4 and data.absent_intervals == 1
    assert data.valid.tolist() == [True,False,False,False,False]
    assert data.missing_values == 1 and data.invalid_values == 2
    assert path.read_bytes() == body


@pytest.mark.parametrize('stamps', [
    ['2024-01-01T00:00','2024-01-01T00:15'],
    ['2024-01-01T00:00Z','2024-01-01T00:00Z'],
    ['2024-01-01T00:15Z','2024-01-01T00:00Z'],
    ['2024-01-01T00:01Z','2024-01-01T00:15Z'],
])
def test_ambiguous_duplicate_unordered_or_off_grid_csv_rejected(tmp_path, stamps):
    path = tmp_path/'input.csv'
    path.write_text('timestamp,generation_mw\n'+'\n'.join(t+',0' for t in stamps))
    with pytest.raises(ValueError):
        load_observations(path)


def test_policy_requires_explicit_delay_and_aware_fit_cutoff():
    data = series()
    with pytest.raises(ValueError):
        forecast_windows(data,date(2024,1,2),date(2024,1,3),-1)
    with pytest.raises(ValueError):
        training_examples(data,date(2024,1,2),date(2024,1,3),'2024-01-03T18:00',15)


def test_cli_writes_masks_not_values_and_refuses_overwrite(tmp_path):
    path = tmp_path/'input.csv'
    times = pd.date_range('2024-01-01T00:00Z', periods=3*96, freq=STEP)
    pd.DataFrame({'timestamp':times,'generation_mw':0}).to_csv(path,index=False)
    before = path.read_bytes()
    args = ['--input',str(path),'--output',str(tmp_path/'audit'), '--availability-delay-minutes','15',
            '--fit-cutoff','2024-01-03T18:00Z', '--training-start','2024-01-01', '--training-end','2024-01-03',
            '--window-start','2024-01-01','--window-end','2024-01-04']
    assert main(args) == 0
    assert path.read_bytes() == before
    assert 'generation_mw' not in (tmp_path/'audit'/'training-eligibility.csv').read_text()
    with pytest.raises(SystemExit):
        main(args)
