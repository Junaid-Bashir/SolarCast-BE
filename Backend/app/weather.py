"""Versioned 48-hour weather archive, offline normalization and solar alignment.

No model fitting, source fallback, missing-value filling or reserved-test scoring.
"""
import argparse
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from .dayahead_schedule import STEP, local_midnight
from .elia import fetch
from .baselines import load_power

MODEL='dwd_icon_eu'
BASE='https://previous-runs-api.open-meteo.com/v1/forecast'
VARIABLES={'radiation':('shortwave_radiation_previous_day2','W/m²'),
           'cloud':('cloud_cover_previous_day2','%'),
           'temperature':('temperature_2m_previous_day2','°C')}
ALLOWANCE=pd.Timedelta(370*60_000_000_000,unit='ns')
LEAD=pd.Timedelta(48*3_600_000_000_000,unit='ns')
HOUR=pd.Timedelta(3_600_000_000_000,unit='ns')
CONFIG=Path(__file__).resolve().parents[2]/'Shared/docs/research/weather-source-audit-v1/locations.json'
ATTRIBUTION='Weather data by Open-Meteo.com, DWD ICON EU, CC BY 4.0; selected, aligned and equally aggregated by SolarCast.'
SOLAR_ATTRIBUTION='Solar study: Elia Transmission Belgium SA, ods032 (https://opendata.elia.be/explore/dataset/ods032/), CC BY 4.0; timestamp-normalized reference targets, unchanged by weather alignment.'


def encoded(value):
    return (json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def config():
    value=json.loads(CONFIG.read_bytes())
    if value['version']!=1 or len(value['locations'])!=11 or len({r['name'] for r in value['locations']})!=11:
        raise ValueError('Expected the audited 11-location registry v1')
    return value


def validate_dates(start,end):
    if not date(2024,1,1)<=start<end<=date(2025,10,1):
        raise ValueError('Weather study range is [2024-01-01,2025-10-01); reserved test is excluded')


def chunks(start,end):
    validate_dates(start,end)
    # Include the UTC days containing both required local-midnight boundaries.
    first=local_midnight(start).floor('D')
    stop=local_midnight(end).floor('D')+pd.Timedelta(86_400_000_000_000,unit='ns')
    while first<stop:
        last=min(stop,first+pd.Timedelta(28*86_400_000_000_000,unit='ns'))
        yield first,last
        first=last


def query_url(first,last,locations):
    return BASE+'?'+urlencode({'latitude':','.join(str(r['latitude']) for r in locations),
        'longitude':','.join(str(r['longitude']) for r in locations),
        'start_date':first.date().isoformat(),'end_date':(last-HOUR).date().isoformat(),
        'hourly':','.join(name for name,_ in VARIABLES.values()),'models':MODEL,'timezone':'UTC'})


def download(start,end,output,getter=fetch,sleep=time.sleep,progress=print):
    validate_dates(start,end)
    registry=config()
    output.mkdir(parents=True,exist_ok=False);(output/'raw').mkdir()
    manifest={'version':1,'complete':False,'model':MODEL,'nominal_lead_hours':48,
              'allowance_minutes_assumed':370,'start_local':start.isoformat(),'end_local_exclusive':end.isoformat(),
              'locations':registry['locations'],'registry_sha256':sha(encoded(registry)),
              'variables':{k:list(v) for k,v in VARIABLES.items()},'responses':[],
              'historical_publication_times_verified':False,'attribution':ATTRIBUTION}
    def checkpoint():
        (output/'manifest.json').write_bytes(encoded(manifest))
    checkpoint()
    for index,(first,last) in enumerate(chunks(start,end)):
        url=query_url(first,last,manifest['locations'])
        progress(f'Downloading {first.date()} through {(last-HOUR).date()}')
        http_status=200
        try:
            body=getter(url)
        except HTTPError as exc:
            http_status=exc.code
            body=exc.read()
        filename=f'raw/{index:03}.json'
        (output/filename).write_bytes(body)
        # Preserve any failed API response for inspection before stopping.
        entry={'path':filename,'url':url,'sha256':sha(body),'http_status':http_status,'start_utc':first.isoformat(),
               'end_utc_exclusive':last.isoformat(),'retrieved_at_utc':datetime.now(timezone.utc).isoformat()}
        manifest['responses'].append(entry);checkpoint()
        if http_status!=200:
            raise ValueError(f'Weather HTTP {http_status}; response preserved in the incomplete archive')
        payload=json.loads(body)
        if isinstance(payload,dict) and payload.get('error'):
            raise ValueError(f"Weather API error: {payload.get('reason')}")
        sleep(1)
    manifest['complete']=True;checkpoint()
    return manifest


def valid_value(value,variable):
    finite=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    if not finite:return False
    if variable=='radiation':return value>=0
    if variable=='cloud':return 0<=value<=100
    return True


def parse_response(payload,first,last,locations):
    if not isinstance(payload,list) or len(payload)!=len(locations):
        raise ValueError('Expected exactly one response per audited location')
    expected=pd.date_range(first,last,freq=HOUR,inclusive='left')
    output=[];grids=[]
    for index,(record,location) in enumerate(zip(payload,locations)):
        if record.get('utc_offset_seconds')!=0 or record.get('timezone') not in ('UTC','GMT'):
            raise ValueError('Weather response must declare UTC')
        if record.get('location_id',index)!=index:
            raise ValueError('Weather location order differs from registry')
        latitude,longitude=record.get('latitude'),record.get('longitude')
        if not isinstance(latitude,(int,float)) or not isinstance(longitude,(int,float)) or not (abs(latitude-location['latitude'])<.25 and abs(longitude-location['longitude'])<.25):
            raise ValueError('Unexpected weather grid coordinates')
        hourly=record.get('hourly',{})
        parsed=[]
        for value in hourly.get('time',[]):
            stamp=pd.Timestamp(value)
            stamp=stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
            parsed.append(stamp)
        actual=pd.DatetimeIndex(parsed)
        if not actual.equals(expected):
            raise ValueError('Weather clock is truncated, duplicated, unordered or outside the requested range')
        columns={}
        for key,(source,unit) in VARIABLES.items():
            if record.get('hourly_units',{}).get(source)!=unit:
                raise ValueError(f'Unexpected weather unit for {source}')
            values=hourly.get(source,[])
            if len(values)!=len(expected):
                raise ValueError('Weather variable length differs from clock')
            if any(v is not None and (not isinstance(v,(int,float)) or isinstance(v,bool)) for v in values):
                raise ValueError('Weather values must be numeric or null')
            columns[key]=values
            columns[key+'_valid']=[valid_value(v,key) for v in values]
        frame=pd.DataFrame({'timestamp':actual,'location':location['name'],**columns})
        output.append(frame)
        grids.append({'location':location['name'],'requested_latitude':location['latitude'],
                      'requested_longitude':location['longitude'],'grid_latitude':latitude,'grid_longitude':longitude,
                      'elevation':record.get('elevation')})
    return output,grids


def read_manifest(root):
    body=(root/'manifest.json').read_bytes();manifest=json.loads(body)
    if manifest.get('version')!=1 or not manifest.get('complete') or manifest.get('model')!=MODEL:
        raise ValueError('Incomplete, unsupported or wrong-model archive')
    registry=config()
    if manifest['locations']!=registry['locations'] or manifest['registry_sha256']!=sha(encoded(registry)):
        raise ValueError('Weather registry differs from audited version')
    if manifest.get('variables')!={k:list(v) for k,v in VARIABLES.items()} or manifest.get('nominal_lead_hours')!=48 or manifest.get('allowance_minutes_assumed')!=370:
        raise ValueError('Weather variable/availability configuration differs')
    start,end=date.fromisoformat(manifest['start_local']),date.fromisoformat(manifest['end_local_exclusive'])
    planned=[(a.isoformat(),b.isoformat()) for a,b in chunks(start,end)]
    if [(r['start_utc'],r['end_utc_exclusive']) for r in manifest['responses']]!=planned:
        raise ValueError('Weather chunks do not cover the requested range exactly')
    if any(r['url']!=query_url(pd.Timestamp(r['start_utc']),pd.Timestamp(r['end_utc_exclusive']),registry['locations']) for r in manifest['responses']):
        raise ValueError('Weather query differs from the pinned source')
    return manifest,sha(body)


def rebuild(root,output):
    manifest,manifest_hash=read_manifest(root)
    frames=[];grid=None
    for entry in manifest['responses']:
        path=(root/entry['path']).resolve()
        if not path.is_relative_to((root/'raw').resolve()):
            raise ValueError('Weather path escapes raw archive')
        body=path.read_bytes()
        if sha(body)!=entry['sha256']:raise ValueError('Weather response checksum mismatch')
        data,current=parse_response(json.loads(body),pd.Timestamp(entry['start_utc']),pd.Timestamp(entry['end_utc_exclusive']),manifest['locations'])
        if grid is not None and current!=grid:
            raise ValueError('Weather grid/elevation changed between chunks; do not silently mix versions')
        grid=current;frames.extend(data)
    frame=pd.concat(frames,ignore_index=True).sort_values(['timestamp','location'],kind='stable')
    if frame.duplicated(['timestamp','location']).any():raise ValueError('Duplicate normalized weather keys')
    quality={}
    for key in VARIABLES:
        numbers=frame[key].to_numpy(dtype=float)
        quality[key]={'values':len(frame),'missing_or_nonfinite':int((~np.isfinite(numbers)).sum()),
                      'invalid_range':int((np.isfinite(numbers)&~frame[key+'_valid'].to_numpy()).sum()),
                      'valid':int(frame[key+'_valid'].sum())}
    if output.resolve().is_relative_to((root/'raw').resolve()):raise ValueError('Derived output cannot be inside raw storage')
    output.mkdir(parents=True,exist_ok=False)
    frame['timestamp']=frame.timestamp.map(lambda t:t.isoformat())
    frame.to_csv(output/'hourly.csv',index=False,lineterminator='\n')
    pd.DataFrame(grid).to_csv(output/'grid.csv',index=False,lineterminator='\n')
    report={'complete':True,'weather_version':1,'model':MODEL,'manifest_sha256':manifest_hash,
            'start_local':manifest['start_local'],'end_local_exclusive':manifest['end_local_exclusive'],
            'locations':manifest['locations'],'nominal_lead_hours':48,'allowance_minutes_assumed':370,
            'location_hour_rows':len(frame),'variable_quality':quality,
            'all_hourly_values_valid':all(v['valid']==v['values'] for v in quality.values()),
            'historical_publication_times_verified':False,'live_grid_compatibility_verified':False,
            'attribution':ATTRIBUTION,
            'artifacts_sha256':{name:sha((output/name).read_bytes()) for name in ['hourly.csv','grid.csv']}}
    (output/'summary.json').write_bytes(encoded(report))
    return report


def align_features(hourly,targets,locations):
    """Strict all-location summaries; no averaging over an available subset."""
    lower=targets.floor('h');upper=(targets+STEP).ceil('h')
    fraction=((targets+STEP/2-lower)/HOUR).to_numpy(dtype=float)
    matrices={key:[] for key in VARIABLES}
    for name in locations:
        local=hourly[hourly.location==name].set_index('timestamp')
        if local.index.has_duplicates:raise ValueError('Duplicate weather timestamps for a location')
        for key in VARIABLES:
            high=local.reindex(upper)[key].to_numpy(dtype=float)
            high_valid=local.reindex(upper)[key+'_valid'].eq(True).to_numpy()
            if key=='radiation':
                values=high.copy();valid=high_valid
            else:
                low=local.reindex(lower)[key].to_numpy(dtype=float)
                low_valid=local.reindex(lower)[key+'_valid'].eq(True).to_numpy()
                valid=low_valid&high_valid
                values=low+(high-low)*fraction
            values[~valid]=np.nan
            matrices[key].append(values)
    result=pd.DataFrame({'timestamp':targets,'target_local':targets.tz_convert('Europe/Brussels').map(lambda t:t.isoformat()),
                         'hourly_lower':lower,'hourly_upper':upper,'midpoint_fraction':fraction})
    for key,values in matrices.items():
        matrix=np.stack(values,axis=1)
        count=np.isfinite(matrix).sum(axis=1)
        complete=count==len(locations)
        result[key+'_valid_locations']=count
        for stat,func in [('mean',np.mean),('min',np.min),('max',np.max)]:
            aggregated=np.full(len(targets),np.nan)
            aggregated[complete]=func(matrix[complete],axis=1)
            result['forecast_'+key+'_'+stat]=aggregated
    result['weather_valid']=result[[key+'_valid_locations' for key in VARIABLES]].eq(len(locations)).all(axis=1)
    result['latest_nominal_available_at_assumed']=upper-LEAD+ALLOWANCE
    return result


def align(weather_root,solar_path,output):
    report=json.loads((weather_root/'summary.json').read_bytes())
    if not report.get('complete') or report.get('weather_version')!=1 or report.get('model')!=MODEL:
        raise ValueError('Require complete normalized weather v1')
    if report.get('nominal_lead_hours')!=48 or report.get('allowance_minutes_assumed')!=370 or report.get('locations')!=config()['locations']:
        raise ValueError('Normalized weather source configuration differs')
    for name,digest in report['artifacts_sha256'].items():
        path=(weather_root/name).resolve()
        if not path.is_relative_to(weather_root.resolve()) or sha(path.read_bytes())!=digest:
            raise ValueError('Normalized weather checksum mismatch')
    start,end=date.fromisoformat(report['start_local']),date.fromisoformat(report['end_local_exclusive'])
    validate_dates(start,end)
    hourly=pd.read_csv(weather_root/'hourly.csv')
    hourly['timestamp']=pd.to_datetime(hourly.timestamp,utc=True)
    names=[r['name'] for r in report['locations']]
    if names!=[r['name'] for r in config()['locations']]:raise ValueError('Unexpected weather locations')
    if set(hourly.location)!=set(names):raise ValueError('Normalized weather locations are incomplete')
    targets=pd.date_range(local_midnight(start),local_midnight(end),freq=STEP,inclusive='left')
    features=align_features(hourly,targets,names)
    # Every row is tied to the original daily 18:00 deadline, not a later update.
    from .dayahead_schedule import window_for_day
    issues={day.isoformat():window_for_day(day,15).issue_at for day in [start+timedelta(days=i) for i in range((end-start).days)]}
    delivery=targets.tz_convert('Europe/Brussels').strftime('%Y-%m-%d')
    features['delivery_date']=delivery
    features['issue_at']=pd.DatetimeIndex([issues[day] for day in delivery])
    features['weather_available_by_issue_assumed']=features.latest_nominal_available_at_assumed<=features.issue_at
    features['weather_valid'] &= features.weather_available_by_issue_assumed
    reasons=[]
    for row in features.itertuples():
        missing=[f'incomplete_{key}' for key in VARIABLES if getattr(row,key+'_valid_locations')!=len(names)]
        if not row.weather_available_by_issue_assumed:missing.append('weather_unavailable_by_issue')
        reasons.append('|'.join(missing))
    features['weather_exclusion_reasons']=reasons
    observations,power=load_power(solar_path)
    valid,_,_=observations.lookup(targets)
    joined=features.copy()
    joined['target_generation_mw']=power.reindex(targets).to_numpy(dtype=float)
    joined['solar_target_valid']=valid
    joined['joint_target_valid']=joined.weather_valid & joined.solar_target_valid
    # Preserve all target rows. Target generation is a label, never a feature.
    output.mkdir(parents=True,exist_ok=False)
    for name,frame in [('weather-features.csv',features),('solar-aligned.csv',joined)]:
        frame.to_csv(output/name,index=False,lineterminator='\n')
    daily=joined.groupby('delivery_date',sort=True).agg(targets=('weather_valid','size'),
        weather_valid=('weather_valid','sum'),solar_valid=('solar_target_valid','sum'),joint_valid=('joint_target_valid','sum'))
    daily['complete_weather']=daily.weather_valid==daily.targets
    daily['complete_joint']=daily.joint_valid==daily.targets
    daily.to_csv(output/'daily-coverage.csv',lineterminator='\n')
    (output/'ATTRIBUTION.txt').write_bytes((ATTRIBUTION+'\nWeather: https://open-meteo.com/ ; https://creativecommons.org/licenses/by/4.0/\n'+SOLAR_ATTRIBUTION+'\n').encode())
    periods={}
    for name,a,b in [('training_2024','2024-01-01','2025-01-01'),('validation_2025','2025-01-01','2025-10-01')]:
        selected=joined[(joined.delivery_date>=a)&(joined.delivery_date<b)]
        days=daily[(daily.index>=a)&(daily.index<b)]
        periods[name]={'targets':len(selected),'weather_valid':int(selected.weather_valid.sum()),
                       'solar_valid':int(selected.solar_target_valid.sum()),'joint_valid':int(selected.joint_target_valid.sum()),
                       'days':len(days),'complete_weather_days':int(days.complete_weather.sum()),'complete_joint_days':int(days.complete_joint.sum())}
    summary={'complete':True,'alignment_version':1,'model':MODEL,'nominal_lead_hours':48,
             'allowance_minutes_assumed':370,'weather_summary_sha256':sha((weather_root/'summary.json').read_bytes()),
             'solar_input_sha256':observations.sha256,'start_local':start.isoformat(),'end_local_exclusive':end.isoformat(),
             'targets':len(targets),'weather_valid':int(joined.weather_valid.sum()),'solar_valid':int(valid.sum()),
             'joint_valid':int(joined.joint_target_valid.sum()),'solar_missing_or_invalid':int((~valid).sum()),
             'periods':periods,'feature_columns':[c for c in features if c.startswith('forecast_')],
             'feature_definition':{'radiation':'Covering trailing-hour mean; same value for all four quarters.',
                                   'temperature_and_cloud':'Linear interpolation at interval midpoint, only between valid adjacent hourly forecasts.',
                                   'spatial':'Equal-weight mean/min/max only when all 11 locations are valid.'},
             'historical_publication_times_verified':False,'live_grid_compatibility_verified':False,
             'reserved_test_included':False,'models_fitted':False,'attribution':ATTRIBUTION,'solar_attribution':SOLAR_ATTRIBUTION,
             'artifacts_sha256':{name:sha((output/name).read_bytes()) for name in ['weather-features.csv','solar-aligned.csv','daily-coverage.csv','ATTRIBUTION.txt']}}
    (output/'summary.json').write_bytes(encoded(summary))
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    get=commands.add_parser('download');get.add_argument('--start',required=True,type=date.fromisoformat);get.add_argument('--end',required=True,type=date.fromisoformat)
    get.add_argument('--output',required=True,type=Path)
    build=commands.add_parser('rebuild');build.add_argument('--input',required=True,type=Path);build.add_argument('--output',required=True,type=Path)
    join=commands.add_parser('align');join.add_argument('--weather',required=True,type=Path);join.add_argument('--solar',required=True,type=Path);join.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=='download':
            result=download(args.start,args.end,args.output)
            print(f"Downloaded {len(result['responses'])} immutable chunks; run rebuild next.")
        elif args.command=='rebuild':
            print(json.dumps(rebuild(args.input,args.output),indent=2))
        else:
            print(json.dumps(align(args.weather,args.solar,args.output),indent=2))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        parser.exit(1,f'Weather pipeline failed: {exc}\n')


if __name__=='__main__':
    raise SystemExit(main())
