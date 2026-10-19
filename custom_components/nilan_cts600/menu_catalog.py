"""Documented CTS600 user menus; match visible labels rather than register guesses.

Source: Nilan VP18 CTS600 user guide, firmware 1.36, pp. 14-23.
Service DHW labels: CTS600 installation instructions, p. 12 (discovery only).
Unpublished ranges are learned from the controller, never invented.
"""
from dataclasses import dataclass
import math
import re
import unicodedata


def canonical(value):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9+-]", "", text)

def fold_display(value):
    return ''.join(ch for ch in unicodedata.normalize('NFKD',value) if not unicodedata.combining(ch))


@dataclass(frozen=True)
class MenuSetting:
    key: str
    title: str
    path: tuple[tuple[str, ...], ...]
    pattern: str
    kind: str = "select"
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[str, ...] | None = None
    aliases: tuple[tuple[str, str], ...] = ()
    protected: bool = False
    page: int = 0
    unit: str | None = None
    allow_off: bool = False

    def parse(self, display):
        clean = fold_display(display.replace("{", "").replace("}", "").strip())
        match = re.fullmatch(self.pattern, clean, re.IGNORECASE)
        if not match:
            return False, None
        raw = match.group("value").strip()
        normalized = canonical(raw)
        if normalized in ("OFF", "--", "VYP", "VYPNUTO"):
            if self.options is None or 'OFF' in self.options or self.key=='dhw_temperature':return True, "OFF"
            return False,None
        for token, target in self.aliases:
            if normalized == canonical(token):
                return True, target
        if self.kind == "number":
            try:
                value = int(raw.replace("°C", "").replace("°", "").strip())
            except ValueError:
                return False, None
            if self.minimum is not None and not self.minimum <= value <= self.maximum:
                return False, None
            return True, value
        if self.unit == "°C":
            raw = raw.replace("°C", "").strip()
        if self.options:
            for option in self.options:
                if canonical(option) == canonical(raw):
                    return True, option
            return False, None
        return True, raw

    def validate(self, value, discovered_options=()):
        if self.allow_off and value == 'OFF':return 'OFF'
        if self.kind == "number":
            if isinstance(value, bool):
                raise ValueError("Boolean is not a temperature or integer setting")
            try:
                number = float(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid {self.key}") from exc
            if not math.isfinite(number) or number != int(number) or not self.minimum <= number <= self.maximum:
                raise ValueError(f"{self.key} must be an integer {self.minimum}–{self.maximum}")
            return int(number)
        value = str(value)
        choices = self.options if self.options is not None else discovered_options
        if value not in choices:
            raise ValueError(f"Unsupported {self.key} option: {value}")
        return value


DHW = (("HOTWATER", "VARMVAND", "TEPVODA"),)
COOL = (("COOLING", "KOLING", "CHLAZENI"),)
AIR = (("AIREXCHANGE", "LUFTSKIFTE", "VZDUCHVYMENA"),)
FILTER = (("AIRFILTER", "LUFTFILTER", "VZDUCHFILTR", "FILTR"),)
TEMP = (("TEMPCONTROL", "TEMPKONTROL", "TEPLOTARIZENI", "TEPRIZENI"),)
USER = (("USERSELECT", "USERCHOICE", "BRUGERVALG", "OBSLUHAVYBER"),)
WEEK = (("WEEKPROGRAM", "WEEKPROG", "UGEPROGRAM", "TYDENPROGRAM"),)
SERVICE_DHW = (("SERVICE", "SERVIS"),) + DHW


SETTINGS = (
    MenuSetting("dhw_temperature", "TUV – požadovaná teplota T12", DHW,
                r"(?:COMP(?:RES(?:S|\.)?)?|KOMP(?:RES(?:S|\.)?)?)/T12\s*(?P<value>--|\d+)°C", "number", 5, 60, page=18, unit="°C"),
    MenuSetting("dhw_aux_temperature", "TUV – teplota elektrického dohřevu T11", DHW,
                r"(?:EL\.?\s*SUP|SUP\s*EL|EL\.?\s*OHREV)/(?:T11\s*)?(?P<value>--|\d+)\s*°C", "number", 5, 85, page=18, unit="°C", allow_off=True),
    MenuSetting("dhw_compressor_max", "TUV – maximum kompresoru", DHW,
                r"(?:COMP|KOMP)\.?\s*MAX/T12\s*(?P<value>\d+)°C", "number", 60, 80, page=18, unit="°C"),
    MenuSetting("dhw_priority", "TUV – přednost voda/vzduch", DHW,
                r"(?:PRIOR(?:ITY)?|PREDNOST)/(?P<value>.+)", options=("WATER", "AIR"),
                aliases=(("VAND","WATER"),("VODA","WATER"),("INLET","AIR"),("VZDUCH","AIR"),("LUFT","AIR")), page=18),
    MenuSetting("cooling_offset", "Chlazení – rozdíl proti pokojové teplotě", COOL,
                r"(?:TEMP\.?|TEPLOTA)/(?:SET|SAET|NAST|NASTAV|ZADEJ)\s*(?P<value>OFF|VYP|\+?\d+)(?:\s*°C)?",
                options=("OFF", "+1", "+2", "+3", "+4", "+5", "+7", "+10"),
                aliases=tuple((str(i),f"+{i}") for i in (1,2,3,4,5,7,10)), page=19),
    MenuSetting("cooling_fan", "Chlazení – zvýšený výkon ventilace", COOL,
                r"(?:VENT\.?|VENTILAT|VETRANI)/?(?:HIGH|HOJ|VYS|VYSO)\s+(?P<value>OFF|VYP|[234])", options=("OFF","2","3","4"), page=19),
    MenuSetting("air_mode", "Výměna vzduchu – režim", AIR,
                r"(?:AIR\s*EXCH|AIR\s*EX|LUFT\s*SK|VZDUCH|VETRANI)/(?P<value>.+)",
                options=("ENERGY", "COMFORT", "COMFORT W"),
                aliases=(("ENERGI","ENERGY"),("ENERGIE","ENERGY"),("KOMFORT","COMFORT"),("KOMFORT V","COMFORT W"),("KOMFORT W","COMFORT W"),("VIC VODY","COMFORT W")), page=20),
    MenuSetting("air_curve_min", "Výměna vzduchu – minimum teplotní křivky", AIR,
                r"(?:CURVE|KURVE|KRIVKA)/MIN\.?\s*(?P<value>-?\d+)°C", page=20, unit="°C"),
    MenuSetting("air_curve_max", "Výměna vzduchu – maximum teplotní křivky", AIR,
                r"(?:CURVE|KURVE|KRIVKA)/MAX\.?\s*(?P<value>-?\d+)°C", page=20, unit="°C"),
    MenuSetting("air_compressor_min", "Výměna vzduchu – spuštění kompresoru v zimě", AIR,
                r"(?:COMP|KOMP)\.?\s*MIN/(?P<value>OFF|VYP|[+-]?\d+)(?:\s*°C)?",
                options=("OFF",)+tuple(str(i) for i in range(-15,16)),
                aliases=tuple((f"+{i}",str(i)) for i in range(0,16)), page=20),
    MenuSetting("air_winter_low", "Výměna vzduchu – nízký zimní výkon", AIR,
                r"(?:WINTER|VINTER|ZIMA)/(?:LOW|LAV|NIZ|NIZK)\.?\s*(?P<value>OFF|VYP|[123])", options=("OFF","1","2","3"), page=20),
    # CTS600/602 quick guide p. 3 and CTS602 guide section 11.3; range is not published.
    MenuSetting("winter_outdoor_threshold", "Zimní útlum – mez venkovní teploty", AIR,
                r"(?:WINTER|VINTER|ZIMA)/<\s*(?P<value>[+-]?\d+)\s*°C", page=17, unit="°C"),
    MenuSetting("filter_interval", "Filtr – interval upozornění", FILTER,
                r"ALARM/(?P<value>OFF|\d+)(?:\s*(?:DAYS|DAGE|DNI))?", options=("OFF","30","90","180","360"), page=21),
    MenuSetting("summer_inlet_min", "Přívodní vzduch – minimum v létě", TEMP,
                r"(?:SUMMER|SOMMER|LETO)/MIN\.?\s*(?P<value>-?\d+)°C", page=22, unit="°C"),
    MenuSetting("winter_inlet_min", "Přívodní vzduch – minimum v zimě", TEMP,
                r"(?:WINTER|VINTER|ZIMA)/MIN\.?\s*(?P<value>-?\d+)°C", page=22, unit="°C"),
    MenuSetting("summer_outdoor_min", "Venkovní teplota – mez letního režimu", TEMP,
                r"(?:SUMMER|SOMMER|LETO)/>\s*(?P<value>-?\d+)°C", page=22, unit="°C"),
    MenuSetting("user_function", "Uživatelská volba – funkce externího kontaktu", USER,
                r"(?:SELECT|VALG|VOLBA|VYBER)/(?P<value>.+)", options=("OFF","EXTEND","INLET","EXHAUST"),
                aliases=(("FORLAENG","EXTEND"),("VICE","EXTEND"),("INDBLAES","INLET"),("PRIVOD","INLET"),("UDSUG","EXHAUST"),("ODTAH","EXHAUST")), page=14),
    MenuSetting("user_duration", "Uživatelská volba – trvání", USER,
                r"(?:TIME|TID|CAS)/(?P<value>\d{2}:\d{2})", page=14),
    MenuSetting("user_fan", "Uživatelská volba – výkon ventilace", USER,
                r"(?:SPEED|FLOW|HAST|VYKON|RYCHLOST)/>?(?P<value>[1-4])<?", options=("1","2","3","4"), page=14),
    MenuSetting("user_temperature", "Uživatelská volba – pokojová teplota", USER,
                r"(?:TEMP\.?|TEPLOTA)/(?P<value>\d+)(?:°C)?", "number", 5, 30, page=14, unit="°C"),
    MenuSetting("weekly_program", "Týdenní program – volba", WEEK,
                r"(?:SELECT|VALG|VOLBA|VYBER)/(?P<value>OFF|VYP|PROG\s*[123])", options=("OFF","PROG 1","PROG 2","PROG 3"), page=16),
    MenuSetting("dhw_aux_enabled", "TUV – povolit elektrický dohřev", SERVICE_DHW,
                r"(?:EL\.?\s*SUP|SUP\s*EL|EL\.?\s*OHREV)/(?P<value>ON|OFF)", "switch", options=("OFF","ON"), protected=True, page=12),
    MenuSetting("legionella_day", "TUV – den hygienického ohřevu", SERVICE_DHW,
                r"LEGIONEL(?:A)?/(?P<value>OFF|MON|TUE|WED|THU|FRI|SAT|SUN)",
                options=("OFF","MON","TUE","WED","THU","FRI","SAT","SUN"), protected=True, page=12),
)
BY_KEY = {item.key:item for item in SETTINGS}
USER_PATHS = tuple(dict.fromkeys(item.path for item in SETTINGS if not item.protected or len(item.path)==1))

# Clock fields are accessed by an explicit service, using the controller's local time.
CLOCK = (("@CLOCK",),)
CLOCK_FIELDS = tuple(MenuSetting(f"clock_{key}", f"Hodiny – {key}", CLOCK,
    rf"(?:{labels})/(?P<value>\d+)", "number", lo, hi, page=15)
    for key,labels,lo,hi in (("year","YEAR|AAR|ROK",0,99),("month","MONTH|MAANED|MESIC",1,12),
                            ("day","DAY|DAG|DEN",1,31),("hour","HOUR|TIME|HODINA",0,23),
                            ("minute","MINUTE|MINUT|MINUTA",0,59)))
BY_KEY.update({item.key:item for item in CLOCK_FIELDS})
