"""Air-side activity, kept separate from compressor activity heating the tank."""
from .menu_catalog import canonical
from .panel_indicators import indicators

PRESETS={'Úsporný':'ENERGY','Komfortní':'COMFORT','Více teplé vody':'COMFORT W'}

def air_action(data):
    if data.get('mode')=='OFF':return 'off'
    status=canonical(data.get('status',''))
    running=indicators(data)['compressor_running']
    if status in ('DEFROST','DEFROSTING','ODMRAZOVANI'):return 'defrosting'
    if running is True:
        if status in ('HEATING','HEAT','VYTAPENI','TOPENI'):return 'heating'
        if status in ('COOLING','COOL','CHLAZENI'):return 'cooling'
        if status not in ('HOTWATER','DHW','WATERHEAT','VARMVAND','OHREVVODY'):return None
    elif running is None:return None
    inlet=data.get('inletFlow')
    if type(inlet) is int and 0<=inlet<=4:return 'fan' if inlet>0 else 'idle'
    return None
