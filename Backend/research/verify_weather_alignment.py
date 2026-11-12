"""Independent raw-response spot checks plus whole-table keys/solar/hash checks.

From Backend: python research/verify_weather_alignment.py ARCHIVE HOURLY ALIGNED SOLAR.csv
No API access, feature generation, model fitting or source mutation.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def verify(archive,hourly,aligned,solar):
    manifest=json.loads((archive/'manifest.json').read_bytes())
    normalized=json.loads((hourly/'summary.json').read_bytes())
    summary=json.loads((aligned/'summary.json').read_bytes())
    assert hashlib.sha256(solar.read_bytes()).hexdigest()==summary['solar_input_sha256']
    assert hashlib.sha256((archive/'manifest.json').read_bytes()).hexdigest()==normalized['manifest_sha256']
    for root,report in [(hourly,normalized),(aligned,summary)]:
        for name,digest in report['artifacts_sha256'].items():
            assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    frame=pd.read_csv(aligned/'solar-aligned.csv',low_memory=False)
    clock=pd.to_datetime(frame.timestamp,utc=True)
    first=pd.Timestamp(summary['start_local'],tz='Europe/Brussels').tz_convert('UTC')
    end=pd.Timestamp(summary['end_local_exclusive'],tz='Europe/Brussels').tz_convert('UTC')
    expected=pd.date_range(first,end,freq='15min',inclusive='left')
    assert pd.DatetimeIndex(clock).equals(expected)
    assert frame.delivery_date.max()<'2025-10-01'
    production=pd.read_csv(solar)
    power=pd.Series(production.generation_mw.to_numpy(),index=pd.to_datetime(production.timestamp,utc=True))
    original=power.reindex(expected).to_numpy(dtype=float)
    np.testing.assert_allclose(frame.target_generation_mw,original,equal_nan=True)
    original_valid=np.isfinite(original)&(original>=0)&(original<=1e12)
    np.testing.assert_array_equal(frame.solar_target_valid,original_valid)
    assert int((~original_valid).sum())==summary['solar_missing_or_invalid']
    assert (pd.to_datetime(frame.latest_nominal_available_at_assumed,utc=True)<=pd.to_datetime(frame.issue_at,utc=True)).all()
    raw={}
    for entry in manifest['responses']:
        body=(archive/entry['path']).read_bytes()
        assert hashlib.sha256(body).hexdigest()==entry['sha256']
        for location,record in zip(manifest['locations'],json.loads(body)):
            for index,label in enumerate(record['hourly']['time']):
                stamp=pd.Timestamp(label,tz='UTC')
                raw[(location['name'],stamp)]={key:record['hourly'][source][index] for key,(source,_) in
                    {'radiation':('shortwave_radiation_previous_day2','W/m²'),
                     'cloud':('cloud_cover_previous_day2','%'),'temperature':('temperature_2m_previous_day2','°C')}.items()}
    examples=['2024-01-01T00:00:00+01:00','2024-01-20T12:45:00+01:00','2024-01-20T13:00:00+01:00',
              '2024-01-23T12:15:00+01:00','2024-03-31T01:45:00+01:00','2024-03-31T03:00:00+02:00',
              '2024-06-15T12:15:00+02:00','2024-10-27T02:15:00+02:00','2024-10-27T02:15:00+01:00',
              '2025-03-30T03:00:00+02:00','2025-06-15T12:15:00+02:00','2025-09-30T23:45:00+02:00']
    indexed=frame.set_index(clock)
    checked=0
    for label in examples:
        t=pd.Timestamp(label).tz_convert('UTC')
        if t not in indexed.index:continue
        checked+=1
        row=indexed.loc[t];low=t.floor('h');high=low+pd.Timedelta(3_600_000_000_000,unit='ns')
        fraction=(t+pd.Timedelta(450_000_000_000,unit='ns')-low)/pd.Timedelta(3_600_000_000_000,unit='ns')
        for variable in ['radiation','cloud','temperature']:
            values=[]
            for loc in manifest['locations']:
                a=raw.get((loc['name'],low),{}).get(variable)
                b=raw.get((loc['name'],high),{}).get(variable)
                def valid(v):
                    return v is not None and np.isfinite(v) and (variable!='radiation' or v>=0) and (variable!='cloud' or 0<=v<=100)
                values.append(b if variable=='radiation' and valid(b) else
                              ((1-fraction)*a+fraction*b) if variable!='radiation' and valid(a) and valid(b) else np.nan)
            expected_count=int(np.isfinite(values).sum())
            assert row[variable+'_valid_locations']==expected_count
            for stat,func in [('mean',np.mean),('min',np.min),('max',np.max)]:
                want=func(values) if expected_count==len(manifest['locations']) else np.nan
                np.testing.assert_allclose(row['forecast_'+variable+'_'+stat],want,rtol=1e-12,atol=1e-9,equal_nan=True)
    return {'verified':True,'aligned_targets':len(frame),'raw_formula_spot_checks':checked,
            'solar_input_sha256':summary['solar_input_sha256'],'reserved_test_included':False,
            'models_fitted':False}


if __name__=='__main__':
    print(json.dumps(verify(*map(Path,sys.argv[1:5])),indent=2))
