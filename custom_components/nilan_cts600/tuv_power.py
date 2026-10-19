"""Bounded use of push-reported electrical power; no device commands."""
from dataclasses import dataclass, asdict
from .tuv_model import finite

FRESH_SECONDS = 600
HOLD_SECONDS = 3900


@dataclass(frozen=True)
class PowerInput:
    watts: float | None
    quality: str
    age_s: float | None
    reported_at: float | None
    reason: str | None


class PowerTracker:
    def __init__(self):
        self.context = None
        self.changed_at = None

    def observe(self, context, at, *, gap=False):
        context = list(context)
        if gap or context != self.context:
            self.context, self.changed_at = context, at

    def choose(self, value, unit, reported_at, now, *, restored=False):
        def assumed(reason, age=None):
            return PowerInput(None, 'assumed', age, reported_at if finite(reported_at) and 0<reported_at<=now+5 else None, reason)
        if restored:return assumed('Obnovená hodnota příkonu není nové hlášení')
        factor={'W':1,'kW':1000}.get(unit)
        try:watts=float(value)*factor
        except (TypeError,ValueError):return assumed('Příkon není dostupný nebo má neplatnou jednotku')
        if not finite(watts) or not 0<=watts<=10000:return assumed('Příkon mimo platný rozsah')
        if not finite(reported_at) or reported_at>now+5:return assumed('Neplatný čas hlášení příkonu')
        age=max(0,now-reported_at)
        if age>HOLD_SECONDS:return assumed('Hlášení příkonu je starší než 65 minut',age)
        if self.context is None or self.changed_at is None:return assumed('Čekám na známý provozní stav',age)
        compressor,auxiliary,mode,fan=self.context
        if type(compressor) is not bool or type(auxiliary) is not bool:return assumed('Chybí potvrzený stav zdrojů tepla',age)
        if reported_at<self.changed_at-5:return assumed('Příkon pochází z doby před změnou provozu',age)
        coherent = watts>=1000 if auxiliary else 120<=watts<=2000 if compressor else watts<=300
        if not coherent:return assumed('Příkon neodpovídá potvrzenému stavu zdrojů',age)
        return PowerInput(watts,'fresh' if age<=FRESH_SECONDS else 'held',age,reported_at,None)

    def dump(self):
        return {'context':self.context,'changed_at':self.changed_at}

    @classmethod
    def restore(cls,data):
        result=cls()
        if not isinstance(data,dict):raise ValueError('Invalid power context')
        context=data.get('context');at=data.get('changed_at')
        if (not isinstance(context,list) or len(context)!=4
            or any(type(v) not in (bool,type(None)) for v in context[:2])
            or any(not isinstance(v,str) or len(v)>32 for v in context[2:])
            or not finite(at) or at<=0):raise ValueError('Invalid power context')
        result.context=context;result.changed_at=at
        return result
