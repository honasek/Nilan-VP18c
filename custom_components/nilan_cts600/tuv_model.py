"""Read-only, adaptive six-volume thermal observer. No HA or hardware dependencies.

The ensemble interval describes model ambiguity, NOT a calibrated confidence
interval. Unknown draw and heat partition are not identifiable from two probes.
Temperatures are ordered from the bottom cell to the top cell. Calibration is
passive: no commands or deliberate draws/heating are ever performed.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import exp, isfinite, log, ceil
import random
from statistics import fmean, median
from copy import deepcopy

VERSION = 2
LAYERS = 6
TANK_L = 180.0
CELL_L = TANK_L / LAYERS
CP_WH_L_K = 1.163
EMPTY_C = 30.0
FULL_C = 60.0
COUNT = 96


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def clamp(value, low, high):
    return max(low, min(high, value))


def soc(temperatures, top):
    if top <= EMPTY_C:
        return 0.0
    return clamp(100 * sum(max(t - EMPTY_C, 0) for t in temperatures) / (LAYERS * (FULL_C - EMPTY_C)), 0, 100)


def mix_unstable(temperatures):
    """Pool adjacent unstable equal-volume cells, preserving total heat."""
    blocks = []
    for temp in temperatures:
        blocks.append([temp, 1])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            b = blocks.pop()
            a = blocks.pop()
            blocks.append([(a[0] * a[1] + b[0] * b[1]) / (a[1] + b[1]), a[1] + b[1]])
    return [temp for temp, count in blocks for _ in range(count)]


def advect(temperatures, liters, inlet):
    """Conservative upwind displacement, substepped to avoid overshooting."""
    out = list(temperatures)
    while liters > 1e-9:
        part = min(liters, CELL_L * 0.25)
        f = part / CELL_L
        old = out
        out = [old[i] + f * ((inlet if i == 0 else old[i - 1]) - old[i]) for i in range(LAYERS)]
        liters -= part
    return out


@dataclass(frozen=True)
class Sample:
    at: float
    top: float
    bottom: float
    compressor: bool | None
    auxiliary: bool | None
    mode: str
    power_w: float | None
    ambient: float = 20.0
    fan_key: str = "unknown"
    power_quality: str = "fresh"
    power_reported_at: float | None = None
    power_age_s: float | None = None
    power_reason: str | None = None

    def valid(self):
        return (finite(self.at) and self.at > 0 and finite(self.top) and finite(self.bottom)
                and 0 <= self.bottom <= 95 and 0 <= self.top <= 95
                and self.bottom <= self.top + 3
                and type(self.compressor) in (bool, type(None))
                and type(self.auxiliary) in (bool, type(None))
                and isinstance(self.mode, str) and len(self.mode) <= 32
                and (self.power_w is None or finite(self.power_w) and 0 <= self.power_w <= 10000)
                and finite(self.ambient) and -10 <= self.ambient <= 50
                and isinstance(self.fan_key, str) and len(self.fan_key) <= 20
                and self.power_quality in ('fresh','held','assumed')
                and (self.power_reported_at is None or finite(self.power_reported_at) and 0<self.power_reported_at<=self.at+5)
                and (self.power_age_s is None or finite(self.power_age_s) and 0<=self.power_age_s<=1e9)
                and (self.power_reason is None or isinstance(self.power_reason,str) and len(self.power_reason)<=200))

    @property
    def fresh_power(self):
        return self.power_w is not None and self.power_quality=='fresh'


@dataclass
class Particle:
    temperatures: list[float]
    gains: list[float]  # effective heat-to-water/electric ratios, not physical COP
    loss_w_k: float
    mixing: float
    inlet_c: float
    coil_shape: float
    draw_l_min: float = 0.0


def predict(particle, sample, minutes, draw, fan_w, *, max_step=.25):
    """Thermal balance before measurement correction; all powers in watts."""
    p = particle
    temperatures = list(p.temperatures)
    aux_w = 1500.0 if sample.auxiliary is True else 0.0  # VP18 nominal, not a new measurement
    if sample.power_w is not None:
        electric = max(0, sample.power_w - fan_w - aux_w)
    else:
        electric = 450.0  # uncertain prior; absence is exposed in quality/interval
    compressor_w = 0.0
    if sample.compressor is not False:
        index = {"HOTWATER": 0, "HEATING": 1, "COOLING": 2}.get(sample.mode)
        gain = p.gains[index] if index is not None else fmean(p.gains)
        compressor_w = electric * gain * (1 if sample.compressor is True else 0.5)
    bottom_coil = [0.30, 0.27, 0.22, 0.14, 0.05, 0.02]
    broad_coil = [0.19, 0.19, 0.19, 0.17, 0.15, 0.11]
    coil = [(1-p.coil_shape)*a + p.coil_shape*b for a,b in zip(bottom_coil,broad_coil)]
    remaining = minutes
    while remaining > 1e-9:
        dt = min(remaining, max_step)
        temperatures = advect(temperatures, draw * dt, p.inlet_c)
        old = temperatures[:]
        for i in range(LAYERS):
            aux_share = (0.35 if i == 4 else 0.65 if i == 5 else 0)
            net_w = compressor_w * coil[i] + aux_w * aux_share - p.loss_w_k / LAYERS * (old[i] - sample.ambient)
            temperatures[i] += net_w * dt / 60 / (CELL_L * CP_WH_L_K)
        # Pairwise conduction/mixing is energy conserving.
        for i in range(LAYERS-1):
            transfer = (old[i+1]-old[i]) * clamp(p.mixing*dt, 0, 0.2)
            temperatures[i] += transfer
            temperatures[i+1] -= transfer
        temperatures = mix_unstable(temperatures)
        remaining -= dt
    return temperatures


class ThermalObserver:
    def __init__(self):
        self.particles = []
        self.last = None
        self.samples = 0
        self.heating_minutes = 0.0
        self.calm_minutes = 0.0
        self.draw_events = 0
        self.draw_minutes = 0.0
        self.active_draw = False
        self.last_draw_at = None
        self.gaps = 0
        self.adaptations = 0
        self.fan_baselines = {}
        self.residual_c = 0.0
        self.phase = "initializing"
        self.last_snapshot = None
        self.learning={mode:{'minutes':0.0,'reports':[],'signal_c':0.0,'updates':0,'last_update_report':0.0} for mode in ('HOTWATER','HEATING','COOLING')}
        self.loss_window_minutes=0.0
        self.loss_window_start=None
        self.loss_learning_minutes=0.0
        self.loss_updates=0
        self.fan_reports={}
        self.learning_reason='Čekám na vhodné úseky a nezávislá hlášení příkonu'
        self.last_draw_evidence_at=None
        self.draw_probability=0.0
        self.stock_trend='steady'
        self.legacy_adaptations=0
        self.migrated_from=None
        self.recent=[]

    def initialize(self, sample, preserve_parameters=True):
        rng = random.Random(61018 + self.samples)
        old = self.particles if preserve_parameters else []
        particles = []
        low, high = min(sample.bottom, sample.top), max(sample.bottom, sample.top)
        for i in range(COUNT):
            center = 0.02 + 0.96 * ((i + 0.5) / COUNT)
            width = rng.uniform(0.035, 0.22)
            sigmoid = lambda z: 1 / (1 + exp(clamp(-(z-center)/width, -60, 60)))
            a, b = sigmoid(0), sigmoid(1)
            temps = [low + (high-low) * (sigmoid(j/(LAYERS-1))-a)/(b-a) for j in range(LAYERS)]
            if old:
                p = old[i % len(old)]
                gains, loss, mixing, inlet, shape = p.gains[:], p.loss_w_k, p.mixing, p.inlet_c, p.coil_shape
            else:
                gains = [rng.uniform(.8,3.8), rng.uniform(.05,1.8), rng.uniform(.3,3.5)]
                loss, mixing = rng.uniform(.6,3.0), rng.uniform(.001,.025)
                inlet, shape = rng.uniform(7,15), rng.random()
            particles.append(Particle(temps,gains,loss,mixing,inlet,shape))
        self.particles = particles
        self.last = sample

    def step(self, sample):
        if not sample.valid():
            raise ValueError("Neplatné vstupní teploty, čas nebo signály")
        if self.last is not None and sample.at <= self.last.at:
            return self.last_snapshot
        delta = None if self.last is None else (sample.at-self.last.at)/60
        if self.last is None or delta > 5:
            if self.last is not None:
                self.gaps += 1
            self.initialize(sample)
            self.phase = "initializing" if self.samples == 0 else "gap"
            self.active_draw = False
            self.loss_window_minutes=0;self.loss_window_start=None
            self.recent=[[sample.at,sample.bottom,sample.top]]
        elif delta < .25:
            return self.last_snapshot  # no duplicate/five-second pseudo-samples
        else:
            previous = self.last
            fan = self.fan_baselines.get(previous.fan_key, {}).get("watts", 60.0)
            rate_bottom = (sample.bottom-previous.bottom)/delta
            rate_top = (sample.top-previous.top)/delta
            recent=[r for r in self.recent if sample.at-r[0]<=300]
            anchor=recent[0] if recent else [previous.at,previous.bottom,previous.top]
            drop_bottom=anchor[1]-sample.bottom
            drop_top=anchor[2]-sample.top
            # Candidate draw and no-draw paths are retained even with a cold bottom.
            # A plateau at T12 never proves that drawing has stopped.
            candidates = []
            weights = []
            rng = random.Random(9721 + self.samples)
            for p in self.particles:
                rates = [0.0, max(0.5,p.draw_l_min), 2.5, 6.0, 12.0]
                for index, draw in enumerate(rates):
                    temps = predict(p, previous, delta, draw, fan)
                    error0 = max(abs(temps[0]-sample.bottom)-.6,0)
                    error5 = max(abs(temps[-1]-sample.top)-.6,0)
                    prior = (0.50 if index == 0 else .125) if p.draw_l_min < .5 else (.22 if index == 0 else .195)
                    if rate_bottom < -.6:
                        prior *= .2 if index == 0 else 1.8
                    score = log(prior) - (error0**2+error5**2)/(2*1.25**2)
                    candidates.append((p,temps,draw))
                    weights.append(score)
            largest = max(weights)
            weights = [exp(w-largest) for w in weights]
            total = sum(weights)
            weights = [w/total for w in weights]
            self.residual_c = sum(w * (abs(t[0]-sample.bottom)+abs(t[-1]-sample.top))/2 for w,(_,t,_) in zip(weights,candidates))
            estimated_draw = sum(w*c[2] for w,c in zip(weights,candidates))
            self.draw_probability=sum(w for w,c in zip(weights,candidates) if c[2]>=.8)
            zero_top=median(t[-1] for _,t,draw in candidates if draw==0)
            bottom_evidence=drop_bottom>=1.5 and rate_bottom<-.15 and estimated_draw>.8 and self.draw_probability>.45
            top_evidence=drop_top>=1.5 and rate_top<-.15 and zero_top-sample.top>.6 and estimated_draw>.8 and self.draw_probability>.6
            evidence=bottom_evidence or top_evidence
            if evidence:self.last_draw_evidence_at=sample.at
            continuing=(self.last_draw_evidence_at is not None and sample.at-self.last_draw_evidence_at<=180
                        and self.draw_probability>.45 and estimated_draw>.8 and rate_bottom<.05 and rate_top<.05)
            drawing=evidence or continuing
            if drawing:
                if self.last_draw_at is None or sample.at-self.last_draw_at>900:
                    self.draw_events += 1
                self.last_draw_at = sample.at
            self.active_draw = drawing
            if drawing:self.draw_minutes += delta
            heating = previous.compressor is True or previous.auxiliary is True
            if heating:self.heating_minutes += delta
            calm=(not heating and sample.compressor is False and sample.auxiliary is False and not drawing
                  and estimated_draw<.8 and abs(sample.bottom-previous.bottom)<=1.05 and abs(sample.top-previous.top)<=1.05
                  and abs(drop_bottom)<=1.1 and abs(drop_top)<=1.1)
            if calm:self.calm_minutes += delta
            # Keep unexcited parameter marginals intact. Temperature corrections
            # and parameter identification are separate operations.
            learn_mode=None
            clean=(not drawing and estimated_draw<.4 and rate_bottom>-.15 and rate_top>-.15)
            heat_clean=(clean and previous.compressor is True and sample.compressor is True
                        and previous.auxiliary is False and sample.auxiliary is False
                        and previous.mode==sample.mode and previous.mode in self.learning
                        and previous.fan_key==sample.fan_key and previous.fresh_power and sample.fresh_power)
            self.learning_reason='Učení pozastaveno: odběr, změna provozu nebo nejistý příkon'
            if heat_clean:
                evidence_row=self.learning[sample.mode]
                evidence_row['minutes']+=delta
                evidence_row['signal_c']+=max(0,sample.top-previous.top)+max(0,sample.bottom-previous.bottom)
                report=sample.power_reported_at if sample.power_reported_at is not None else sample.at
                if report not in evidence_row['reports']:evidence_row['reports']=(evidence_row['reports']+[report])[-64:]
                self.learning_reason='Sbírám čerstvá hlášení a průběh ohřevu; parametry zatím zachovány'
                if (evidence_row['minutes']>=20 and len(evidence_row['reports'])>=3
                    and evidence_row['signal_c']>=1 and report>evidence_row['last_update_report']):
                    learn_mode={'HOTWATER':0,'HEATING':1,'COOLING':2}[sample.mode]
                    evidence_row['updates']+=1;evidence_row['last_update_report']=report
                    self.learning_reason='Úprava koeficientu pozorovaného režimu podle čerstvých dat'
            loss_adapt=False
            if calm and previous.compressor is False and previous.auxiliary is False and sample.compressor is False and sample.auxiliary is False:
                if self.loss_window_start is None:self.loss_window_start=[previous.bottom,previous.top]
                self.loss_window_minutes+=delta
                self.loss_learning_minutes+=delta
                signal=sum(abs(t-s) for t,s in zip((sample.bottom,sample.top),self.loss_window_start))
                loss_adapt=self.loss_window_minutes>=30 and signal>=1
                self.learning_reason='Pozoruji klidový úsek; ztráty se upraví po 30 minutách a měřitelném ochlazení'
                if loss_adapt:
                    self.loss_updates+=1;self.loss_window_minutes=0;self.loss_window_start=[sample.bottom,sample.top]
                    self.learning_reason='Úprava ztrát podle souvislého klidového úseku'
            else:self.loss_window_minutes=0;self.loss_window_start=None
            if learn_mode is not None or loss_adapt:self.adaptations+=1
            cumulative = []
            acc = 0
            for w in weights:
                acc += w
                cumulative.append(acc)
            new = []
            pointer = 0
            offset = rng.random()/COUNT
            for i in range(COUNT):
                q = offset+i/COUNT
                while pointer < len(cumulative)-1 and cumulative[pointer] < q:pointer += 1
                p,t,draw = candidates[pointer]
                t = t[:]
                t[0] = .85*sample.bottom+.15*t[0]
                t[-1] = .85*sample.top+.15*t[-1]
                t = mix_unstable(t)
                anchor=self.particles[i]
                gains=anchor.gains[:]
                loss,mixing=anchor.loss_w_k,anchor.mixing
                if learn_mode is not None:gains[learn_mode]=clamp(p.gains[learn_mode]*exp(rng.gauss(0,.02)),.02,4.5)
                if loss_adapt:loss=clamp(p.loss_w_k*exp(rng.gauss(0,.015)),.2,5)
                new.append(Particle(t,gains,loss,mixing,anchor.inlet_c,anchor.coil_shape,draw))
            self.particles = new
            if self.residual_c > 8:
                self.initialize(sample)
                self.phase = "mismatch"
            elif drawing:self.phase = "draw_heating" if sample.compressor is True or sample.auxiliary is True else "draw"
            elif sample.compressor is True or sample.auxiliary is True:self.phase = "heating"
            else:self.phase = "idle"
            self.last = sample
            self.recent=(recent+[[sample.at,sample.bottom,sample.top]])[-12:]
        # Electrical fan baseline can genuinely be learned from heating-off samples.
        report=sample.power_reported_at if sample.power_reported_at is not None else sample.at
        if (sample.compressor is False and sample.auxiliary is False and sample.fresh_power and 5 <= sample.power_w <= 250
            and report>self.fan_reports.get(sample.fan_key,0)):
            old=self.fan_baselines.get(sample.fan_key,{"watts":sample.power_w,"count":0})
            count=min(old["count"]+1,1000)
            alpha=1/min(count,30)
            self.fan_baselines[sample.fan_key]={"watts":old["watts"]+alpha*(sample.power_w-old["watts"]),"count":count}
            self.fan_reports[sample.fan_key]=report
        self.samples += 1
        old_soc=(self.last_snapshot or {}).get('soc')
        self.last_snapshot = self.snapshot()
        if old_soc is not None:
            change=self.last_snapshot['soc']-old_soc
            self.stock_trend='falling' if change<0 else 'rising' if change>0 else 'steady'
            self.last_snapshot['stock_trend']=self.stock_trend
        return self.last_snapshot

    def snapshot(self):
        if not self.particles or self.last is None:return None
        values=sorted(soc(p.temperatures,self.last.top) for p in self.particles)
        value=median(values)
        p10,p90=values[int(.1*(COUNT-1))],values[int(.9*(COUNT-1))]
        stratified=self.last.top-self.last.bottom > 8
        # Deliberate uncertainty floor prevents particle collapse masquerading as precision.
        floor=25 if self.samples < 180 or self.phase in ("gap","mismatch") else 15
        if stratified:floor=max(floor,22)
        if self.last.power_quality=='held':floor=max(floor,25)
        if self.last.power_w is None or self.last.compressor is None or self.last.auxiliary is None:floor=max(floor,30)
        low,high=min(p10,value-floor),max(p90,value+floor)
        low,high=clamp(low,0,100),clamp(high,0,100)
        if self.last.top <= EMPTY_C:value=low=high=0
        layers=[round(median(p.temperatures[i] for p in self.particles),1) for i in range(LAYERS)]
        # Use one actual ensemble member for the representative physical profile.
        representative=min(self.particles,key=lambda p:abs(soc(p.temperatures,self.last.top)-value))
        mature=(self.samples >= 1440 and self.draw_events >= 5 and self.heating_minutes >= 180 and self.calm_minutes >= 120
                and self.learning['HOTWATER']['minutes']>=60 and self.learning['HOTWATER']['updates']>=3 and self.loss_updates>=1)
        return {
            "soc":round(value),"lower":int(low),"upper":ceil(high),
            "quality":"limited" if mature else "learning",
            "phase":self.phase,"layers_bottom_to_top":[round(t,1) for t in representative.temperatures],
            "layer_medians":layers,"last_sample":self.last.at,"samples":self.samples,
            "draw_events":self.draw_events,"draw_minutes":round(self.draw_minutes),
            "heating_minutes":round(self.heating_minutes),"calm_minutes":round(self.calm_minutes),
            "adaptations":self.adaptations,"gaps":self.gaps,"fit_error_c":round(self.residual_c,2),
            "effective_heat_gains":{mode:round(median(p.gains[i] for p in self.particles),2) if self.learning[mode]['updates'] else None for i,mode in enumerate(("HOTWATER","HEATING","COOLING"))},
            "gain_prior_medians":{mode:round(median(p.gains[i] for p in self.particles),2) for i,mode in enumerate(("HOTWATER","HEATING","COOLING"))},
            "loss_w_k":round(median(p.loss_w_k for p in self.particles),2),
            "fan_baselines":{key:dict(value) for key,value in self.fan_baselines.items()},
            "power_missing":self.last.power_w is None,
            "power_quality":self.last.power_quality if self.last.power_w is not None else 'assumed',
            "power_age_s":None if self.last.power_age_s is None else round(self.last.power_age_s),
            "power_w_used":self.last.power_w,"power_reason":self.last.power_reason,
            "draw_probability":round(self.draw_probability,2),"stock_trend":self.stock_trend,
            "heat_active":True if self.last.compressor is True or self.last.auxiliary is True else False if self.last.compressor is False and self.last.auxiliary is False else None,
            "learning_reason":self.learning_reason,
            "parameter_evidence":{mode:{'fresh_minutes':round(e['minutes'],1),'independent_reports':len(e['reports']),'updates':e['updates']} for mode,e in self.learning.items()},
            "loss_updates":self.loss_updates,"loss_learning_minutes":round(self.loss_learning_minutes),
            "parameter_families":len(set((p.mixing,p.inlet_c,p.coil_shape) for p in self.particles)),
            "legacy_adaptations":self.legacy_adaptations,"migrated_from":self.migrated_from,
            "signals_missing":self.last.compressor is None or self.last.auxiliary is None,
            "cold_water_range_c":[7,15],"empty_c":EMPTY_C,"full_c":FULL_C,"tank_l":TANK_L,
            "calibrated_by_measured_draw":False,"interval_is_calibrated_confidence":False,
            "sensor_positions_verified":False,
        }

    def dump(self):
        return {"version":VERSION,"particles":[asdict(p) for p in self.particles],
                "last":asdict(self.last) if self.last else None,
                **{key:getattr(self,key) for key in ("samples","heating_minutes","calm_minutes","draw_events","draw_minutes","last_draw_at","gaps","adaptations","residual_c")},
                "fan_baselines":{key:dict(value) for key,value in self.fan_baselines.items()},
                "v2":deepcopy({key:getattr(self,key) for key in ('learning','loss_window_minutes','loss_window_start','loss_learning_minutes','loss_updates','fan_reports','learning_reason','last_draw_evidence_at','draw_probability','stock_trend','active_draw','legacy_adaptations','migrated_from','recent')})}

    @classmethod
    def restore(cls, data):
        """Validate every persisted scalar; corrupt/old state is rejected atomically."""
        if not isinstance(data,dict) or data.get("version") not in (1,VERSION):raise ValueError("Unsupported model state")
        rows=data.get("particles")
        if not isinstance(rows,list) or len(rows)!=COUNT:raise ValueError("Invalid ensemble")
        result=cls()
        for row in rows:
            if not isinstance(row,dict):raise ValueError("Invalid particle")
            p=Particle(**row)
            if (len(p.temperatures)!=LAYERS or len(p.gains)!=3
                or not all(finite(t) and -5<=t<=105 for t in p.temperatures)
                or not all(finite(g) and .01<=g<=5 for g in p.gains)
                or not finite(p.loss_w_k) or not .1<=p.loss_w_k<=6
                or not finite(p.mixing) or not 0<=p.mixing<=.1
                or not finite(p.inlet_c) or not 0<=p.inlet_c<=30
                or not finite(p.coil_shape) or not 0<=p.coil_shape<=1
                or not finite(p.draw_l_min) or not 0<=p.draw_l_min<=20):raise ValueError("Invalid particle values")
            result.particles.append(p)
        result.last=Sample(**data["last"])
        if not result.last.valid():raise ValueError("Invalid persisted sample")
        for key in ("samples","heating_minutes","calm_minutes","draw_events","draw_minutes","gaps","adaptations","residual_c"):
            value=data.get(key)
            if not finite(value) or not 0<=value<=1e9:raise ValueError("Invalid model counters")
            if key in ("samples","draw_events","gaps","adaptations") and type(value) is not int:raise ValueError("Invalid integer counter")
            setattr(result,key,value)
        last_draw=data.get("last_draw_at")
        if last_draw is not None and (not finite(last_draw) or last_draw>result.last.at):raise ValueError("Invalid draw timestamp")
        result.last_draw_at=last_draw
        baselines=data.get("fan_baselines",{})
        if not isinstance(baselines,dict) or len(baselines)>100:raise ValueError("Invalid fan calibration")
        for key,value in baselines.items():
            if not isinstance(key,str) or len(key)>20 or not isinstance(value,dict) or not finite(value.get("watts")) or not 5<=value["watts"]<=250 or type(value.get("count")) is not int or not 0<=value["count"]<=1000:raise ValueError("Invalid fan calibration")
        result.fan_baselines=baselines
        if data['version']==1:
            # Keep all inferred water temperatures and history counters; the old
            # parameter marginals were selected without identifiable evidence.
            rng=random.Random(61018)
            for p in result.particles:
                p.gains=[rng.uniform(.8,3.8),rng.uniform(.05,1.8),rng.uniform(.3,3.5)]
                p.loss_w_k=rng.uniform(.6,3);p.mixing=rng.uniform(.001,.025)
                p.inlet_c=rng.uniform(7,15);p.coil_shape=rng.random()
            result.legacy_adaptations=result.adaptations;result.adaptations=0
            result.migrated_from=1
            result.learning_reason='Obnoveny teploty a historie; neověřené parametry verze 1 mají nové široké rozpětí'
            for key in baselines:result.fan_reports[key]=result.last.at
        else:
            extra=data.get('v2')
            if not isinstance(extra,dict):raise ValueError('Missing parameter evidence')
            for key in result.learning:
                row=extra.get('learning',{}).get(key)
                if not isinstance(row,dict) or set(row)!=set(result.learning[key]):raise ValueError('Invalid learning evidence')
                if (not all(finite(row[k]) and 0<=row[k]<=1e9 for k in ('minutes','signal_c','updates'))
                    or not finite(row['last_update_report']) or not 0<=row['last_update_report']<=result.last.at+5
                    or type(row['updates']) is not int or not isinstance(row['reports'],list) or len(row['reports'])>64
                    or any(not finite(t) or not 0<t<=result.last.at+5 for t in row['reports'])):raise ValueError('Invalid learning evidence')
            for key in ('loss_window_minutes','loss_learning_minutes','loss_updates','draw_probability','legacy_adaptations'):
                if not finite(extra.get(key)) or not 0<=extra[key]<=1e9:raise ValueError('Invalid learning counter')
            if not 0<=extra['draw_probability']<=1 or type(extra.get('active_draw')) is not bool:raise ValueError('Invalid draw state')
            if extra.get('stock_trend') not in ('steady','rising','falling'):raise ValueError('Invalid trend')
            if extra.get('migrated_from') not in (None,1):raise ValueError('Invalid migration marker')
            if not isinstance(extra.get('learning_reason'),str) or len(extra['learning_reason'])>300:raise ValueError('Invalid learning reason')
            for key in ('last_draw_evidence_at',):
                if extra.get(key) is not None and (not finite(extra[key]) or not 0<extra[key]<=result.last.at):raise ValueError('Invalid evidence timestamp')
            start=extra.get('loss_window_start')
            if start is not None and (not isinstance(start,list) or len(start)!=2 or any(not finite(v) or not 0<=v<=95 for v in start)):raise ValueError('Invalid loss window')
            if not isinstance(extra.get('fan_reports'),dict) or len(extra['fan_reports'])>100 or any(not isinstance(k,str) or len(k)>20 or not finite(v) or not 0<v<=result.last.at+5 for k,v in extra['fan_reports'].items()):raise ValueError('Invalid fan report times')
            recent=extra.get('recent')
            if not isinstance(recent,list) or len(recent)>12 or any(not isinstance(r,list) or len(r)!=3 or not finite(r[0]) or not 0<r[0]<=result.last.at or any(not finite(t) or not 0<=t<=95 for t in r[1:]) for r in recent):raise ValueError('Invalid temperature window')
            for key in ('learning','loss_window_minutes','loss_window_start','loss_learning_minutes','loss_updates','fan_reports','learning_reason','last_draw_evidence_at','draw_probability','stock_trend','active_draw','legacy_adaptations','migrated_from','recent'):setattr(result,key,extra[key])
        result.phase="restored"
        result.last_snapshot=result.snapshot()
        return result
