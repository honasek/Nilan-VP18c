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

from .tuv_model import finite, soc, predict, CP_WH_L_K, CELL_L

VERSION=1
MODES=('HOTWATER','HEATING','COOLING')
STEP=5
MAX_PROFILES=24
GAP_S=300
RETENTION_DAYS=45


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
        local=datetime.fromtimestamp(at,ZoneInfo(settings.timezone))
        index=local.hour*2+local.minute//30
        values=[row['litres'][index]*30/row['minutes'][index]
                for day,row in self.days.items() if day<local.date().isoformat() and row['minutes'][index]>=20]
        prior=prior_rate(at,settings)
        # Distinct days, not repeated identical readings, provide evidence.
        if len(values)>=3:return max(prior,quantile(values,.9)*1.2/30),'observed_model'
        return prior,'prior'

    def summary(self):
        complete=sum(sum(row['minutes'])>=1080 and sum(row['minutes'][12:18])>=135
                     and sum(row['minutes'][36:46])>=225 for row in self.days.values())
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
                'gaps':self.gaps,'samples':self.samples,'revision':self.revision}

    @classmethod
    def restore(cls,data,now):
        from .tuv_model import Sample
        if not isinstance(data,dict) or data.get('version')!=VERSION:raise ValueError('Invalid shadow version')
        result=cls()
        if not isinstance(data.get('days'),dict) or len(data['days'])>47:raise ValueError('Invalid shadow days')
        for day,row in data['days'].items():
            datetime.strptime(day,'%Y-%m-%d')
            if not isinstance(row,dict) or set(row)!= {'minutes','litres'}:raise ValueError('Invalid demand row')
            for key,limit in (('minutes',65),('litres',2000)):
                if not isinstance(row[key],list) or len(row[key])!=48 or any(not finite(v) or not 0<=v<=limit for v in row[key]):
                    raise ValueError('Invalid demand bins')
        for key,limit in (('rates',256),('sessions',128),('pending',48),('scores',256)):
            if not isinstance(data.get(key),list) or len(data[key])>limit:raise ValueError('Invalid shadow evidence')
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


def simulate(model,learner,settings,target,now,start,deadline,variant,price_start=None):
    """Frozen initial state evolves independently through waiting, draws and heating.

    This simplified thermostat is a scenario assumption, not emulated firmware.
    Heat needed for the currently observed house mode is never suppressed.
    """
    fan=model.fan_baselines.get(model.last.fan_key,{}).get('watts',60)
    house=model.last.compressor is True and model.last.mode in ('HEATING','COOLING')
    price_start=start if price_start is None else price_start
    end=min(now+28*3600,deadline+4*3600)
    rates=[];cursor=now
    while cursor<end:
        until=min(cursor+STEP*60,end,start if cursor<start<cursor+STEP*60 else end,
                  deadline if cursor<deadline<cursor+STEP*60 else end)
        rate,source=learner.demand((cursor+until)/2,settings)
        rates.append((cursor,until,rate,source));cursor=until
    completions=[];minimums=[];tops=[];on_minutes=[];window_minutes=[];cancelled=[];deadline_temperatures=[]
    for p in profiles(model):
        min_stock=soc(p.temperatures,p.temperatures[-1]);min_top=p.temperatures[-1]
        full=variant=='normal'
        wait_litres=sum(draw*max(0,min(last,start)-first)/60 for first,last,draw,_ in rates if first<start)
        reserve_goal=min(100,settings.reserve_pct+5+wait_litres*max(0,target-p.inlet_c)/(180*30)*100)
        top_goal=min(target,settings.top_min_c+2)
        reserve_phase=variant=='partial' and (min_stock<reserve_goal or min_top<top_goal)
        water_on=model.last.compressor is True and model.last.mode=='HOTWATER'
        completion=now if full and p.temperatures[0]>=target else None
        total=cheap=0.0;cancel=None;deadline_bottom=None
        for first,last,draw,_ in rates:
            dt=(last-first)/60
            if first>=start and not full:
                full=True
                # Conditional continuous compressor heating after the candidate
                # start, not a promise that restoring a setpoint forces firmware.
                water_on=p.temperatures[0]<target
            if variant!='normal' and not full and not reserve_phase and (soc(p.temperatures,p.temperatures[-1])<settings.reserve_pct or p.temperatures[-1]<settings.top_min_c):
                # Cancel once; never re-postpone this virtual heating cycle.
                full=True;cancel=first
            desired=target if full else max(5,min(target,settings.top_min_c))
            if reserve_phase:
                if soc(p.temperatures,p.temperatures[-1])>=reserve_goal and p.temperatures[-1]>=top_goal:
                    reserve_phase=False;water_on=False
                elif p.temperatures[0]>=target:
                    reserve_phase=False;water_on=False
                else:water_on=True
            elif full:
                if p.temperatures[0]>=desired:water_on=False
                elif p.temperatures[0]<desired-3 or water_on:water_on=True
            else:water_on=False
            heating=water_on or house
            # Do not assume a switch to faster water-only heat at the expense
            # of a currently observed heating/cooling requirement for the house.
            mode=model.last.mode if house else 'HOTWATER'
            # An idle socket reading must never become a future compressor's input.
            virtual=replace(model.last,at=first,compressor=heating,auxiliary=False,mode=mode,
                            power_w=None,power_quality='assumed')
            p.temperatures=predict(p,virtual,dt,draw,fan,max_step=1.0)
            if water_on:
                total+=dt
                cheap+=max(0,min(last,deadline)-max(first,price_start))
            if first<deadline:
                min_stock=min(min_stock,soc(p.temperatures,p.temperatures[-1]));min_top=min(min_top,p.temperatures[-1])
            if full and completion is None and p.temperatures[0]>=target:completion=last
            if first<deadline<=last:deadline_bottom=p.temperatures[0]
            if last>=deadline and completion is not None:break
        completions.append(float('inf') if completion is None else completion)
        minimums.append(min_stock);tops.append(min_top);on_minutes.append(total);window_minutes.append(cheap/60)
        cancelled.append(cancel)
        deadline_temperatures.append(float('-inf') if deadline_bottom is None else deadline_bottom)
    upper=quantile(completions,.9)
    # Include all selected profiles, including paths that never reached the target.
    low=quantile(minimums,.1);top=quantile(tops,.1);deadline_bottom=quantile(deadline_temperatures,.1)
    return {'variant':variant,'start_at':stamp(start),'finish_at':stamp(upper),
            'finish_epoch':upper if finite(upper) else None,
            'min_reserve_pct':round(low,1),'min_top_c':round(top,1),
            'reserve_met':low>=settings.reserve_pct and top>=settings.top_min_c,
            # T11/T12 report whole degrees. Allow half a degree for model floats,
            # but do not call an early crossing a success after a later draw.
            'deadline_met':finite(upper) and upper<=deadline-settings.margin_min*60 and deadline_bottom>=target-.5,
            'deadline_t12_c':round(deadline_bottom,1) if finite(deadline_bottom) else None,
            'target_tolerance_c':.5,
            'cancelled_paths':sum(x is not None for x in cancelled),
            'reaching_paths':sum(finite(x) for x in completions),'profiles':len(completions),
            'compressor_electric_kwh_model':round(median(on_minutes)*.45/60,3),
            'window_electric_kwh_model':round(median(window_minutes)*.45/60,3),
            'saving_czk':None,'energy_scope':'Modelový příkon požadavku TUV 450 W; bez ocenění a bez alokace souběhu pro dům'}


def empty_plan(now,reason,status='cannot_evaluate'):
    return {'issued_at':stamp(now),'issued_epoch':now,'status':status,'reason':reason,
            'shadow_only':True,'validated_for_control':False,'selected_variant':None,
            'window_start':None,'deadline':None,'latest_start':None,'finish_at':None,
            'expected_reserve_pct':None,'scenarios':[],'saving_czk':None}


def make_plan(model,learner,settings,target,now):
    try:settings.validate()
    except (ValueError,TypeError,KeyError) as exc:return empty_plan(now,str(exc))
    if not settings.enabled:return empty_plan(now,'Shadow výpočet je vypnutý','disabled')
    s=model.last
    if s is None or not model.particles or not s.valid() or not -5<=now-s.at<=GAP_S:
        return empty_plan(now,'Čekám na čerstvý úplný odečet T11/T12')
    if not finite(target) or not 5<=target<=60:return empty_plan(now,'Chybí platný aktuální cíl TUV')
    if s.auxiliary is True:return empty_plan(now,'Běží spirála; kompresorový scénář nelze oddělit','auxiliary_active')
    if s.auxiliary is None or s.compressor is None:return empty_plan(now,'Chybí potvrzený stav kompresoru nebo spirály')
    if s.mode not in MODES or model.phase in ('gap','mismatch','initializing'):
        return empty_plan(now,'Čekám na stabilní známý provozní režim')
    window=next_window(now,settings)
    start=max(now,window['start']);deadline=window['end']
    normal=simulate(model,learner,settings,target,now,now,deadline,'normal',window['start'])
    full=simulate(model,learner,settings,target,now,start,deadline,'full_delay',window['start'])
    partial=simulate(model,learner,settings,target,now,start,deadline,'partial',window['start'])
    scenarios=[normal,full,partial]
    selected=None
    for row in (full,partial):
        if row['reserve_met'] and row['deadline_met'] and row['cancelled_paths']==0:selected=row;break
    latest=None
    if selected is not None:
        # Bounded search: report only a fully simulated passing start, rounded
        # conservatively to five minutes; unresolved later starts are not advertised.
        lo=start;hi=deadline-settings.margin_min*60
        for _ in range(3):
            middle=int(((lo+hi)/2)//300)*300
            if middle<=lo or middle>=hi:break
            candidate=simulate(model,learner,settings,target,now,middle,deadline,selected['variant'],window['start'])
            if candidate['reserve_met'] and candidate['deadline_met'] and candidate['cancelled_paths']==0:
                lo=middle;selected=candidate
            else:hi=middle
        latest=selected['start_at']
        scenarios.append({**selected,'latest_start_candidate':True})
    elif normal['finish_epoch'] is not None:
        # Earlier candidate includes simulated waiting losses and draws when verified.
        over=max(0,(full['finish_epoch'] or deadline+86400)-deadline+settings.margin_min*60)
        candidate=max(now,start-over)
        if now+60<candidate<start:
            earlier=simulate(model,learner,settings,target,now,candidate,deadline,'earlier',window['start'])
            scenarios.append(earlier)
            if earlier['reserve_met'] and earlier['deadline_met'] and earlier['cancelled_paths']==0:
                selected=earlier;latest=earlier['start_at']
    summary=learner.summary()
    enough=summary['learning_ready']
    if selected:
        status={'full_delay':'would_delay','partial':'would_top_up','earlier':'would_start_earlier'}[selected['variant']]
        reason={'full_delay':'Model připouští odklad zbývajícího ohřevu',
                'partial':'Model vyžaduje doplnění rezervy nyní a pozdější dokončení',
                'earlier':'Pro dokončení je nutné začít před levným oknem'}[selected['variant']]
    else:
        selected=normal;status='would_not_delay'
        reason='Odklad nesplnil modelovou rezervu nebo čas; běžný ohřev má přednost'
    if not enough:
        status='collecting';reason='Sbírám celé dny a průběhy dohřevu; doporučení odkladu zatím není ověřené'
    return {'issued_at':stamp(now),'issued_epoch':now,'status':status,'reason':reason,
            'shadow_only':True,'validated_for_control':False,'selected_variant':selected['variant'],
            'window_kind':window['kind'],'window_start':stamp(window['start']),'deadline':stamp(deadline),
            'latest_start':latest,'latest_start_is_tested_candidate':True,
            'finish_at':selected['finish_at'],'expected_reserve_pct':selected['min_reserve_pct'],
            'target_c':target,'target_probe':'T12','settings':asdict(settings),'scenarios':scenarios,
            'initial_sample':asdict(s),'observer_samples':model.samples,'thermal_model_version':2,
            'initial_profiles':[asdict(p) for p in profiles(model)],
            'forecast_demand':[{'at':stamp(at),'tank_l_min':learner.demand(at,settings)[0],
                                'source':learner.demand(at,settings)[1]}
                               for at in range(int(now),int(min(now+28*3600,deadline+4*3600))+1,1800)],
            'learning':summary,'saving_czk':None,'demand_source':'Modelově odhadnuté odběry; pro chybějící půlhodiny explicitní denní předpoklad',
            'assumptions':'Podmíněný souvislý ohřev po startu, následná hystereze 3 °C, kompresor 450 W, zachovaný současný požadavek domu; spuštění firmwarem ani komfort nejsou zaručené',
            'distribution_is_calibrated':False}
