"""Small archived-weather source audit. No model fitting or solar scoring.

From Backend:
python research/audit_weather_source.py collect --output ../Shared/data/weather/source-audit-v1
python research/audit_weather_source.py verify --input ../Shared/data/weather/source-audit-v1
"""
import argparse
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

UTC=timezone.utc
ZONE=ZoneInfo('Europe/Brussels')
VARIABLES={'shortwave_radiation_previous_day2':'W/m²',
           'cloud_cover_previous_day2':'%', 'temperature_2m_previous_day2':'°C'}
LOCATIONS=[('antwerp',51.2194,4.4025),('bruges',51.2093,3.2247),
           ('ghent',51.0543,3.7174),('hasselt',50.9307,5.3325),
           ('leuven',50.8798,4.7005),('wavre',50.7172,4.6121),
           ('mons',50.4542,3.9567),('namur',50.4674,4.8718),
           ('liege',50.6326,5.5797),('arlon',49.6833,5.8167),
           ('brussels',50.8503,4.3517)]
DAYS=['2024-01-23','2024-03-31','2024-06-15','2024-10-27','2024-11-01',
      '2025-01-15','2025-03-30','2025-06-15','2025-09-30']
NATIVE_DAYS=['2024-11-01','2025-03-30','2025-06-15']
ALLOWANCE=timedelta(hours=6,minutes=10)


def request_plan():
    return [(model,day) for model,days in [('dwd_icon_eu',DAYS),('dwd_icon_eu_native',NATIVE_DAYS),('gfs_global',DAYS)] for day in days]


def encoded(value):
    return (json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()


def fetch(url):
    for attempt in range(4):
        try:
            with urlopen(Request(url,headers={'User-Agent':'SolarCast-source-audit/1.0'}),timeout=30) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (429,500,502,503,504):
                return exc.read()
            if attempt==3: raise
        except (URLError,TimeoutError):
            if attempt==3: raise
        time.sleep(2**attempt)


def required_hours(day):
    """Bounds for instantaneous interpolation and trailing-hour radiation means.

    Includes both local midnight boundaries, stored in UTC. Radiation at hour h
    describes the preceding hour. No quarter-hour weather values are generated.
    """
    first=datetime.combine(day,datetime.min.time(),ZONE).astimezone(UTC)
    last=datetime.combine(day+timedelta(days=1),datetime.min.time(),ZONE).astimezone(UTC)
    return [first+i*timedelta(hours=1) for i in range(int((last-first).total_seconds()/3600)+1)]


def audit_payload(payload,day):
    if isinstance(payload,dict) and payload.get('error'):
        return {'passed':False,'error':payload.get('reason','API error'),'locations':[]}
    records=payload if isinstance(payload,list) else [payload]
    results=[]
    for index,(name,latitude,longitude) in enumerate(LOCATIONS):
        if index>=len(records):
            results.append({'location':name,'passed':False,'error':'missing response location'})
            continue
        record=records[index]
        header_ok=(record.get('utc_offset_seconds')==0 and record.get('timezone') in ('GMT','UTC')
                   and abs(record.get('latitude',1000)-latitude)<.25
                   and abs(record.get('longitude',1000)-longitude)<.25
                   and record.get('location_id',index)==index)
        hourly=record.get('hourly',{})
        labels=hourly.get('time',[])
        try:
            times=[datetime.fromisoformat(value.replace('Z','+00:00')).replace(tzinfo=UTC)
                   if datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None
                   else datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(UTC) for value in labels]
            clock_ok=(len(times)==48 and len(set(times))==48 and
                      all(b-a==timedelta(hours=1) for a,b in zip(times,times[1:])))
        except (ValueError,TypeError):
            times=[]; clock_ok=False
        lookup={value:i for i,value in enumerate(times)}
        required=required_hours(day)
        variables={}
        for variable,unit in VARIABLES.items():
            values=hourly.get(variable,[])
            selected=[values[lookup[t]] if t in lookup and lookup[t]<len(values) else None for t in required]
            finite=[v for v in selected if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)]
            invalid_range=sum(v<0 for v in finite) if variable.startswith('shortwave') else sum(not 0<=v<=100 for v in finite) if variable.startswith('cloud') else 0
            units_ok=record.get('hourly_units',{}).get(variable)==unit
            variables[variable]={'required':len(required),'finite':len(finite),
                                 'missing_or_nonfinite':len(required)-len(finite),
                                 'invalid_range':invalid_range,'units_ok':units_ok,
                                 'minimum':min(finite) if finite else None,'maximum':max(finite) if finite else None}
        passed=(header_ok and clock_ok and all(v['missing_or_nonfinite']==0 and v['invalid_range']==0 and v['units_ok'] for v in variables.values()))
        results.append({'location':name,'requested_latitude':latitude,'requested_longitude':longitude,
                        'grid_latitude':record.get('latitude'),'grid_longitude':record.get('longitude'),
                        'header_ok':header_ok,'clock_ok':clock_ok,'passed':passed,'variables':variables})
    issue=datetime.combine(day-timedelta(days=1),datetime.min.time(),ZONE).replace(hour=18).astimezone(UTC)
    latest=max(required_hours(day))-timedelta(hours=48)+ALLOWANCE
    return {'passed':len(records)==len(LOCATIONS) and all(r['passed'] for r in results),
            'response_locations':len(records),'locations':results,
            'required_hour_boundaries':len(required_hours(day)),
            'delivery_quarter_hours':(len(required_hours(day))-1)*4,
            'issue_at':issue.isoformat(),'latest_nominal_available_at_assumed':latest.isoformat(),
            'assumed_deadline_margin_minutes':int((issue-latest).total_seconds()/60),
            'nominal_rule_passed':latest<=issue,
            'publication_times_verified':False}


def analyze(root):
    manifest=json.loads((root/'manifest.json').read_bytes())
    if not manifest.get('complete'):
        raise ValueError('Incomplete audit collection')
    expected_locations=[{'name':n,'latitude':a,'longitude':b} for n,a,b in LOCATIONS]
    if manifest.get('locations')!=expected_locations or manifest.get('variables')!=VARIABLES:
        raise ValueError('Audit configuration does not match this version')
    if [(r['model'],r['delivery_date']) for r in manifest['responses']]!=request_plan():
        raise ValueError('Audit request coverage is incomplete or changed')
    cases=[]
    for entry in manifest['responses']:
        path=(root/entry['path']).resolve()
        if not path.is_relative_to((root/'raw').resolve()):
            raise ValueError('Response path escapes raw directory')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=entry['sha256']:
            raise ValueError('Response checksum mismatch')
        cases.append({'model':entry['model'],'delivery_date':entry['delivery_date'],
                      **audit_payload(json.loads(body),date.fromisoformat(entry['delivery_date']))})
    by_model={}
    for model in sorted({c['model'] for c in cases}):
        selected=[c for c in cases if c['model']==model]
        by_model[model]={'sampled_dates':len(selected),'passed_dates':sum(c['passed'] for c in selected),
                         'location_date_cases':sum(len(c['locations']) for c in selected),
                         'passed_location_date_cases':sum(sum(r['passed'] for r in c['locations']) for c in selected)}
    return {'audit_version':1,'collection_started_at_utc':manifest['started_at_utc'],
            'attribution':manifest['attribution'],'models':by_model,
            'nominal_lead_hours':48,'assumed_processing_allowance_minutes':370,
            'scope':'Sample source coverage and retrospective timing assumptions only; no solar labels or model evaluation.',
            'cases':cases,'historical_publication_times_verified':False,
            'full_study_coverage_verified':False}


def collect(root):
    root.mkdir(parents=True,exist_ok=False);(root/'raw').mkdir()
    manifest={'audit_version':1,'complete':False,'started_at_utc':datetime.now(UTC).isoformat(),
              'locations':[{'name':n,'latitude':a,'longitude':b} for n,a,b in LOCATIONS],
              'variables':VARIABLES,'responses':[],
              'attribution':'Weather data by Open-Meteo.com; underlying DWD ICON / NCEP GFS; CC BY 4.0. Selected, summarized by SolarCast.'}
    def checkpoint():
        (root/'manifest.json').write_bytes(encoded(manifest))
    checkpoint()
    for model,days in [('dwd_icon_eu',DAYS),('dwd_icon_eu_native',NATIVE_DAYS),('gfs_global',DAYS)]:
        for value in days:
            day=date.fromisoformat(value)
            query={'latitude':','.join(str(a) for _,a,_ in LOCATIONS),
                   'longitude':','.join(str(b) for _,_,b in LOCATIONS),
                   'start_date':(day-timedelta(days=1)).isoformat(),'end_date':value,
                   'hourly':','.join(VARIABLES),'models':model,'timezone':'UTC'}
            url='https://previous-runs-api.open-meteo.com/v1/forecast?'+urlencode(query)
            body=fetch(url)
            path=f'raw/{model}-{value}.json'
            (root/path).write_bytes(body)
            manifest['responses'].append({'model':model,'delivery_date':value,'path':path,
                                          'url':url,'sha256':hashlib.sha256(body).hexdigest(),
                                          'retrieved_at_utc':datetime.now(UTC).isoformat()})
            checkpoint()
            print(f'Captured {model} {value}',flush=True)
            time.sleep(1)
    manifest['complete']=True;checkpoint()
    report=analyze(root)
    (root/'summary.json').write_bytes(encoded(report))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('collect').add_argument('--output',required=True,type=Path)
    commands.add_parser('verify').add_argument('--input',required=True,type=Path)
    args=parser.parse_args()
    report=collect(args.output) if args.command=='collect' else analyze(args.input)
    print(json.dumps({'models':report['models'],'full_study_coverage_verified':False,
                      'historical_publication_times_verified':False},indent=2))
