"""Plot the complete ML comparison; optional report dependencies, no model fitting."""
import hashlib
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render(run,output):
    report=json.loads((run/'summary.json').read_bytes())
    if not report['complete'] or report['reserved_test_scored']:
        raise ValueError('Require a complete validation-only run')
    for name,digest in report['artifacts_sha256'].items():
        if hashlib.sha256((run/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Artifact checksum mismatch: '+name)
    metrics=report['metrics']
    order=sorted(metrics,key=lambda name:metrics[name]['rmse_mw'])
    labels={'persistence':'Flat persistence','seasonal_naive_48h':'48-hour seasonal',
            'random_forest':'Random Forest','xgboost':'XGBoost','lightgbm':'LightGBM'}
    colors=['#008D91' if name=='seasonal_naive_48h' else '#A2AEB8' if name=='persistence' else '#365E83' for name in order]
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'text.color':'#142E42',
                         'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,
                         'axes.edgecolor':'#DCE3E9','xtick.color':'#536777','ytick.color':'#142E42'})
    fig=plt.figure(figsize=(14,9),facecolor='#F5F7FA')
    fig.text(.07,.935,'SOLARCAST  /  FIRST ML COMPARISON',fontsize=13,color='#087F81',weight='bold')
    fig.text(.07,.87,'The seasonal baseline still leads',fontsize=29,weight='bold')
    fig.text(.07,.815,f"Jan–Sep 2025 validation · {report['common_scored_days']} common days · {report['scored_intervals_per_model']:,} quarter-hours per model",fontsize=13,color='#536777')
    ax=fig.add_axes([.23,.29,.66,.43],facecolor='#F5F7FA')
    values=[metrics[name]['rmse_mw'] for name in order]
    bars=ax.barh(list(range(len(order))),values,color=colors,height=.58)
    ax.set_yticks(list(range(len(order))),[labels[name] for name in order])
    ax.invert_yaxis()
    ax.set_xlim(0,max(values)*1.18)
    ax.grid(axis='x',color='#DFE5EA');ax.set_axisbelow(True)
    ax.set_xlabel('RMSE (MW) · lower is better',labelpad=14,color='#536777')
    for bar,value in zip(bars,values):
        ax.text(value+35,bar.get_y()+bar.get_height()/2,f'{value:,.0f}',va='center',weight='bold')
    fig.text(.07,.18,f"Frozen ML models · {report['training_examples']:,} eligible 2024 training examples · No weather inputs",fontsize=12)
    fig.text(.07,.125,'18:00 Brussels issue · 15-min assumed delay after interval completion · Nighttime included',fontsize=11,color='#536777')
    fig.text(.07,.085,'Fixed settings; no validation tuning. October–December 2025 test remains unscored.',fontsize=11,color='#536777')
    fig.text(.07,.04,'Source: Elia Transmission Belgium SA · Elia Open Data, ods032 · CC BY 4.0 · Independent SolarCast project',fontsize=9,color='#536777')
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=150,facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__=='__main__':
    render(Path(sys.argv[1]),Path(sys.argv[2]))
