"""Conditional heating duration from existing thermal profiles; never a control."""
from dataclasses import replace
from math import ceil
from statistics import median
from .tuv_model import finite,predict

HORIZON_MINUTES=1440
STEP_MINUTES=5
ASSUMPTION='Pokračující současný režim kompresoru, bez dalšího odběru; orientační modelový scénář'
KEYS={'full_heat_remaining':('T11','top',-1),'minimum_heat_remaining':('T12','bottom',0)}


def blank(target,status,reason):
    return {'eta_target_c':target,'eta_assumption':ASSUMPTION,
            **{key:{'minutes':None,'status':status,'reason':reason,'probe':probe,
                     'lower_minutes':None,'upper_minutes':None,'horizon_minutes':HORIZON_MINUTES,
                     'reached_fraction':None} for key,(probe,_,_) in KEYS.items()}}


def forecast(model,target,*,context_changed=False):
    """Compute both user-defined endpoints together, preserving observer state.

    Full = upper T11, minimum = lower T12, both at the compressor setpoint.
    All particles count, including right-censored trajectories beyond 24 h.
    """
    if not finite(target) or not 5<=target<=60:
        return blank(None,'target_unknown','Čekám na nastavenou teplotu TUV kompresoru')
    result=blank(float(target),'waiting','Čekám na teplotní stav modelu')
    sample=model.last
    if sample is None or not sample.valid() or not model.particles:return result
    pending=[]
    for key,(_,attribute,_) in KEYS.items():
        if getattr(sample,attribute)>=target:
            result[key].update(minutes=0,status='reached',reason=None,lower_minutes=0,upper_minutes=0,reached_fraction=1.0)
        else:pending.append(key)
    if not pending:return result
    reason=None
    if context_changed:status,reason='context_changed','Čekám na nový úplný odečet po změně provozu'
    elif sample.compressor is None or sample.auxiliary is None:status,reason='signals_unknown','Chybí potvrzený stav kompresoru nebo spirály'
    elif not sample.compressor:status,reason='heating_off','Kompresor neohřívá; čas do jeho dalšího spuštění nelze určit'
    elif sample.auxiliary:status,reason='auxiliary_active','Běží také spirála; samostatný odhad kompresoru není dostupný'
    elif sample.mode not in ('HOTWATER','HEATING','COOLING'):status,reason='mode_unknown','Neznámý provozní režim kompresoru'
    if reason is not None:
        for key in pending:result[key].update(status=status,reason=reason)
        return result
    fan=model.fan_baselines.get(sample.fan_key,{}).get('watts',60.0)
    hits={key:[] for key in pending}
    for particle in model.particles:
        simulated=replace(particle,temperatures=list(particle.temperatures))
        crossed={key:None for key in pending}
        for elapsed in range(STEP_MINUTES,HORIZON_MINUTES+1,STEP_MINUTES):
            # One-minute heat-only substeps are stable for the model's bounded
            # conduction/loss coefficients; observer integration stays at 15 s.
            simulated.temperatures=predict(simulated,sample,STEP_MINUTES,0,fan,max_step=1.0)
            for key in pending:
                index=KEYS[key][2]
                if crossed[key] is None and simulated.temperatures[index]>=target:crossed[key]=elapsed
            if all(value is not None for value in crossed.values()):break
        for key in pending:hits[key].append(crossed[key])
    for key in pending:
        values=sorted(float('inf') if t is None else t for t in hits[key])
        n=len(values);fraction=sum(t!=float('inf') for t in values)/n
        low=values[int(.1*(n-1))];high=values[ceil(.9*(n-1))]
        result[key].update(reached_fraction=round(fraction,3),
                           lower_minutes=None if not finite(low) else int(low),
                           upper_minutes=None if not finite(high) else int(high))
        # Do not report the median of only successful paths. Censored paths
        # remain in the distribution and <80% reaching the target is uncertain.
        value=median(values)
        if fraction<.8 or not finite(value):
            result[key].update(status='uncertain',reason='Při současném režimu nelze čas spolehlivě určit do 24 hodin')
        else:
            result[key].update(minutes=max(STEP_MINUTES,ceil(value/STEP_MINUTES)*STEP_MINUTES),status='forecast',reason=None)
    return result
