"""Passive DHW thermostat evidence, distinct from physical compressor activity.

No I/O or HA dependencies. Integer probes and delayed samples bracket thresholds;
they do not reveal an exact firmware setting. No deliberate heating is requested.
"""
from copy import deepcopy
from dataclasses import asdict
from statistics import median
from .tuv_model import Sample,finite

VERSION=1
MAX_GAP=300
MAX_EVENTS=192
MODES=('HOTWATER','HEATING','COOLING')


class ThermostatObserver:
    def __init__(self):
        self.previous=None
        self.target=None
        self.reached={'T11':False,'T12':False}
        self.events=[]
        self.samples=0

    def observe(self,sample,target,*,inhibited=False):
        if not sample.valid():return
        if self.previous is not None and sample.at<=self.previous['sample']['at']:return
        qualified=(finite(target) and 30<target<=60 and not inhibited
                   and sample.auxiliary is False and sample.compressor is not None and sample.mode in MODES)
        if target!=self.target or not qualified:
            self.reached={'T11':False,'T12':False}
        before=self.previous
        if before is not None and sample.at-before['sample']['at']>MAX_GAP:
            # An unobserved start/stop may have occurred during a telemetry gap.
            # Keep threshold evidence, but require completion to be seen again.
            self.reached={'T11':False,'T12':False}
        continuous=(before is not None and before['qualified'] and qualified and before['target']==target
                    and 0<sample.at-before['sample']['at']<=MAX_GAP)
        if continuous:
            old=Sample(**before['sample'])
            starting=old.compressor is False and sample.compressor is True and sample.mode=='HOTWATER'
            stopping=old.compressor is True and sample.compressor is False and old.mode=='HOTWATER'
            if starting:
                drop=old.bottom-sample.bottom
                # Rapid draw crosses a broad temperature interval and cannot
                # identify a start threshold. Keep it as censored evidence.
                clean=0<=drop<=1 and 0<=target-max(old.bottom,sample.bottom)<=8
                self.events.append({'at':sample.at,'kind':'start','target':target,
                                    'before':old.bottom,'after':sample.bottom,'gap_s':sample.at-old.at,
                                    'qualified':clean})
            if stopping:
                clean=(max(old.bottom,sample.bottom)>=target
                       and min(old.bottom,sample.bottom)>=target-1
                       and max(old.bottom,sample.bottom)<=target+2)
                self.events.append({'at':sample.at,'kind':'stop','target':target,
                                    'before':old.bottom,'after':sample.bottom,'gap_s':sample.at-old.at,
                                    'qualified':clean})
        self.events=[e for e in self.events if e['at']>=sample.at-45*86400][-MAX_EVENTS:]
        self.target=target
        if qualified:
            # A new water-heating request reopens the cycle even if a previous
            # cycle had reached the target. House heating alone does not.
            if (sample.compressor is True and sample.mode=='HOTWATER'
                    and (not continuous or before['sample']['compressor'] is False
                         or before['sample']['mode']!='HOTWATER')):
                self.reached={'T11':False,'T12':False}
            estimate=self.estimate(target)
            floor=estimate['restart_range_c'][1] if estimate['restart_range_c'] else target-1.5
            for probe,temp in (('T11',sample.top),('T12',sample.bottom)):
                if temp>=target:self.reached[probe]=True
                elif temp<=floor:self.reached[probe]=False
        self.previous={'sample':asdict(sample),'target':target,'qualified':qualified,'inhibited':bool(inhibited)}
        self.samples+=1

    def _group(self,target):
        starts=[e for e in self.events if e['qualified'] and e['target']==target and e['kind']=='start']
        stops=[e for e in self.events if e['qualified'] and e['target']==target and e['kind']=='stop']
        return starts,stops

    def estimate(self,target):
        empty={'status':'collecting','target_c':target,'source_target_c':None,'restart_drop_c':None,
               'hysteresis_c':None,'restart_c':None,'restart_range_c':None,'observed_stop_c':None,
               'start_samples':0,'stop_samples':0,'current_target_starts':0,'current_target_stops':0,
               'resolution_uncertainty_c':1.0,'source':'passive_samples','exact_firmware_setting':False}
        if not finite(target) or not 30<target<=60:return empty
        current_starts,current_stops=self._group(target)
        empty.update(current_target_starts=len(current_starts),current_target_stops=len(current_stops),
                     observed_stop_c=median(e['after'] for e in current_stops) if current_stops else None)
        groups=[]
        for value in sorted({e['target'] for e in self.events if e['qualified']}):
            starts,stops=self._group(value)
            if len(starts)>=3 and len(stops)>=3:
                groups.append((value,starts,stops))
        own=next((g for g in groups if g[0]==target),None)
        chosen=own or (max(groups,key=lambda g:max(e['at'] for e in g[1]+g[2])) if groups else None)
        if chosen is None:return empty
        source,starts,stops=chosen
        drop=median(e['target']-max(e['before'],e['after']) for e in starts)
        # Do not transfer a mature cohort if new direct observations contradict it.
        if source!=target and current_starts and abs(median(e['target']-max(e['before'],e['after']) for e in current_starts)-drop)>1:
            return empty
        low=max(0.5,drop-1);high=drop+1
        stop_offset=median(e['after']-e['target'] for e in stops)
        return {**empty,'status':'measured' if own else 'transferred','source_target_c':source,
                'restart_drop_c':round(drop,2),'hysteresis_c':round(drop+stop_offset,2),
                'restart_c':round(target-drop,2),'restart_range_c':[round(target-high,2),round(target-low,2)],
                'start_samples':len(starts),'stop_samples':len(stops),
                'median_transition_gap_s':round(median(e['gap_s'] for e in starts+stops),1)}

    def snapshot(self,target):
        estimate=self.estimate(target)
        last=self.previous
        usable=(last is not None and last['qualified'] and target==self.target)
        held={probe:False for probe in ('T11','T12')}
        if usable:
            s=Sample(**last['sample'])
            idle_for_water=not (s.compressor is True and s.mode=='HOTWATER')
            floor=estimate['restart_range_c'][1] if estimate['restart_range_c'] else target-1.5
            for probe,temp in (('T11',s.top),('T12',s.bottom)):
                held[probe]=(idle_for_water and self.reached[probe] and temp>floor)
        return {**estimate,'reached_probes':held,'samples':self.samples,
                'last_sample':None if last is None else last['sample']['at']}

    def dump(self):
        return {'version':VERSION,'previous':deepcopy(self.previous),'target':self.target,
                'reached':dict(self.reached),'events':deepcopy(self.events),'samples':self.samples}

    @classmethod
    def restore(cls,data,now):
        if not isinstance(data,dict) or data.get('version')!=VERSION:raise ValueError('Invalid thermostat version')
        obj=cls()
        if type(data.get('samples')) is not int or data['samples']<0:raise ValueError('Invalid thermostat samples')
        target=data.get('target')
        if target is not None and (not finite(target) or not 5<=target<=60):raise ValueError('Invalid thermostat target')
        reached=data.get('reached')
        if not isinstance(reached,dict) or set(reached)!= {'T11','T12'} or any(type(v) is not bool for v in reached.values()):raise ValueError('Invalid reached flags')
        events=data.get('events')
        if not isinstance(events,list) or len(events)>MAX_EVENTS:raise ValueError('Invalid thermostat events')
        for e in events:
            if (not isinstance(e,dict) or e.get('kind') not in ('start','stop') or type(e.get('qualified')) is not bool
                    or any(not finite(e.get(k)) for k in ('at','target','before','after','gap_s'))
                    or not 0<e['at']<=now+5 or not 30<e['target']<=60
                    or not 0<=e['before']<=95 or not 0<=e['after']<=95 or not 0<e['gap_s']<=MAX_GAP):
                raise ValueError('Invalid thermostat event')
        previous=data.get('previous')
        if previous is not None:
            if not isinstance(previous,dict) or type(previous.get('qualified')) is not bool or type(previous.get('inhibited')) is not bool:raise ValueError('Invalid thermostat context')
            sample=Sample(**previous['sample'])
            if not sample.valid() or sample.at>now+5 or previous.get('target')!=target:raise ValueError('Invalid thermostat sample')
            if events and max(e['at'] for e in events)>sample.at:raise ValueError('Future thermostat event')
        obj.previous=deepcopy(previous);obj.target=target;obj.reached=dict(reached);obj.events=deepcopy(events);obj.samples=data['samples']
        return obj


def bootstrap(archive,cutoff=None):
    """Replay actual archived samples once; never simulate measurements."""
    import json
    obj=ThermostatObserver()
    for path in sorted(archive.glob('samples-????-??-??.jsonl'))[-45:]:
        if path.is_symlink() or not path.is_file():continue
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row=json.loads(line);sample=Sample(**row['sample'])
                    if cutoff is not None and sample.at>cutoff:continue
                    obj.observe(sample,row.get('target_c'),inhibited=row.get('thermostat_inhibited',False))
                except (ValueError,TypeError,KeyError):continue
    return obj
