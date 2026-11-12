"""Render saved validation forecasts; deterministic error-rank selection, no fitting.

Optional plotting dependency: matplotlib (same as earlier research charts).
"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

WEATHER='xgboost_weather'
CONTROL='xgboost_no_weather'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(run):
    report=json.loads((run/'summary.json').read_bytes())
    if not report.get('complete') or report.get('reserved_test_scored') or report.get('weather_pair_version')!=1:
        raise ValueError('Require completed validation-only paired run')
    path=run/'scored-predictions.csv'
    if digest(path)!=report['artifacts_sha256']['scored-predictions.csv']:
        raise ValueError('Prediction checksum mismatch')
    frame=pd.read_csv(path)
    if not ((frame.delivery_date>='2025-01-01')&(frame.delivery_date<'2025-10-01')).all():
        raise ValueError('Only January–September validation may be plotted')
    keys=None
    for name,group in frame.groupby('model'):
        current=list(zip(group.delivery_date,group.target_timestamp_utc))
        if len(set(current))!=len(current):raise ValueError('Duplicate targets')
        if keys is None:keys=current
        elif keys!=current:raise ValueError('Unmatched forecast targets')
        rmse=float(np.sqrt(np.mean((group.predicted_generation_mw-group.actual_generation_mw)**2)))
        if not np.isclose(rmse,report['metrics'][name]['rmse_mw'],rtol=1e-12):
            raise ValueError('Metrics differ from published run')
    selected=frame[frame.model==WEATHER].copy()
    selected['squared_error']=(selected.predicted_generation_mw-selected.actual_generation_mw)**2
    daily=selected.groupby('delivery_date').squared_error.mean().pow(.5).rename('rmse_mw').reset_index()
    if len(daily)!=report['common_scored_days'] or len(selected)!=report['scored_targets_per_model']:
        raise ValueError('Coverage differs from published run')
    daily=daily.sort_values(['rmse_mw','delivery_date']).reset_index(drop=True)
    choices=[]
    for label,q in [('Lower error',.1),('Median error',.5),('Higher error',.9)]:
        rank=int(q*(len(daily)-1))
        choices.append({'label':label,'rank_one_based':rank+1,'delivery_date':daily.iloc[rank].delivery_date,
                        'rmse_mw':float(daily.iloc[rank].rmse_mw)})
    return report,frame,choices


def panel(ax,frame,choice):
    day=frame[frame.delivery_date==choice['delivery_date']]
    ref=day[day.model==WEATHER]
    # Each observation is an interval start; use local wall-clock hour for this
    # selected non-DST example, without smoothing, filling or interpolation.
    def hours(group):
        local=pd.to_datetime(group.target_timestamp_utc,utc=True).dt.tz_convert('Europe/Brussels')
        return local.dt.hour+local.dt.minute/60
    ax.plot(hours(ref),ref.actual_generation_mw/1000,color='#142E42',lw=2.8,label='Actual production',zorder=3)
    control=day[day.model==CONTROL]
    ax.plot(hours(control),control.predicted_generation_mw/1000,color='#C27B31',lw=1.8,ls='--',label='Without weather')
    ax.plot(hours(ref),ref.predicted_generation_mw/1000,color='#008B88',lw=2.5,label='With weather',zorder=4)
    ax.set_xlim(0,24);ax.set_ylim(bottom=0)
    ax.set_xticks([0,6,12,18,24],['00:00','06:00','12:00','18:00','24:00'])
    ax.set_ylabel('Solar production (GW)',labelpad=10)
    ax.grid(axis='y',color='#DFE6ED');ax.set_axisbelow(True)
    ax.set_title(f"{choice['delivery_date']}  ·  {choice['label']} day  ·  RMSE {choice['rmse_mw']:,.0f} MW",loc='left',fontsize=12,pad=16)


def render(run,output):
    report,frame,choices=prepare(run)
    output.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'text.color':'#142E42',
        'axes.labelcolor':'#536777','xtick.color':'#536777','ytick.color':'#536777',
        'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.edgecolor':'#DCE3E9'})
    for filename,examples in [('weather-prediction-v1.png',[choices[1]]),('weather-prediction-range-v1.png',choices)]:
        many=len(examples)>1
        fig=plt.figure(figsize=(14,14 if many else 9),facecolor='#F5F7FA')
        fig.text(.08,.95,'SOLARCAST BE  /  BELGIAN SOLAR FORECASTING',fontsize=12,color='#008580',weight='bold')
        fig.text(.08,.90,'How close were the predictions?',fontsize=29,weight='bold')
        fig.text(.08,.855 if not many else .867,'Tomorrow’s forecast vs actual production · historical validation',fontsize=14,color='#536777')
        fig.text(.08,.805 if not many else .833,
                 f"{report['weather_rmse_skill_vs_matched_control_pct']:.1f}% lower RMSE vs matching no-weather model  |  {report['common_scored_days']} days",fontsize=14,weight='bold')
        axes=[]
        for i,choice in enumerate(examples):
            ax=fig.add_axes([.09,.30 if not many else .64-i*.205,.84,.40 if not many else .145],facecolor='#F5F7FA')
            panel(ax,frame,choice);axes.append(ax)
        handles,labels=axes[0].get_legend_handles_labels()
        fig.legend(handles,labels,loc='lower left',bbox_to_anchor=(.08,.205 if not many else .13),ncol=3,frameon=False,fontsize=12)
        fig.text(.08,.17 if not many else .105,'Brussels local time · issued at 18:00 the previous day · quarter-hour predictions',fontsize=11,color='#536777')
        selection='Days selected by weather-model daily RMSE rank: 10th / 50th / 90th percentile.' if many else f"Example selected by median daily RMSE across {report['common_scored_days']} days, Jan–Sep 2025."
        fig.text(.08,.125 if not many else .078,selection,fontsize=10,color='#536777')
        fig.text(.08,.085 if not many else .052,'Archived weather; exact publication times unverified. Examples are not the full-period score.',fontsize=10,color='#536777')
        fig.text(.08,.045 if not many else .025,'Sources: Elia (ods032), Open-Meteo / DWD ICON EU · CC BY 4.0 | github.com/Junaid-Bashir/SolarCast-BE',fontsize=9,color='#536777')
        fig.savefig(output/filename,dpi=150,facecolor=fig.get_facecolor());plt.close(fig)
    manifest={'source_summary_sha256':digest(run/'summary.json'),'source_predictions_sha256':digest(run/'scored-predictions.csv'),
        'selection':'Sort daily weather RMSE ascending, delivery date as tie-break; zero-based floor(q*(N-1)), q=0.1,0.5,0.9.',
        'days':choices,'validation_days':report['common_scored_days'],'reserved_test_plotted':False,
        'images_sha256':{name:digest(output/name) for name in ['weather-prediction-v1.png','weather-prediction-range-v1.png']}}
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--manifest',required=True,type=Path)
    args=parser.parse_args();manifest=render(args.run,args.output)
    args.manifest.parent.mkdir(parents=True,exist_ok=True)
    args.manifest.write_bytes((json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode())
    print(json.dumps(manifest,indent=2))
