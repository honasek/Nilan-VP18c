"""Bounded CTS600 menu navigation and writes confirmed by a fresh readback.

Menu absence is a capability result. A failed/uncertain write is never replayed.
No arbitrary registers, menu offsets, or assumed enum ordering are used.
"""
from datetime import datetime
import re
from .menu_catalog import BY_KEY, SETTINGS, CLOCK_FIELDS, MenuSetting, canonical, fold_display
from .nilan_cts600 import Key, NilanCTS600Exception


class MenuUnavailable(NilanCTS600Exception):
    """This firmware did not expose the requested menu/value."""


class MenuWriteError(NilanCTS600Exception):
    """The operation cannot be confirmed; some fields may already be saved."""


class MenuControls:
    def __init__(self, panel):
        self.panel = panel
        self.values = {}
        self.choices = {}
        self.raw_menus = {}
        self.weekly_slots = {}
        self.root_menus = None
        self.clock = None
        self.alarm_history = None

    def read_profile(self):
        """Inspect top menus and controller clock without entering edit mode."""
        self.panel.resetMenu()
        roots=[]
        clock=None
        for direction in (Key.DOWN,Key.UP):
            seen=set()
            for _ in range(64):
                text=self.panel.display()
                if text in seen:break
                seen.add(text)
                if text not in roots:roots.append(text)
                if self._path_match(text,('@CLOCK',)):
                    match=re.fullmatch(r'(\d{2})-(\d{2})-(\d{2})/[A-Z]{2,3}\.?\s*(\d{2})[.:](\d{2})',fold_display(text))
                    if match:
                        try:
                            parts=[int(value) for value in match.groups()]
                            clock=datetime(2000+parts[0],*parts[1:]).strftime('%Y-%m-%d %H:%M')
                        except ValueError:pass
                language=re.fullmatch(r'(?:LANGUAGE|SPROG|JAZYK|SPRACHE|LANGUE|SPRAK|KIELI)/(.+)',text)
                if language:self.values['panel_language']=language[1].strip()
                if self.panel.key(direction)==text:break
            else:
                self.panel.resetMenu()
                raise MenuUnavailable('Přehled hlavních menu překročil omezený počet kroků')
        self.panel.resetMenu()
        self.root_menus=roots
        self.clock=clock
        return roots

    def read_alarm_history(self):
        """Read the log branch only; never press ENTER on an active alarm."""
        self.enter_path((("SHOWALARMS","VISALARMER","PREHLEDVYSTRAH"),))
        self._find(lambda text:canonical(text.split('/')[0]) in ('ALARMLOG','EVENTS','UDALOSTI'))
        self.panel.key(Key.ENTER)
        raw=[]
        for direction in (Key.DOWN,Key.UP):
            seen=set()
            for _ in range(64):
                text=self.panel.display()
                if text in seen:break
                seen.add(text)
                if text not in raw:raw.append(text)
                if self.panel.key(direction)==text:break
            else:
                self.panel.resetMenu()
                raise MenuUnavailable('Historie výstrah překročila omezený počet kroků')
        self.panel.resetMenu()
        # Raw records preserve firmware wording; an unrecognised log is not zero alarms.
        if not raw:raise MenuUnavailable('Historie výstrah nevrátila data')
        self.alarm_history=raw
        return raw

    def _path_match(self, display, aliases):
        clean = display.replace("{", "").replace("}", "")
        if "@CLOCK" in aliases:
            return bool(re.fullmatch(r"\d{2}-\d{2}-\d{2}/[A-Z]{2,3}\.?\s*\d{2}[.:]\d{2}", fold_display(clean)))
        return canonical(clean) in {canonical(alias) for alias in aliases}

    def _find(self, predicate, limit=64):
        display = self.panel.display()
        if predicate(display):
            return display
        for direction in (Key.DOWN, Key.UP):
            seen = {display}
            for _ in range(limit):
                display = self.panel.key(direction)
                if predicate(display):
                    return display
                if display in seen:
                    break
                seen.add(display)
        raise MenuUnavailable("Požadovaná položka není v tomto menu dostupná")

    def enter_path(self, path):
        self.panel.resetMenu()
        for aliases in path:
            self._find(lambda text:self._path_match(text, aliases))
            self.panel.key(Key.ENTER)

    def locate(self, spec):
        self.enter_path(spec.path)
        self._find(lambda text:spec.parse(text)[0])
        return spec.parse(self.panel.display())[1]

    def read(self, key):
        spec = BY_KEY[key]
        value = self.locate(spec)
        self.values[key] = value
        self.panel.resetMenu()
        return value

    def read_group(self, path):
        candidates = [spec for spec in SETTINGS if spec.path == path]
        self.enter_path(path)
        result = {}
        raw = []
        for direction in (Key.DOWN, Key.UP):
            seen = set()
            for _ in range(64):
                display = self.panel.display()
                if display in seen:
                    break
                seen.add(display)
                if display not in raw:
                    raw.append(display)
                for spec in candidates:
                    matched, value = spec.parse(display)
                    if matched:
                        if spec.key in result and result[spec.key] != value:
                            raise MenuUnavailable(f"Nejednoznačná položka {spec.key}")
                        result[spec.key] = value
                next_display = self.panel.key(direction)
                if next_display == display:
                    break
            else:
                raise MenuUnavailable("Menu překročilo omezený počet kroků")
        # Replace this group's capability snapshot; never leave vanished controls available.
        values = {key:value for key,value in self.values.items() if key not in {spec.key for spec in candidates}}
        values.update(result)
        self.values = values
        self.raw_menus["/".join(group[0] for group in path)] = raw
        self.panel.resetMenu()
        return result

    def _editing(self, spec):
        display = self.panel.display()
        matched, value = spec.parse(display)
        blink = re.search(r"\{([^{}]+)\}", display)
        if not matched or blink is None:
            raise MenuWriteError("Nilan nepotvrdil editační režim očekávané položky")
        clean = fold_display(display.replace("{", "").replace("}", "").strip())
        expected = re.fullmatch(spec.pattern, clean, re.IGNORECASE)
        start = len(display[:blink.start()].replace("{", "").replace("}", ""))
        # CTS600 pads a blinking single digit, e.g. MIN { 9}°C. Compare the
        # first actual character, not a padding space consumed by the regex.
        start += len(blink[1])-len(blink[1].lstrip())
        if not expected.start("value") <= start < expected.end("value"):
            raise MenuWriteError("Bliká jiná položka než požadovaná hodnota")
        return value

    def _cancel(self):
        self.panel.key(Key.ESC)
        self.panel.resetMenu()

    def _begin_edit(self, spec, original):
        self.panel.key(Key.ENTER)
        display = self.panel.display()
        # Some documented menus need two ENTER presses. A second press is allowed
        # only on the same unchanged value with no blinking field anywhere.
        if '{' not in display and '}' not in display and spec.parse(display) == (True, original):
            self.panel.key(Key.ENTER)
        try:
            self._editing(spec)
        except MenuWriteError:
            self._cancel()
            raise

    def _move_to(self, spec, target):
        current = self._editing(spec)
        if current == target:
            return
        # Numeric direction is observed from values. Enums are searched in both directions.
        if spec.kind == "number" and current != "OFF" and target != "OFF":
            directions = (Key.UP,) if target > current else (Key.DOWN,)
        else:
            directions = (Key.UP, Key.DOWN)
        limit = (spec.maximum-spec.minimum+2) if spec.kind == "number" else 512
        for direction in directions:
            seen = {current}
            for _ in range(limit):
                self.panel.key(direction)
                current = self._editing(spec)
                if current == target:
                    return
                if current in seen:
                    break
                seen.add(current)
        self._cancel()
        raise MenuWriteError("Požadovaná hodnota nebyla dosažitelná; změna nebyla potvrzena")

    def set(self, key, value):
        spec = BY_KEY[key]
        target = spec.validate(value, self.choices.get(key, ()))
        return self._apply(spec,target)

    def _apply(self,spec,target):
        key=spec.key
        previous = self.locate(spec)
        if previous == target:
            self.values[key] = previous
            self.panel.resetMenu()
            return previous
        self._begin_edit(spec, previous)
        self._move_to(spec, target)
        # Set no optimistic value before commit AND a separate navigation/readback.
        self.panel.key(Key.ENTER)
        confirmed = self.locate(spec)
        if confirmed != target:
            self.panel.resetMenu()
            raise MenuWriteError(f"Zápis {key} nebyl potvrzen čtením: {confirmed!r}")
        self.values[key] = confirmed
        if key=='weekly_program':self.weekly_slots={}
        self.panel.resetMenu()
        return confirmed

    def set_language(self,language):
        spec=MenuSetting('panel_language','Panel language',(),
            r'(?:LANGUAGE|SPROG|JAZYK|SPRACHE|LANGUE|SPRAK|KIELI)/(?P<value>.+)',
            options=('ENGLISH','DANISH','CZECH','GERMAN','FRENCH','SWEDISH','NORWEG.','SUOMI'))
        return self._apply(spec,spec.validate(language))==language

    def discover_choices(self, key):
        """Read edit choices, cancel, then prove the saved setting is unchanged.

        Explicit commissioning action only. Never performed during ordinary polling.
        """
        spec = BY_KEY[key]
        if spec.options is not None or spec.kind == "number":
            return spec.options
        original = self.locate(spec)
        self._begin_edit(spec, original)
        options = []
        for direction in (Key.UP, Key.DOWN):
            seen = set()
            for _ in range(512):
                value = self._editing(spec)
                if value in seen:
                    break
                seen.add(value)
                if str(value) not in options:
                    options.append(str(value))
                self.panel.key(direction)
            else:
                self._cancel()
                raise MenuUnavailable("Výčet voleb překročil 512 kroků")
        self._cancel()
        if self.locate(spec) != original:
            self.panel.resetMenu()
            raise MenuWriteError("Řídicí jednotka při čtení voleb změnila uloženou hodnotu")
        self.panel.resetMenu()
        self.choices[key] = tuple(options)
        self.values[key] = original
        return tuple(options)

    def set_air_mode_preserving_dhw(self, value):
        # Nilan's VP18 guide warns that changing air mode can reset the T12 target to '--'.
        previous_dhw = self.read("dhw_temperature")
        if previous_dhw == 'OFF':
            raise MenuUnavailable('Před změnou větrání nastavte a ověřte teplotu TUV; původní T12 je vypnutá')
        result = self.set("air_mode", value)
        new_dhw = self.read("dhw_temperature")
        if previous_dhw != "OFF" and new_dhw != previous_dhw:
            self.set("dhw_temperature", previous_dhw)
        return result

    def set_clock(self, when:datetime):
        if when.tzinfo is None or not 2000 <= when.year <= 2099:
            raise ValueError("Použijte čas s časovou zónou v letech 2000–2099")
        # Avoid impossible intermediate dates such as 31 February.
        self.set("clock_day", 1)
        for key,value in (("year",when.year % 100),("month",when.month),("day",when.day),
                          ("hour",when.hour),("minute",when.minute)):
            self.set("clock_"+key, value)
        return {spec.key:self.read(spec.key) for spec in CLOCK_FIELDS}

    def read_alarms(self):
        self.enter_path((("SHOWALARMS","VISALARMER","PREHLEDVYSTRAH"),))
        records = {}
        raw = []
        for direction in (Key.DOWN,Key.UP):
            seen=set()
            for _ in range(64):
                text=self.panel.display()
                if text in seen:break
                seen.add(text)
                if text not in raw:raw.append(text)
                match=re.fullmatch(r"(?:ALARM|VYSTRA)\s*(\d{1,2})/(.+)",text)
                if match:records[int(match[1])]=match[2].strip()
                # Do not enter ALARMLOG automatically or perform an acknowledgement.
                new=self.panel.key(direction)
                if new==text:break
        self.panel.resetMenu()
        self.raw_menus['ALARMS']=raw
        if not records:raise MenuUnavailable('Formát alarmů nebyl rozpoznán; nelze tvrdit, že nejsou žádné alarmy')
        return records

    def read_show_data(self):
        self.enter_path((("SHOWDATA","VISDATA","PREHLEDDAT"),))
        result={}
        raw=[]
        for direction in (Key.DOWN,Key.UP):
            seen=set()
            for _ in range(64):
                text=self.panel.display()
                if text in seen:break
                seen.add(text)
                if text not in raw:raw.append(text)
                temp=re.fullmatch(r'(.*)/(T\d+)\s*(-?\d+)°C',text)
                if temp:result[temp[2]]=int(temp[3])
                status=re.fullmatch(r'(?:STATUS|STAV)/(.+)',text)
                if status:result['status']=status[1]
                flow=re.fullmatch(r'(.+)/(?:FLOW|SPEED|HAST|RYCHLOST)\s*([0-4])',text)
                if flow:
                    label=canonical(flow[1])
                    if label in ('INLET','INDBLAES','PRIVOD'):result['inletFlow']=int(flow[2])
                    elif label in ('EXHAUST','UDSUG','ODTAH'):result['exhaustFlow']=int(flow[2])
                version=re.fullmatch(r'SOFTWARE/([123])\s+([^/{}]{1,8})',text)
                if version is None:version=re.fullmatch(r'SOFTWARE\s*([123])/([^/{}]{1,8})',text)
                if version:result[f'software_{version[1]}']=version[2].strip()
                unit=re.fullmatch(r'(?:TYPE|TYP)/(.+)',text)
                if unit:result['unit_type']=unit[1]
                humidity=re.fullmatch(r'(?:HUMIDITY|FUGT|VLHKOST)/\s*(\d+)\s*%',text)
                if humidity and 0<=int(humidity[1])<=100:result['humidity']=int(humidity[1])
                co2=re.fullmatch(r'CO2/\s*(\d+)\s*(?:PPM|ppm)',text)
                if co2:result['co2']=int(co2[1])
                bypass=re.fullmatch(r'(?:BY-?PASS|BYPASS)/(OPEN|CLOSED|ON|OFF|OTEVRENO|ZAVRENO)',fold_display(text))
                if bypass:result['bypass']=bypass[1]
                if self.panel.key(direction)==text:break
        self.panel.resetMenu()
        self.raw_menus['SHOWDATA']=raw
        return result

    def acknowledge_alarm(self, code):
        if isinstance(code,bool) or not isinstance(code,int) or not 1<=code<=99:
            raise ValueError("Požadováno konkrétní číslo alarmu 1–99")
        alarms=self.read_alarms()
        if code not in alarms: return alarms
        self.enter_path((("SHOWALARMS","VISALARMER","PREHLEDVYSTRAH"),))
        self._find(lambda text:bool(re.fullmatch(rf"(?:ALARM|VYSTRA)\s*{code}/.+",text)))
        self.panel.key(Key.ENTER)
        self.panel.key(Key.ENTER)
        remaining=self.read_alarms()
        if code in remaining:
            raise MenuWriteError("Alarm zůstal aktivní; řídicí jednotka jej nepotvrdila")
        return remaining
