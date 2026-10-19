"""CTS600's 7 x 6 program rows; identify editable fields from actual blinking."""
import re
from .menu_catalog import WEEK,BY_KEY
from .menu_controls import MenuUnavailable, MenuWriteError
from .nilan_cts600 import Key

DAYS = {"MON":("MO","MON","MA"),"TUE":("TU","TUE","TI"),"WED":("WE","WED","ON"),
        "THU":("TH","THU","TO"),"FRI":("FR","FRI"),"SAT":("SA","SAT","LO"),"SUN":("SU","SUN","SO")}
ROW = re.compile(r"(?P<day>[A-Z]{2,3})\s*(?P<slot>[1-6])\s*(?P<time>OFF|\d{2}[.:]\d{2})/>(?P<fan>OFF|VYP|[1-4])<\s*(?P<temperature>\d+)°C")

def parse_row(display):
    clean=display.replace("{","").replace("}","")
    match=ROW.fullmatch(clean)
    if not match:return None
    day=next((key for key,aliases in DAYS.items() if match['day'] in aliases),None)
    if not day:return None
    clock=match['time'].replace('.',':')
    if clock!='OFF':
        h,m=map(int,clock.split(':'))
        if h>23 or m>59:return None
    temperature=int(match['temperature'])
    if not 5<=temperature<=30:return None
    return {"day":day,"slot":int(match['slot']),"time":clock,"fan":0 if match['fan'] in ('OFF','VYP') else int(match['fan']),"temperature":temperature}

def read_slots(menu):
    menu.enter_path(WEEK)
    slots={}
    raw=[]
    for direction in (Key.DOWN,Key.UP):
        seen=set()
        for _ in range(64):
            text=menu.panel.display()
            if text in seen:break
            seen.add(text)
            if text not in raw:raw.append(text)
            matched,program=BY_KEY['weekly_program'].parse(text)
            if matched:menu.values['weekly_program']=program
            row=parse_row(text)
            if row:slots[f"{row['day']}_{row['slot']}"]=row
            if menu.panel.key(direction)==text:break
        else:raise MenuUnavailable("Týdenní program překročil očekávaný počet řádků")
    menu.panel.resetMenu()
    menu.raw_menus['WEEKPROGRAM']=raw
    if not slots:
        if menu.values.get('weekly_program')=='OFF':raise MenuUnavailable('Týdenní program je vypnutý; tato deska nyní nabízí pouze jeho volbu, nikoli časové kroky')
        raise MenuUnavailable('Řádky týdenního programu nebyly rozpoznány')
    menu.weekly_slots=slots
    return slots

def validate_slot(day,slot,clock,fan,temperature):
    if day not in DAYS or isinstance(slot,bool) or not isinstance(slot,int) or slot not in range(1,7):raise ValueError('Neplatný den nebo krok 1–6')
    if isinstance(fan,bool) or not isinstance(fan,int) or fan not in range(0,5):raise ValueError('Ventilace musí být 0 (Vypnuto) nebo 1–4')
    if isinstance(temperature,bool) or not isinstance(temperature,int) or not 5<=temperature<=30:raise ValueError('Teplota musí být celé číslo 5–30 °C')
    if clock!='OFF':
        if not isinstance(clock,str) or not re.fullmatch(r'\d{2}:\d{2}',clock):raise ValueError('Použijte HH:MM nebo OFF')
        h,m=map(int,clock.split(':'))
        if h>23 or m>59:raise ValueError('Neplatný čas')
    return {"day":day,"slot":slot,"time":clock,"fan":fan,"temperature":temperature}

def _segment(text):
    row=parse_row(text)
    blink=re.search(r'\{([^{}]+)\}',text)
    if not row or not blink:raise MenuWriteError('Neznámá editace týdenního programu')
    clean=text.replace('{','').replace('}','')
    match=ROW.fullmatch(clean)
    start=len(text[:blink.start()].replace('{','').replace('}',''))
    for field in ('fan','temperature','time'):
        lo,hi=match.span(field)
        if lo<=start<hi:
            if field!='time':return field,row[field],row
            token=blink[1]
            if token=='OFF' or re.fullmatch(r'\d{2}[.:]\d{2}',token):
                value='OFF' if row['time']=='OFF' else int(row['time'][:2])*60+int(row['time'][3:])
                return 'time',value,row
            if re.fullmatch(r'\d{2}',token) and row['time']!='OFF':
                return ('hour',int(row['time'][:2]),row) if start==lo else ('minute',int(row['time'][3:]),row)
    raise MenuWriteError('Bliká den nebo jiná neočekávaná položka; nic nepotvrzuji')

def set_slot(menu,day,slot,clock,fan,temperature):
    target=validate_slot(day,slot,clock,fan,temperature)
    def is_row(text):
        row=parse_row(text)
        return row is not None and row['day']==day and row['slot']==slot
    menu.enter_path(WEEK)
    menu._find(is_row)
    if parse_row(menu.panel.display())==target:
        menu.panel.resetMenu()
        return target
    menu.panel.key(Key.ENTER)
    seen_phases=set()
    for _ in range(12):
        field,current,row=_segment(menu.panel.display())
        if (field,current) in seen_phases:
            raise MenuWriteError('Řídicí jednotka neumožnila přejít na další pole programu; část mohla být uložena')
        seen_phases.add((field,current))
        if field in ('fan','temperature'):wanted=target[field]
        elif field=='time':wanted='OFF' if clock=='OFF' else int(clock[:2])*60+int(clock[3:])
        elif clock=='OFF':raise MenuWriteError('Tato editace nenabízí vypnutí času bez dalšího ověření firmware')
        elif field=='hour':wanted=int(clock[:2])
        else:wanted=int(clock[3:])
        for _ in range(1450 if field=='time' else 100):
            if current==wanted:break
            direction=Key.UP if current=='OFF' or wanted!='OFF' and wanted>current else Key.DOWN
            menu.panel.key(direction)
            new_field,new_value,new_row=_segment(menu.panel.display())
            if new_field!=field or new_row['day']!=day or new_row['slot']!=slot or new_value==current:
                menu._cancel()
                raise MenuWriteError('Požadované pole programu není dosažitelné')
            if isinstance(current,int) and isinstance(wanted,int) and isinstance(new_value,int) and (current<wanted<new_value or new_value<wanted<current):
                menu._cancel()
                raise MenuWriteError('Požadovanou hodnotu nelze nastavit krokem tohoto firmware')
            current=new_value
        else:
            menu._cancel()
            raise MenuWriteError('Požadovaný čas programu nebyl dosažitelný')
        menu.panel.key(Key.ENTER)
        if parse_row(menu.panel.display())==target and '{' not in menu.panel.display():break
        if '{' not in menu.panel.display():menu.panel.key(Key.ENTER)
    else:raise MenuWriteError('Nastavení programu nebylo potvrzeno')
    menu.enter_path(WEEK)
    menu._find(is_row)
    confirmed=parse_row(menu.panel.display())
    menu.panel.resetMenu()
    if confirmed!=target:raise MenuWriteError('Týdenní program neodpovídá požadavku při novém čtení')
    menu.weekly_slots[f'{day}_{slot}']=confirmed
    return confirmed
