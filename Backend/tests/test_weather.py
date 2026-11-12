from datetime import date
import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit

import numpy as np
import pandas as pd
import pytest

from app import weather
from app.dayahead_schedule import STEP,local_midnight


def response(first,last):
    times=pd.date_range(first,last,freq=weather.HOUR,inclusive='left')
    output=[]
    for index,point in enumerate(weather.config()['locations']):
        output.append({'latitude':point['latitude'],'longitude':point['longitude'],'elevation':10,
            'location_id':index,'timezone':'GMT','utc_offset_seconds':0,
            'hourly_units':{name:unit for name,unit in weather.VARIABLES.values()},
            'hourly':{'time':[t.strftime('%Y-%m-%dT%H:%M') for t in times],
                      'shortwave_radiation_previous_day2':[100.0]*len(times),
                      'cloud_cover_previous_day2':[30.0]*len(times),
                      'temperature_2m_previous_day2':[10.0]*len(times)}})
    return output


def archive(tmp_path,start=date(2025,1,3),end=date(2025,1,4),change=None):
    def getter(url):
        query=parse_qs(urlsplit(url).query)
        first=pd.Timestamp(query['start_date'][0],tz='UTC')
        last=pd.Timestamp(query['end_date'][0],tz='UTC')+pd.Timedelta(86_400_000_000_000,unit='ns')
        payload=response(first,last)
        if change:change(payload)
        return weather.encoded(payload)
    root=tmp_path/'archive'
    weather.download(start,end,root,getter=getter,sleep=lambda _:None,progress=lambda _:None)
    return root


def normalized(tmp_path,**kwargs):
    root=archive(tmp_path,**kwargs)
    output=tmp_path/'hourly'
    weather.rebuild(root,output)
    return root,output


def test_rebuild_offline_integrity_and_determinism(tmp_path):
    root,output=normalized(tmp_path)
    weather.rebuild(root,tmp_path/'repeat')
    for name in ['hourly.csv','grid.csv','summary.json']:
        assert (output/name).read_bytes()==(tmp_path/'repeat'/name).read_bytes()
    raw=next((root/'raw').glob('*.json'))
    raw.write_bytes(raw.read_bytes()+b' ')
    with pytest.raises(ValueError,match='checksum'):
        weather.rebuild(root,tmp_path/'bad')


def test_clock_units_and_grid_changes_are_not_silently_accepted(tmp_path):
    first=pd.Timestamp('2025-01-01T00:00Z');last=first+2*weather.HOUR
    payload=response(first,last)
    payload[0]['hourly']['time'][1]=payload[0]['hourly']['time'][0]
    with pytest.raises(ValueError,match='clock'):
        weather.parse_response(payload,first,last,weather.config()['locations'])
    payload=response(first,last);payload[0]['hourly_units']['temperature_2m_previous_day2']='K'
    with pytest.raises(ValueError,match='unit'):
        weather.parse_response(payload,first,last,weather.config()['locations'])
    payload=response(first,last);payload[0]['latitude']=0
    with pytest.raises(ValueError,match='grid'):
        weather.parse_response(payload,first,last,weather.config()['locations'])


def test_missing_and_out_of_range_values_remain_visible(tmp_path):
    def change(payload):
        payload[0]['hourly']['shortwave_radiation_previous_day2'][1]=None
        payload[0]['hourly']['cloud_cover_previous_day2'][1]=101
    _,out=normalized(tmp_path,change=change)
    report=json.loads((out/'summary.json').read_bytes())
    assert report['variable_quality']['radiation']['missing_or_nonfinite']==1
    assert report['variable_quality']['cloud']['invalid_range']==1
    frame=pd.read_csv(out/'hourly.csv')
    assert frame.cloud.max()==101 and not report['all_hourly_values_valid']


def test_midpoint_interpolation_radiation_hour_alignment_and_energy():
    first=pd.Timestamp('2025-01-01T00:00Z');last=first+3*weather.HOUR
    frames,_=weather.parse_response(response(first,last),first,last,weather.config()['locations'])
    data=pd.concat(frames)
    data.loc[data.timestamp==first,'temperature']=0
    data.loc[data.timestamp==first+weather.HOUR,'temperature']=8
    data.loc[data.timestamp==first+weather.HOUR,'radiation']=200
    targets=pd.date_range(first,periods=4,freq=STEP)
    result=weather.align_features(data,targets,[r['name'] for r in weather.config()['locations']])
    np.testing.assert_allclose(result.forecast_temperature_mean,[1,3,5,7])
    np.testing.assert_allclose(result.forecast_radiation_mean,[200]*4)
    assert (result.forecast_radiation_mean*.25).sum()==200
    assert result.weather_valid.all()


def test_no_interpolation_across_missing_hour_or_partial_spatial_averages():
    first=pd.Timestamp('2025-01-01T00:00Z');last=first+3*weather.HOUR
    frames,_=weather.parse_response(response(first,last),first,last,weather.config()['locations'])
    data=pd.concat(frames)
    data=data[~((data.location=='brussels')&(data.timestamp==first+weather.HOUR))]
    result=weather.align_features(data,pd.date_range(first,periods=4,freq=STEP),[r['name'] for r in weather.config()['locations']])
    assert not result.weather_valid.any()
    assert result.forecast_radiation_mean.isna().all()
    assert result.forecast_temperature_mean.isna().all()
    assert result.radiation_valid_locations.eq(10).all()


@pytest.mark.parametrize('day,count',[(date(2024,3,31),92),(date(2024,10,27),100)])
def test_alignment_dst_and_missing_solar_preserved(tmp_path,day,count):
    end=day+pd.Timedelta(86_400_000_000_000,unit='ns').to_pytimedelta()
    _,out=normalized(tmp_path,start=day,end=end)
    times=pd.date_range(local_midnight(day),local_midnight(end),freq=STEP,inclusive='left')
    source=tmp_path/'solar.csv'
    values=np.zeros(len(times));values[2]=np.nan
    pd.DataFrame({'timestamp':times,'generation_mw':values}).to_csv(source,index=False)
    original=source.read_bytes()
    report=weather.align(out,source,tmp_path/'joined')
    assert report['targets']==count and report['solar_missing_or_invalid']==1
    assert report['joint_valid']==count-1
    joined=pd.read_csv(tmp_path/'joined'/'solar-aligned.csv')
    assert joined.target_generation_mw.isna().sum()==1
    assert len(set(joined.timestamp))==count
    assert source.read_bytes()==original
    assert (pd.to_datetime(joined.latest_nominal_available_at_assumed,utc=True)<=pd.to_datetime(joined.issue_at,utc=True)).all()


def test_reserved_range_and_overwrite_rejected(tmp_path):
    with pytest.raises(ValueError,match='reserved test'):
        weather.download(date(2025,9,30),date(2025,10,2),tmp_path/'bad')
    root=archive(tmp_path)
    with pytest.raises(FileExistsError):
        weather.download(date(2025,1,3),date(2025,1,4),root,getter=lambda _:b'[]')


def test_query_and_coverage_manifest_mutations_rejected(tmp_path):
    root=archive(tmp_path)
    manifest=json.loads((root/'manifest.json').read_bytes())
    manifest['responses'][0]['url']=manifest['responses'][0]['url'].replace('dwd_icon_eu','gfs_global')
    (root/'manifest.json').write_bytes(weather.encoded(manifest))
    with pytest.raises(ValueError,match='query'):
        weather.rebuild(root,tmp_path/'bad')


def test_registry_line_endings_do_not_change_semantics_or_hash(tmp_path,monkeypatch):
    root,out=normalized(tmp_path)
    original=weather.CONFIG.read_bytes().replace(b'\r\n',b'\n')
    registry=tmp_path/'registry.json';registry.write_bytes(original.replace(b'\n',b'\r\n'))
    monkeypatch.setattr(weather,'CONFIG',registry)
    weather.rebuild(root,tmp_path/'repeat')
    assert (out/'summary.json').read_bytes()==(tmp_path/'repeat'/'summary.json').read_bytes()


def test_http_errors_are_saved_without_marking_archive_complete(tmp_path):
    def unavailable(url):
        raise HTTPError(url,400,'Bad request',{},io.BytesIO(b'{"error":true,"reason":"unavailable"}'))
    root=tmp_path/'failed'
    with pytest.raises(ValueError,match='HTTP 400'):
        weather.download(date(2025,1,3),date(2025,1,4),root,getter=unavailable,sleep=lambda _:None,progress=lambda _:None)
    manifest=json.loads((root/'manifest.json').read_bytes())
    assert not manifest['complete'] and manifest['responses'][0]['http_status']==400
    assert json.loads((root/'raw/000.json').read_bytes())['error']


def test_grid_change_between_chunks_requires_a_new_version(tmp_path):
    def change(payload):
        if payload[0]['hourly']['time'][0]>='2024-01-28':payload[0]['elevation']=20
    root=archive(tmp_path,start=date(2024,1,1),end=date(2024,3,1),change=change)
    with pytest.raises(ValueError,match='grid/elevation changed'):
        weather.rebuild(root,tmp_path/'bad')


def test_invalid_cloud_does_not_enter_interpolated_features():
    first=pd.Timestamp('2025-01-01T00:00Z');last=first+3*weather.HOUR
    payload=response(first,last)
    payload[0]['hourly']['cloud_cover_previous_day2'][1]=101
    frames,_=weather.parse_response(payload,first,last,weather.config()['locations'])
    result=weather.align_features(pd.concat(frames),pd.date_range(first,periods=4,freq=STEP),[r['name'] for r in weather.config()['locations']])
    assert result.forecast_cloud_mean.isna().all()
    assert result.forecast_radiation_mean.notna().all()
    assert not result.weather_valid.any()
    assert payload[0]['hourly']['cloud_cover_previous_day2'][1]==101
