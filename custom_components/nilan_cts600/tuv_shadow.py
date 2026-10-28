"""Passive DHW demand learning and conditional counterfactuals, no HA imports/I/O.

Draw litres, thermal stock and future savings are model estimates, never meters.
No simulated trajectory is assimilated into the real thermal observer.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace, asdict
from datetime import datetime, timedelta, time, timezone
from math import ceil
from statistics import median
from zoneinfo import ZoneInfo
from . import tuv_validation as validation

from .tuv_model import finite, soc, predict, CP_WH_L_K, CELL_L

VERSION=1
SIMULATION_VERSION=3
MODES=('HOTWATER','HEATING','COOLING')
STEP=5
MAX_PROFILES=24
GAP_S=300
RETENTION_DAYS=45
CANDIDATE_STEP_MIN=5
PROFILE_DAYS=14


def quantile(values, fraction):
    values=sorted(values)
    return values[min(len(values)-1,ceil(fraction*(len(values)-1)))] if values else None


def stamp(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat() if finite(value) else None


@dataclass(frozen=True)
class Settings:
    enabled: bool=True
    window_start: str='03:00'
    window_end: str='05:00'
    day_enabled: bool=False
    day_start: str='13:00'
    day_end: str='15:00'
    reserve_pct: float=35.0
    top_min_c: float=45.0
    prior_daily_l: float=120.0
    margin_min: float=30.0
    timezone: str='Europe/Prague'

    def validate(self):
        if type(self.enabled) is not bool or type(self.day_enabled) is not bool:
            raise ValueError('Neznámé povolení shadow výpočtu')
        for text in (self.window_start,self.window_end,self.day_start,self.day_end):
            if not isinstance(text,str) or len(text)!=5 or time.fromisoformat(text).isoformat(timespec='minutes')!=text:
                raise ValueError('Neplatný čas okna')
        if self.window_start==self.window_end or (self.day_enabled and self.day_start==self.day_end):
            raise ValueError('Začátek a konec okna se shodují')
        for value,lo,hi in ((self.reserve_pct,5,90),(self.top_min_c,35,60),(self.prior_daily_l,0,500),(self.margin_min,0,180)):
            if not finite(value) or not lo<=value<=hi:raise ValueError('Neplatná rezerva nebo předpoklad odběru')
        ZoneInfo(self.timezone)
        return self


def wall_timestamp(day, text, tz):
    """Choose first occurrence on a fold; move a nonexistent wall time forward.

    Intervals use real UTC seconds, hence DST never fabricates a 120-minute slot.
    """
    naive=datetime.combine(day,time.fromisoformat(text))
    candidate=naive.replace(tzinfo=tz,fold=0)
    roundtrip=datetime.fromtimestamp(candidate.timestamp(),tz)
    return roundtrip.timestamp() if roundtrip.replace(tzinfo=None)!=naive else candidate.timestamp()


def next_window(now,settings):
    tz=ZoneInfo(settings.timezone)
    today=datetime.fromtimestamp(now,tz).date()
    choices=[]
    windows=[('night',settings.window_start,settings.window_end)]
    if settings.day_enabled:windows.append(('day',settings.day_start,settings.day_end))
    for offset in (-1,0,1):
        day=today+timedelta(days=offset)
        for kind,start,end in windows:
            first=wall_timestamp(day,start,tz)
            last=wall_timestamp(day+timedelta(days=end<=start),end,tz)
            if last>now and last>first:choices.append((max(now,first),first,last,kind))
    if not choices:raise ValueError('Nelze určit následující okno')
    _,start,end,kind=min(choices)
    return {'kind':kind,'start':start,'end':end}


def prior_rate(at,settings):
    """Explicit provisional tank-outlet draw profile; not learnt household usage."""
    hour=datetime.fromtimestamp(at,ZoneInfo(settings.timezone)).hour
    share=.4/180 if 6<=hour<9 else .5/300 if 18<=hour<23 else .1/960
    return settings.prior_daily_l*share


def energy(temperatures):
    return sum(temperatures)*CELL_L*CP_WH_L_K


def complete_day(row):
    return (sum(row['minutes'])>=1080 and sum(row['minutes'][12:18])>=135
            and sum(row['minutes'][36:46])>=225)


@dataclass(frozen=True)
class DemandProfile:
    """One coherent local-day trajectory, repeated over the forecast horizon.

    Litres are inferred by the observer, not metered. The stress factor scales
    the entire day; peaks from unrelated days are never combined.
    """
    kind: str
    source: str
    bins: tuple[float, ...]
    observed_day: str | None=None
    factor: float=1.0

    def rate(self, at, settings):
        local=datetime.fromtimestamp(at,ZoneInfo(settings.timezone))
        return self.bins[local.hour*2+local.minute//30]*self.factor/30

    def public(self):
        return {'kind':self.kind,'source':self.source,'observed_day':self.observed_day,
                'factor':self.factor,'daily_tank_l_model':round(sum(self.bins)*self.factor,1),
                'draw_is_measured':False}


def demand_profiles(learner,settings,now,horizon):
    """Median-like whole day and a high-demand whole day for this horizon.

    Three distinct, sufficiently covered past days are required. Missing bins
    are filled from the explicit prior; a covered zero stays zero. At most the
    latest fourteen days participate, without changing persisted learning.
    """
    tz=ZoneInfo(settings.timezone)
    today=datetime.fromtimestamp(now,tz).date()
    prior=tuple(settings.prior_daily_l*(.4/6 if 12<=i<18 else .5/10 if 36<=i<46 else .1/32)
                for i in range(48))
    days=[]
    for day,row in sorted(learner.days.items()):
        if day>=today.isoformat() or (today-datetime.fromisoformat(day).date()).days>RETENTION_DAYS:
            continue
        if complete_day(row):
            bins=tuple(row['litres'][i]*30/row['minutes'][i] if row['minutes'][i]>=20 else prior[i]
                       for i in range(48))
            days.append(DemandProfile('expected','observed_whole_day',bins,day))
    days=days[-PROFILE_DAYS:]
    if len(days)<3:
        expected=DemandProfile('expected','explicit_prior',prior)
        return expected,replace(expected,kind='stress',factor=1.2)
    # Choose an actually observed median-volume day; L1 distance resolves ties.
    # A pure shape medoid could prefer an unusually empty day when draw times
    # vary. Pointwise medians/quantiles can invent a multi-peak household day.
    typical_total=median(sum(p.bins) for p in days)
    expected=min(days,key=lambda p:(abs(sum(p.bins)-typical_total),
                                    sum(sum(abs(a-b) for a,b in zip(p.bins,q.bins)) for q in days),p.observed_day))
    def horizon_draw(p):
        total=0.;cursor=now
        while cursor<horizon:
            until=min((cursor//300+1)*300,horizon)
            total+=p.rate((cursor+until)/2,settings)*(until-cursor)/60
            cursor=until
        return total
    # Select one whole historical day, not a different maximum for each bin.
    stress=max(days,key=lambda p:(horizon_draw(p),sum(p.bins),p.observed_day))
    return expected,replace(stress,kind='stress',factor=1.2)


def evaluation_horizon(now,window,settings):
    """All candidates include the following morning/evening demand period."""
    tz=ZoneInfo(settings.timezone)
    day=datetime.fromtimestamp(window['end'],tz).date()
    end=wall_timestamp(day,'09:00' if window['kind']=='night' else '23:00',tz)
    return max(window['end'],end)


class ShadowLearner:
    """Bounded evidence from actual observer updates only, independent Store."""
    def __init__(self):
        self.days={}
        self.last_at=None
        self.previous=None
        self.previous_energy=None
        self.previous_drawing=False
        self.clean=None
        self.rates=[]
        self.session=None
        self.sessions=[]
        self.pending=[]
        self.scores=[]
        self.gaps=0
        self.samples=0
        self.revision=0
        self.comfort={}

    def _credit(self,start,end,litres,tz_name):
        tz=ZoneInfo(tz_name)
        cursor=start
        while cursor<end-1e-6:
            # UTC minute slices also handle repeated/missing local half-hours.
            until=min(end,(int(cursor//60)+1)*60)
            local=datetime.fromtimestamp(cursor,tz)
            key=local.date().isoformat()
            index=local.hour*2+local.minute//30
            row=self.days.setdefault(key,{'minutes':[0.0]*48,'litres':[0.0]*48})
            row['minutes'][index]+=(until-cursor)/60
            row['litres'][index]+=litres*(until-cursor)/(end-start)
            cursor=until

    def observe(self,model,target,settings):
        s=model.last
        if s is None or not s.valid() or (self.last_at is not None and s.at<=self.last_at):return False
        self.samples+=1;self.revision+=1
        previous=self.previous
        dt=None if self.last_at is None else (s.at-self.last_at)/60
        stock=median(energy(p.temperatures) for p in model.particles)
        drawing=bool(model.active_draw)
        valid=(dt is not None and .25<=dt<=5 and previous is not None
               and previous.compressor is not None and previous.auxiliary is not None
               and s.compressor is not None and s.auxiliary is not None)
        if valid:
            validation.credit(self.comfort,self.last_at,s.at,min(previous.top,s.top))
            draw=median(p.draw_l_min for p in model.particles) if drawing and model.draw_probability>=.45 else 0.0
            self._credit(self.last_at,s.at,max(0,min(20,draw))*dt,settings.timezone)
        elif self.last_at is not None:
            self.gaps+=1
        clean=(valid and previous.compressor is True and s.compressor is True
               and previous.auxiliary is False and s.auxiliary is False
               and previous.mode==s.mode and s.mode in MODES and previous.fan_key==s.fan_key
               and not drawing and not self.previous_drawing
               and model.phase not in ('gap','mismatch','initializing')
               and s.bottom>=previous.bottom and s.top>=previous.top)
        if clean:
            if self.clean is None:
                self.clean={'start':self.last_at,'mode':s.mode,'bottom':previous.bottom,'top':previous.top,'energy':self.previous_energy}
            c=self.clean
            minutes=(s.at-c['start'])/60
            if minutes>=20 and s.bottom-c['bottom']>=1:
                self.rates.append({'at':s.at,'mode':s.mode,'minutes':round(minutes,2),
                                   'bottom_c_min':(s.bottom-c['bottom'])/minutes,
                                   'top_c_min':(s.top-c['top'])/minutes,
                                   'model_heat_w':max(0,(stock-c['energy'])*60/minutes)})
                self.rates=self.rates[-256:];self.clean=None
        else:self.clean=None
        # Measured elapsed time to T12, with explicit interruption/draw flags.
        target_valid=finite(target) and 5<=target<=60
        if self.session is not None:
            session=self.session
            if not valid or not target_valid or target!=session['target'] or s.auxiliary is not False:
                self._end_session(s.at,'interrupted',s)
            else:
                session['drawing']=session['drawing'] or drawing
                session['modes']=sorted(set(session['modes']+[s.mode]))
                if s.bottom>=target:self._end_session(s.at,'reached',s)
                elif s.compressor is not True:self._end_session(s.at,'stopped_before_target',s)
        if self.session is None and target_valid and s.compressor is True and s.auxiliary is False and s.bottom<target:
            self.session={'start':s.at,'target':target,'start_bottom':s.bottom,'start_top':s.top,
                          'drawing':drawing,'modes':[s.mode],'start_observed':bool(valid and previous.compressor is False)}
        # Predictions remain frozen. Score only the same real target, not a virtual future.
        pending=[]
        for item in self.pending:
            if not valid or not target_valid or target!=item['target'] or s.auxiliary is not False or drawing:
                self.scores.append({**item,'outcome':'censored','scored_at':s.at})
            elif s.bottom>=target:
                self.scores.append({**item,'outcome':'reached','scored_at':s.at,'error_min':(s.at-item['predicted_at'])/60})
            elif s.compressor is not True or s.mode!=item['mode'] or s.at-item['issued_at']>86400:
                self.scores.append({**item,'outcome':'censored','scored_at':s.at})
            else:pending.append(item)
        self.pending=pending;self.scores=self.scores[-256:]
        self.last_at=s.at;self.previous=s;self.previous_energy=stock;self.previous_drawing=drawing
        cutoff=(datetime.fromtimestamp(s.at,ZoneInfo(settings.timezone)).date()-timedelta(days=RETENTION_DAYS)).isoformat()
        self.days={day:row for day,row in self.days.items() if day>=cutoff}
        return True

    def _end_session(self,at,outcome,sample):
        item={**self.session,'end':at,'outcome':outcome,'end_bottom':sample.bottom,'end_top':sample.top,
              'minutes':round((at-self.session['start'])/60,2)}
        self.sessions=(self.sessions+[item])[-128:];self.session=None

    def register_eta(self,model,result):
        eta=result.get('minimum_heat_remaining',{})
        s=model.last
        if s is None or model.active_draw or s.auxiliary is not False or s.compressor is not True:return
        if eta.get('status')!='forecast' or not finite(eta.get('minutes')):return
        if any(s.at-item['issued_at']<1800 for item in self.pending):return
        self.pending.append({'issued_at':s.at,'predicted_at':s.at+eta['minutes']*60,
                             'target':result['eta_target_c'],'mode':s.mode})
        self.pending=self.pending[-48:]

    def demand(self,at,settings):
        expected,_=demand_profiles(self,settings,at,at+86400)
        return expected.rate(at,settings),expected.source

    def summary(self):
        complete=sum(complete_day(row) for row in self.days.values())
        periods={}
        for name,start,end in (('morning',12,18),('evening',36,46)):
            values=[sum(row['litres'][start:end]) for row in self.days.values()
                    if sum(row['minutes'][start:end])>=(end-start)*30*.75]
            periods[name]={'observed_days':len(values),'model_draw_l_p90':None if not values else round(quantile(values,.9),1)}
        reached=[x for x in self.sessions if x['outcome']=='reached']
        errors=[x['error_min'] for x in self.scores if x['outcome']=='reached']
        return {'samples':self.samples,'observed_days':len(self.days),'complete_days':complete,
                'covered_hours':round(sum(sum(x['minutes']) for x in self.days.values())/60,1),
                'periods':periods,'clean_heat_segments':len(self.rates),
                'heat_speed_c_min':{mode:quantile([x['bottom_c_min'] for x in self.rates if x['mode']==mode],.1) for mode in MODES},
                'completed_heat_sessions':len(reached),'last_heat_sessions':deepcopy(self.sessions[-5:]),
                'eta_evaluated':len(errors),'eta_censored':sum(x['outcome']=='censored' for x in self.scores),
                'eta_late_error_p90_min':None if not errors else round(quantile(errors,.9),1),
                'learning_ready':complete>=14 and len(self.rates)>=5 and len(errors)>=5,
                'gaps':self.gaps,'draw_is_measured':False,'validated_for_control':False}

    def dump(self):
        return {'version':VERSION,'days':deepcopy(self.days),'last_at':self.last_at,
                'previous':None if self.previous is None else asdict(self.previous),
                'previous_energy':self.previous_energy,'previous_drawing':self.previous_drawing,
                'clean':deepcopy(self.clean),'rates':deepcopy(self.rates),'session':deepcopy(self.session),
                'sessions':deepcopy(self.sessions),'pending':deepcopy(self.pending),'scores':deepcopy(self.scores),
                'gaps':self.gaps,'samples':self.samples,'revision':self.revision,'comfort':deepcopy(self.comfort)}

    @classmethod
    def restore(cls,data,now):
        from .tuv_model import Sample
        if not isinstance(data,dict) or data.get('version')!=VERSION:raise ValueError('Invalid shadow version')
        result=cls()
        data=deepcopy(data)
        data.setdefault('comfort',{})
        if not isinstance(data.get('days'),dict) or len(data['days'])>47:raise ValueError('Invalid shadow days')
        for day,row in data['days'].items():
            datetime.strptime(day,'%Y-%m-%d')
            if not isinstance(row,dict) or set(row)!= {'minutes','litres'}:raise ValueError('Invalid demand row')
            for key,limit in (('minutes',65),('litres',2000)):
                if not isinstance(row[key],list) or len(row[key])!=48 or any(not finite(v) or not 0<=v<=limit for v in row[key]):
                    raise ValueError('Invalid demand bins')
        for key,limit in (('rates',256),('sessions',128),('pending',48),('scores',256)):
            if not isinstance(data.get(key),list) or len(data[key])>limit:raise ValueError('Invalid shadow evidence')
        if (not isinstance(data.get('comfort'),dict) or len(data['comfort'])>100
                or any(not str(k).isdigit() or not isinstance(v,dict) or set(v)!= {'seconds','min_top'}
                       or not finite(v['seconds']) or not 0<=v['seconds']<=3600
                       or not finite(v['min_top']) or not 0<=v['min_top']<=95 for k,v in data['comfort'].items())):
            raise ValueError('Invalid comfort evidence')
        # Validate all persisted trees, including optional ongoing sessions, atomically.
        def check(value,depth=0):
            if depth>8:raise ValueError('Invalid shadow nesting')
            if isinstance(value,bool) or value is None:return
            if isinstance(value,(int,float)):
                if not finite(value) or abs(value)>1e12:raise ValueError('Invalid shadow scalar')
            elif isinstance(value,str):
                if len(value)>100:raise ValueError('Invalid shadow text')
            elif isinstance(value,list):
                if len(value)>256:raise ValueError('Invalid shadow list')
                for item in value:check(item,depth+1)
            elif isinstance(value,dict):
                if len(value)>100:raise ValueError('Invalid shadow mapping')
                for item in value.values():check(item,depth+1)
            else:raise ValueError('Invalid shadow type')
        check(data)
        for key in result.__dict__:
            if key not in data:raise ValueError('Missing shadow field')
            setattr(result,key,deepcopy(data[key]))
        if result.last_at is not None and (not finite(result.last_at) or not 0<result.last_at<=now+5):raise ValueError('Invalid shadow time')
        if result.previous is not None:
            result.previous=Sample(**result.previous)
            if not result.previous.valid() or result.previous.at!=result.last_at:raise ValueError('Invalid shadow sample')
            if not finite(result.previous_energy) or not 0<=result.previous_energy<=100000:raise ValueError('Invalid previous thermal energy')
        for key in ('samples','revision','gaps'):
            if type(getattr(result,key)) is not int or getattr(result,key)<0:raise ValueError('Invalid shadow count')
        if type(result.previous_drawing) is not bool:raise ValueError('Invalid draw marker')
        # Key/shape checks keep a corrupt evidence item from disabling all future samples.
        for item in result.rates:
            if not isinstance(item,dict) or not {'at','mode','minutes','bottom_c_min','top_c_min','model_heat_w'}<=item.keys() or item['mode'] not in MODES or not finite(item['bottom_c_min']) or not finite(item['minutes']):raise ValueError('Invalid heat rate')
        for item in result.pending+result.scores:
            if not isinstance(item,dict) or not {'issued_at','predicted_at','target','mode'}<=item.keys() or any(not finite(item[k]) for k in ('issued_at','predicted_at','target')):raise ValueError('Invalid ETA evidence')
        for item in result.sessions+([] if result.session is None else [result.session]):
            if not isinstance(item,dict) or not {'start','target','drawing','modes','start_observed'}<=item.keys() or not finite(item['start']) or not finite(item['target']) or not isinstance(item['modes'],list) or any(mode not in MODES for mode in item['modes']) or type(item['drawing']) is not bool or type(item['start_observed']) is not bool:raise ValueError('Invalid heat session')
        if result.clean is not None and (not isinstance(result.clean,dict) or not {'start','mode','bottom','top','energy'}<=result.clean.keys() or any(not finite(result.clean[k]) for k in ('start','bottom','top','energy'))):raise ValueError('Invalid clean segment')
        return result


def profiles(model):
    """Bound runtime without pretending selected profiles are calibrated probabilities."""
    rows=model.particles
    if len(rows)<=MAX_PROFILES:return [replace(p,temperatures=p.temperatures[:]) for p in rows]
    indices={round(i*(len(rows)-1)/(MAX_PROFILES-8-1)) for i in range(MAX_PROFILES-8)}
    for measure in (lambda p:p.gains[0],lambda p:p.loss_w_k,lambda p:soc(p.temperatures,p.temperatures[-1]),lambda p:p.inlet_c):
        indices.add(min(range(len(rows)),key=lambda i:measure(rows[i])))
        indices.add(max(range(len(rows)),key=lambda i:measure(rows[i])))
    return [replace(rows[i],temperatures=rows[i].temperatures[:]) for i in sorted(indices)]


def simulate(model,learner,settings,target,now,start,deadline,variant,price_start=None,
             *,demand_profile=None,horizon=None,thermostat=None):
    """Evolve every path to the same fixed horizon, even after first reaching T12.

    A conditional start is a scenario assumption, not an emulation of firmware.
    Private path results permit paired comparisons without mismatching medians.
    """
    fan=model.fan_baselines.get(model.last.fan_key,{}).get('watts',60)
    if not finite(target) or not 30<target<=60:raise ValueError('Stock reference must be above 30 °C')
    thermostat=thermostat or {}
    measured=thermostat.get('restart_drop_c')
    drop=measured if finite(measured) and 0<measured<=10 else 3.0
    bounds=thermostat.get('restart_range_c')
    hold_floor=bounds[1] if bounds else target-drop
    hysteresis_drop=(target-bounds[0] if bounds and demand_profile is not None and demand_profile.kind=='stress' else drop)
    house=model.last.compressor is True and model.last.mode in ('HEATING','COOLING')
    price_start=start if price_start is None else price_start
    end=deadline if horizon is None else max(deadline,horizon)
    rates=[];cursor=now
    while cursor<end:
        until=min((cursor//(STEP*60)+1)*STEP*60,end,
                  start if cursor<start else end,deadline if cursor<deadline else end)
        if demand_profile is None:
            rate,source=learner.demand((cursor+until)/2,settings)
        else:
            rate,source=demand_profile.rate((cursor+until)/2,settings),demand_profile.source
        rates.append((cursor,until,rate,source));cursor=until
    paths=[]
    for p in profiles(model):
        initial=p.temperatures[:]
        min_stock=soc(initial,initial[-1],target);min_top=initial[-1]
        post_stock=post_top=float('inf')
        full=variant=='normal'
        wait_litres=sum(draw*max(0,min(last,start)-first)/60 for first,last,draw,_ in rates if first<start)
        reserve_goal=min(100,settings.reserve_pct+5+wait_litres*max(0,target-p.inlet_c)/(180*(target-30))*100)
        top_goal=min(target,settings.top_min_c+2)
        reserve_phase=variant=='partial' and (min_stock<reserve_goal or min_top<top_goal)
        water_on=model.last.compressor is True and model.last.mode=='HOTWATER'
        initial_held=thermostat.get('reached_probes',{}).get('T12') is True and initial[0]>hold_floor
        completion=now if initial[0]>=target or initial_held else None
        cycle_reached=completion;deadline_cycle=None;deadline_ready=False
        held_low=initial[0] if completion is not None else float('inf')
        total=water_minutes=cheap=0.0;cancel=None;deadline_bottom=None
        for first,last,draw,_ in rates:
            dt=(last-first)/60
            was_water_on=water_on
            if first>=start and not full:
                full=True
                water_on=p.temperatures[0]<target and not (cycle_reached is not None and p.temperatures[0]>hold_floor)
            if variant!='normal' and not full and not reserve_phase and (
                    soc(p.temperatures,p.temperatures[-1],target)<settings.reserve_pct
                    or p.temperatures[-1]<settings.top_min_c):
                full=True;cancel=first
            desired=target if full else max(5,min(target,settings.top_min_c))
            if reserve_phase:
                if ((soc(p.temperatures,p.temperatures[-1],target)>=reserve_goal and p.temperatures[-1]>=top_goal)
                        or p.temperatures[0]>=target):
                    reserve_phase=False;water_on=False
                else:water_on=True
            elif full:
                if p.temperatures[0]>=desired:water_on=False
                elif p.temperatures[0]<desired-hysteresis_drop or water_on:water_on=True
            else:water_on=False
            if water_on and not was_water_on and p.temperatures[0]<target:cycle_reached=None
            heating=water_on or house
            mode=model.last.mode if house else 'HOTWATER'
            virtual=replace(model.last,at=first,compressor=heating,auxiliary=False,mode=mode,
                            power_w=None,power_quality='assumed')
            p.temperatures=predict(p,virtual,dt,draw,fan,max_step=1.0)
            if heating:
                total+=dt
                cheap+=max(0,min(last,deadline)-max(first,price_start))/60
            if water_on:water_minutes+=dt
            if p.temperatures[0]>=target:
                if completion is None:completion=last
                if cycle_reached is None:cycle_reached=last
            elif p.temperatures[0]<=hold_floor:cycle_reached=None
            if last<=deadline:
                min_stock=min(min_stock,soc(p.temperatures,p.temperatures[-1],target))
                min_top=min(min_top,p.temperatures[-1])
                if completion is not None:held_low=min(held_low,p.temperatures[0])
            if first<deadline<=last:
                deadline_bottom=p.temperatures[0]
                deadline_cycle=cycle_reached
                deadline_ready=(deadline_bottom>=target-.5 or
                                (cycle_reached is not None and not water_on and deadline_bottom>hold_floor))
                post_stock=soc(p.temperatures,p.temperatures[-1],target);post_top=p.temperatures[-1]
            if last>deadline:
                post_stock=min(post_stock,soc(p.temperatures,p.temperatures[-1],target))
                post_top=min(post_top,p.temperatures[-1])
        paths.append({'completion':float('inf') if completion is None else completion,
                      'held_low':held_low if completion is not None and completion<=deadline else float('-inf'),
                      'min_stock':min_stock,'min_top':min_top,'post_stock':post_stock,'post_top':post_top,
                      'deadline_bottom':deadline_bottom,'cancel':cancel,'electric_kwh':total*.45/60,
                      'deadline_ready':deadline_ready,'deadline_cycle':float('inf') if deadline_cycle is None else deadline_cycle,
                      'water_kwh':water_minutes*.45/60,'window_kwh':cheap*.45/60,
                      'end':p.temperatures[:],'initial':initial,'inlet':p.inlet_c})
    upper=quantile([p['completion'] for p in paths],.9)
    low=quantile([p['min_stock'] for p in paths],.1);top=quantile([p['min_top'] for p in paths],.1)
    bottom=quantile([p['deadline_bottom'] for p in paths],.1)
    post_stock=quantile([p['post_stock'] for p in paths],.1);post_top=quantile([p['post_top'] for p in paths],.1)
    held_low=quantile([p['held_low'] for p in paths],.1)
    reached=finite(upper) and upper<=deadline
    cycle_finish=quantile([p['deadline_cycle'] for p in paths],.9)
    with_margin=finite(cycle_finish) and cycle_finish<=deadline-settings.margin_min*60
    at_deadline=quantile([p['deadline_ready'] for p in paths],.1) is True
    return {'variant':variant,'start_at':stamp(start),'finish_at':stamp(upper),
            'finish_epoch':upper if finite(upper) else None,
            'horizon_end':stamp(end),'target_reached_before_deadline':reached,
            'target_reached_with_margin':with_margin,'ready_at_deadline':at_deadline,
            'target_held_until_deadline':finite(held_low) and held_low>hold_floor,
            'nominal_target_held_until_deadline':finite(held_low) and held_low>=target-.5,
            'nominal_target_at_deadline':bottom>=target-.5,
            'ready_cycle_finish_at':stamp(cycle_finish),
            'hysteresis_drop_c':round(hysteresis_drop,2),'readiness_floor_c':round(hold_floor,2),
            'stock_reference_c':target,
            'holding_min_t12_c':round(held_low,1) if finite(held_low) else None,
            'min_reserve_pct':round(low,1),'min_top_c':round(top,1),
            'reserve_met':low>=settings.reserve_pct and top>=settings.top_min_c,
            'deadline_met':with_margin and at_deadline,'deadline_t12_c':round(bottom,1),
            'post_use_comfort_met':post_stock>=settings.reserve_pct and post_top>=settings.top_min_c,
            'post_use_min_reserve_pct':round(post_stock,1),'post_use_min_top_c':round(post_top,1),
            'target_tolerance_c':.5,'cancelled_paths':sum(p['cancel'] is not None for p in paths),
            'reaching_paths':sum(finite(p['completion']) for p in paths),'profiles':len(paths),
            'compressor_electric_kwh_model':round(median(p['electric_kwh'] for p in paths),3),
            'dhw_request_electric_kwh_model':round(median(p['water_kwh'] for p in paths),3),
            'window_electric_kwh_model':round(median(p['window_kwh'] for p in paths),3),
            'end_tank_heat_kwh_model':round(median((energy(p['end'])-6*CELL_L*CP_WH_L_K*p['inlet'])/1000 for p in paths),3),
            'end_stock_pct':round(median(soc(p['end'],p['end'][-1],target) for p in paths),1),
            'end_layers_c':[round(median(p['end'][i] for p in paths),1) for i in range(6)],
            'saving_czk':None,
            'energy_scope':'Celý modelový kompresor 450 W ve společném horizontu; koncový stav se porovnává zvlášť',
            '_paths':paths}


def compare_energy(row,normal):
    """Do not credit an emptier/cooler tank as an electrical saving.

    Compare matching thermal paths, not unrelated medians. There is no trusted
    COP for converting an inventory deficit into a claimed future electricity
    cost, so differing end states are explicitly ineligible for savings.
    """
    pairs=list(zip(row['_paths'],normal['_paths']))
    heat=[(energy(a['end'])-energy(b['end']))/1000 for a,b in pairs]
    electrical=[a['electric_kwh']-b['electric_kwh'] for a,b in pairs]
    equivalent=all(abs(h)<=.05 and max(abs(x-y) for x,y in zip(a['end'],b['end']))<=.5
                   for (a,b),h in zip(pairs,heat))
    service=all(r['deadline_met'] and r['reserve_met'] and r['post_use_comfort_met'] for r in (row,normal))
    same_horizon=row['horizon_end']==normal['horizon_end']
    comparable=equivalent and service and same_horizon
    return {'same_horizon':same_horizon,'terminal_state_equivalent':equivalent,
            'service_comparable':service,'saving_comparable':comparable,
            'compressor_delta_kwh':round(median(electrical),3),
            'end_tank_heat_delta_kwh':round(median(heat),3),
            'saving_electric_kwh_model':round(-median(electrical),3) if comparable else None,
            'reason':'comparable' if comparable else 'different_terminal_state' if not equivalent else 'service_not_met'}


def public_scenario(row):
    return {key:value for key,value in row.items() if not key.startswith('_')}


def empty_plan(now,reason,status='cannot_evaluate'):
    return {'issued_at':stamp(now),'issued_epoch':now,'status':status,'reason':reason,
            'simulation_version':SIMULATION_VERSION,'shadow_only':True,'validated_for_control':False,
            'selected_variant':None,'window_start':None,'deadline':None,'latest_start':None,'finish_at':None,
            'expected_reserve_pct':None,'scenarios':[],'saving_czk':None}


def candidate_times(now,start,deadline,margin):
    """Scan the whole feasible window; feasibility need not be monotonic."""
    first=max(now,start);last=deadline-margin*60
    if last<first:return []
    rows={first,last}
    cursor=(int(first//(CANDIDATE_STEP_MIN*60))+1)*CANDIDATE_STEP_MIN*60
    while cursor<last:
        rows.add(cursor);cursor+=CANDIDATE_STEP_MIN*60
    return sorted(rows)


def make_plan(model,learner,settings,target,now,*,thermostat=None):
    try:settings.validate()
    except (ValueError,TypeError,KeyError) as exc:return empty_plan(now,str(exc))
    if not settings.enabled:return empty_plan(now,'Shadow výpočet je vypnutý','disabled')
    s=model.last
    if s is None or not model.particles or not s.valid() or not -5<=now-s.at<=GAP_S:
        return empty_plan(now,'Čekám na čerstvý úplný odečet T11/T12')
    if not finite(target) or not 5<=target<=60:return empty_plan(now,'Chybí platný aktuální cíl TUV')
    if target<=30:return empty_plan(now,'Pro model zásoby je nutný cíl kompresoru nad 30 °C')
    if s.auxiliary is True:return empty_plan(now,'Běží spirála; kompresorový scénář nelze oddělit','auxiliary_active')
    if s.auxiliary is None or s.compressor is None:return empty_plan(now,'Chybí potvrzený stav kompresoru nebo spirály')
    if s.mode not in MODES or model.phase in ('gap','mismatch','initializing'):
        return empty_plan(now,'Čekám na stabilní známý provozní režim')
    readiness=validation.evaluate(learner,target,s.mode,now,settings.top_min_c,settings.margin_min)
    settings=replace(settings,margin_min=min(180,readiness['effective_margin_min']))
    window=next_window(now,settings)
    start=max(now,window['start']);deadline=window['end']
    horizon=evaluation_horizon(now,window,settings)
    expected,stress=demand_profiles(learner,settings,now,horizon)
    cache={}
    def run(variant,at,profile):
        key=(variant,at,profile.kind)
        if key not in cache:
            cache[key]=simulate(model,learner,settings,target,now,at,deadline,variant,window['start'],
                                demand_profile=profile,horizon=horizon,thermostat=thermostat)
        return cache[key]
    normal=run('normal',now,expected);stress_normal=run('normal',now,stress)
    def feasible(row):
        return (row['reserve_met'] and row['deadline_met'] and row['post_use_comfort_met']
                and row['cancelled_paths']==0)
    def passing(variant,at):
        return feasible(run(variant,at,expected)) and feasible(run(variant,at,stress))
    def combined(variant,at):
        a=run(variant,at,expected);b=run(variant,at,stress)
        ea=public_scenario(a);eb=public_scenario(b)
        ea['energy_comparison']=compare_energy(a,normal)
        eb['energy_comparison']=compare_energy(b,stress_normal)
        # Existing consumers receive conservative guard booleans; detailed
        # expected/stress readings remain separate and clearly labelled.
        return {**ea,'expected':ea,'stress':eb,
                'reserve_met':a['reserve_met'] and b['reserve_met'],
                'deadline_met':a['deadline_met'] and b['deadline_met'],
                'post_use_comfort_met':a['post_use_comfort_met'] and b['post_use_comfort_met'],
                'cancelled_paths':a['cancelled_paths']+b['cancelled_paths'],
                'expected_profile_passes':feasible(a),'stress_profile_passes':feasible(b),
                'eligible':feasible(a) and feasible(b)}
    times=candidate_times(now,window['start'],deadline,settings.margin_min)
    selected_variant=None;selected_at=None;evaluations=[]
    # A failed early start is not evidence that all later starts fail: the tank
    # may cool after an early crossing, but retain heat after a later crossing.
    for variant in ('full_delay','partial'):
        accepted=[]
        for at in times:
            ok=passing(variant,at)
            evaluations.append({'variant':variant,'start_at':stamp(at),'passes':ok})
            if ok:accepted.append(at)
        if accepted:
            selected_variant=variant;selected_at=max(accepted)
            break
    if selected_variant is None and start>now+60 and normal['finish_epoch'] is not None:
        full=run('full_delay',start,expected)
        over=max(0,(full['finish_epoch'] or deadline+86400)-deadline+settings.margin_min*60)
        earlier=max(now,start-over)
        if now+60<earlier<start and passing('earlier',earlier):
            selected_variant='earlier';selected_at=earlier
            evaluations.append({'variant':'earlier','start_at':stamp(earlier),'passes':True})
    # Diagnose representative candidates as well as the earliest start.
    scenarios=[combined('normal',now),combined('full_delay',start),combined('partial',start)]
    if selected_variant is not None:
        selected=combined(selected_variant,selected_at)
        scenarios.append({**selected,'latest_start_candidate':True})
        status={'full_delay':'would_delay','partial':'would_top_up','earlier':'would_start_earlier'}[selected_variant]
        reason=('Odklad splňuje termín i následný komfort v běžném a zátěžovém průběhu'
                if selected_variant=='full_delay' else 'Pro dokončení je nutné začít před oknem'
                if selected_variant=='earlier' else 'Model vyžaduje doplnění rezervy před odkladem')
    else:
        selected=scenarios[0];status='would_not_delay'
        reason='Žádný prověřený start nesplnil rezervu, připravenost v termínu a následný komfort'
    summary=learner.summary()
    if not summary['learning_ready']:
        status='collecting';reason='Sbírám celé dny a průběhy dohřevu; doporučení odkladu zatím není ověřené'
    elif not readiness['ready']:
        status='validation_pending';reason='Odklad čeká na ověřenou chybu dohřevu a 72 hodin teplotního komfortu'
    return {'issued_at':stamp(now),'issued_epoch':now,'status':status,'reason':reason,
            'control_readiness':readiness,
            'simulation_version':SIMULATION_VERSION,'shadow_only':True,'validated_for_control':False,
            'selected_variant':selected['variant'],'window_kind':window['kind'],
            'window_start':stamp(window['start']),'deadline':stamp(deadline),
            'horizon_end':stamp(horizon),'post_use_period':'morning' if window['kind']=='night' else 'evening',
            'latest_start':stamp(selected_at),'latest_start_is_tested_candidate':True,
            'finish_at':selected['finish_at'],'expected_reserve_pct':selected['expected']['min_reserve_pct'],
            'target_c':target,'target_probe':'T12','settings':asdict(settings),'scenarios':scenarios,
            'stock_reference_c':target,'thermostat':thermostat,
            'demand_profiles':[p.public() for p in (expected,stress)],
            'candidate_step_min':CANDIDATE_STEP_MIN,'candidate_evaluations':evaluations,
            'initial_sample':asdict(s),'observer_samples':model.samples,'thermal_model_version':2,
            'initial_profiles':[asdict(p) for p in profiles(model)],
            'forecast_demand':[{'at':stamp(at),'tank_l_min':expected.rate(at,settings),
                                'stress_tank_l_min':stress.rate(at,settings),'source':expected.source}
                               for at in range(int(now),int(horizon)+1,1800)],
            'frozen_demand_profiles':[asdict(p) for p in (expected,stress)],
            'learning':summary,'saving_czk':None,
            'demand_source':'Souvislý typický den a zátěžový den × 1,2; při nedostatku dnů explicitní předpoklad',
            'assumptions':('Podmíněný start, pozorovaná hystereze a její nejistota; při nedostatku dat předpoklad 3 °C. '
                           'Příkon 450 W; zachován současný požadavek domu. Ráno do 09:00, večer do 23:00; bez záruky spuštění firmwarem či komfortu'),
            'distribution_is_calibrated':False}
