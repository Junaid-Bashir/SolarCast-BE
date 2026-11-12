import copy
import importlib.util
from datetime import date,datetime,timedelta,timezone
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('weather_audit',Path(__file__).resolve().parents[1]/'research/audit_weather_source.py')
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def payload(day):
    first=datetime.combine(day-timedelta(days=1),datetime.min.time(),timezone.utc)
    labels=[(first+i*timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M') for i in range(48)]
    return [{'latitude':lat,'longitude':lon,'location_id':index,'timezone':'GMT','utc_offset_seconds':0,
             'hourly_units':audit.VARIABLES.copy(),
             'hourly':{'time':list(labels),**{key:[0.0]*48 for key in audit.VARIABLES}}}
            for index,(_,lat,lon) in enumerate(audit.LOCATIONS)]


@pytest.mark.parametrize('day,hours,quarters,margin',[(date(2025,1,15),25,96,710),
    (date(2025,3,30),24,92,770),(date(2024,10,27),26,100,650)])
def test_required_weather_bounds_dst_and_conservative_timing(day,hours,quarters,margin):
    result=audit.audit_payload(payload(day),day)
    assert result['passed']
    assert result['required_hour_boundaries']==hours
    assert result['delivery_quarter_hours']==quarters
    assert result['assumed_deadline_margin_minutes']==margin
    assert result['nominal_rule_passed'] and not result['publication_times_verified']
    assert len(set(audit.required_hours(day)))==hours


def test_missing_forecast_and_zero_radiation_are_distinct():
    day=date(2025,1,15)
    raw=payload(day)
    raw[0]['hourly']['shortwave_radiation_previous_day2'][24]=None
    before=copy.deepcopy(raw)
    result=audit.audit_payload(raw,day)
    assert not result['passed'] and result['locations'][1]['passed']
    assert result['locations'][0]['variables']['shortwave_radiation_previous_day2']['missing_or_nonfinite']==1
    assert raw==before


def test_out_of_range_cloud_is_flagged_without_clipping():
    day=date(2025,1,15);raw=payload(day)
    raw[0]['hourly']['cloud_cover_previous_day2'][24]=101
    result=audit.audit_payload(raw,day)
    assert not result['passed']
    assert result['locations'][0]['variables']['cloud_cover_previous_day2']['invalid_range']==1
    assert raw[0]['hourly']['cloud_cover_previous_day2'][24]==101


def test_bad_units_clock_and_location_are_not_accepted():
    day=date(2025,1,15);raw=payload(day)
    raw[0]['hourly_units']['temperature_2m_previous_day2']='K'
    raw[1]['hourly']['time'][1]=raw[1]['hourly']['time'][0]
    raw[2]['location_id']=0
    result=audit.audit_payload(raw,day)
    assert not any(r['passed'] for r in result['locations'][:3])
    assert all(r['passed'] for r in result['locations'][3:])


def test_api_error_is_not_a_successful_coverage_result():
    result=audit.audit_payload({'error':True,'reason':'model unavailable'},date(2025,1,15))
    assert not result['passed'] and result['locations']==[]
