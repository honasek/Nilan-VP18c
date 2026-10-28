"""Conservative readiness from real completed forecasts and observed comfort."""
from __future__ import annotations
import math

MIN_MARGIN=120
COMFORT_HOURS=72
MAX_AGE=14*86400


def number(v):return type(v) in (float,int) and math.isfinite(v)


def credit(bins,start,end,top):
    if not all(number(v) for v in (start,end,top)) or not 0<end-start<=300:return
    while start<end:
        key=int(start//3600);until=min(end,(key+1)*3600)
        row=bins.setdefault(str(key),{'seconds':0.,'min_top':top})
        row['seconds']=min(3600.,row['seconds']+until-start)
        row['min_top']=min(row['min_top'],top);start=until
    cutoff=int((end-96*3600)//3600)
    for key in list(bins):
        if int(key)<cutoff:del bins[key]


def evaluate(learner,target,mode,now,top_min,configured_margin):
    # Earliest issued prediction per real completion, current target AND mode.
    completions={}
    for row in sorted(learner.scores,key=lambda r:r['issued_at']):
        if (row.get('outcome')=='reached' and row.get('target')==target and row.get('mode')==mode
                and number(row.get('scored_at')) and now-MAX_AGE<=row['issued_at']<=row['scored_at']<=now
                and number(row.get('error_min'))):
            completions.setdefault(row['scored_at'],row)
    errors=sorted(max(0,r['error_min']) for r in completions.values())
    p90=errors[math.ceil(.9*(len(errors)-1))] if errors else None
    required=max(MIN_MARGIN,math.ceil((p90+15)/5)*5 if p90 is not None else MIN_MARGIN)
    effective=max(required,configured_margin)
    lo=now-COMFORT_HOURS*3600;covered=0.;violations=0;last_bad=None
    for key,row in getattr(learner,'comfort',{}).items():
        at=int(key)*3600;overlap=max(0,min(now,at+3600)-max(lo,at))
        if not overlap:continue
        # With hourly aggregates, partial boundary hours must not claim that
        # observation outside the requested window happened inside it.
        covered+=max(0,row['seconds']-(3600-overlap))
        if row['min_top']<top_min:violations+=1;last_bad=max(last_bad or at,at+3600)
    # UTC day buckets avoid needing a timezone and still require several dates.
    days=len({int(r['scored_at']//86400) for r in completions.values()})
    reasons=[]
    if not learner.summary()['learning_ready']:reasons.append('insufficient_complete_days')
    if mode!='HOTWATER':reasons.append('operating_mode_not_validated')
    if len(errors)<10 or days<3:reasons.append('insufficient_target_mode_completions')
    if effective>180:reasons.append('required_margin_exceeds_supported_window')
    if any(e>effective for e in errors):reasons.append('observed_delay_exceeds_margin')
    if covered<.95*COMFORT_HOURS*3600:reasons.append('insufficient_recent_comfort_coverage')
    if violations:reasons.append('recent_top_temperature_below_minimum')
    return dict(version=1,ready=not reasons,reasons=reasons,target_c=target,mode=mode,as_of=now,
        unique_completions=len(errors),completion_days=days,late_error_p90_min=p90,
        required_margin_min=required,effective_margin_min=effective,
        configured_margin_min=configured_margin,minimum_margin_min=MIN_MARGIN,
        comfort_hours=COMFORT_HOURS,comfort_covered_hours=round(covered/3600,2),
        comfort_min_top_c=top_min,comfort_violating_hours=violations,last_bad_hour_end=last_bad,
        automatic_activation=False)
