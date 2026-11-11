"""Render a baseline-validation figure from verified artifacts (optional Matplotlib).

Illustrative week is fixed in advance: 9–15 June 2025, not chosen by errors.
Run from backend: python research/plot_baseline_validation.py RUN_DIR OUTPUT.png
"""
import hashlib
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


def render(run,output):
    report=json.loads((run/'summary.json').read_bytes())
    if not report['complete'] or report['period']!='validation' or report['reserved_test_scored']:
        raise ValueError('Require a complete validation-only run')
    for name,expected in report['artifacts_sha256'].items():
        if hashlib.sha256((run/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Artifact checksum mismatch: '+name)
    scored=pd.read_csv(run/'scored-predictions.csv')
    scored['clock']=pd.to_datetime(scored.target_timestamp_utc,utc=True).dt.tz_convert('Europe/Brussels')
    selected=scored[(scored.delivery_date>='2025-06-09') & (scored.delivery_date<'2025-06-16')]
    if selected.delivery_date.nunique()!=7:
        raise ValueError('The fixed illustrative week must contain seven completely scored days')
    colors={'actual':'#142E42','persistence':'#A47749','seasonal_naive_48h':'#008D91'}
    labels={'persistence':'Flat persistence','seasonal_naive_48h':'48-hour seasonal naive'}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,
                        'axes.spines.right':False,'axes.labelcolor':'#536777','text.color':'#142E42',
                        'xtick.color':'#536777','ytick.color':'#536777','axes.edgecolor':'#DCE3E9'})
    fig=plt.figure(figsize=(14,10),facecolor='#F5F7FA')
    fig.text(.075,.947,'SOLARCAST  /  FIRST DAY-AHEAD BASELINES',fontsize=13,color='#087F81',weight='bold')
    fig.text(.075,.897,'A first benchmark for Belgian solar forecasts',fontsize=25,weight='bold')
    fig.text(.075,.855,f"Validation: Jan–Sep 2025  ·  {report['common_scored_days']}/{report['candidate_days']} complete days  ·  "
             f"{report['scored_delivery_intervals_per_model']:,} quarter-hours per model",fontsize=12,color='#536777')
    chart=fig.add_axes([.085,.455,.85,.30],facecolor='#F5F7FA')
    actual=selected[selected.model=='persistence'].sort_values('clock')
    for model in ['persistence','seasonal_naive_48h']:
        subset=selected[selected.model==model].sort_values('clock')
        chart.plot(subset.clock,subset.predicted_generation_mw,color=colors[model],lw=1.5,
                   ls='--' if model=='persistence' else '-',label=labels[model],alpha=.88)
    chart.plot(actual.clock,actual.actual_generation_mw,color=colors['actual'],lw=2,label='Elia production estimate')
    chart.set_ylabel('Power (MW)')
    chart.set_ylim(bottom=0)
    chart.set_title('Illustrative week: 9–15 June 2025 · Brussels local time',loc='left',fontsize=13,pad=16)
    chart.xaxis.set_major_locator(mdates.DayLocator(tz=ZoneInfo('Europe/Brussels')))
    chart.xaxis.set_major_formatter(mdates.DateFormatter('%d %b',tz=ZoneInfo('Europe/Brussels')))
    chart.grid(axis='y',color='#DFE5EA',lw=.7)
    chart.legend(loc='upper center',bbox_to_anchor=(.5,1.04),ncol=3,frameon=False,fontsize=10)
    for i,(metric,title) in enumerate([('mae_mw','Mean absolute error'),('rmse_mw','Root mean squared error')]):
        ax=fig.add_axes([.105+i*.46,.18,.37,.16],facecolor='#F5F7FA')
        values=[report['metrics'][m][metric] for m in ['persistence','seasonal_naive_48h']]
        bars=ax.barh([1,0],values,color=[colors['persistence'],colors['seasonal_naive_48h']],height=.48)
        ax.set_yticks([1,0],['Persistence','48h seasonal'])
        ax.set_xlim(0,max(values)*1.28)
        ax.set_xlabel('MW · lower is better')
        ax.set_title(title+' · full validation period',loc='left',fontsize=11,pad=10)
        ax.grid(axis='x',color='#DFE5EA',lw=.6)
        ax.set_axisbelow(True)
        for bar,value in zip(bars,values):
            ax.text(value+max(values)*.025,bar.get_y()+bar.get_height()/2,f'{value:,.0f}',va='center',weight='bold')
    fig.text(.075,.092,'18:00 Brussels issue · 15-min assumed delay after interval end · Nighttime included',fontsize=11)
    fig.text(.075,.060,'Baseline validation, not an AI result or an Elia forecast comparison. Oct–Dec 2025 test unscored.',fontsize=10,color='#536777')
    fig.text(.075,.030,'Source: Elia Transmission Belgium SA · Elia Open Data, ods032 · CC BY 4.0 · Independent SolarCast project',fontsize=9,color='#536777')
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=150,facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__=='__main__':
    render(Path(sys.argv[1]),Path(sys.argv[2]))
