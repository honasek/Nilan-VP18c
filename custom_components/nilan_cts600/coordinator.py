"""One serialized executor operation at a time; no blind replay of writes."""
from __future__ import annotations
import asyncio
from datetime import timedelta, datetime
import logging
import time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .const import DOMAIN, DEFAULT_T15, DEFAULT_T15_BACKUP, DEFAULT_GATE, DEFAULT_MAX_AGE
from .nilan_cts600 import CTS600, Key, nilanString
from .t15_guard import configured_t15_sources, validate_t15, select_t15
from .transport import TransparentTCP
from .menu_catalog import BY_KEY, USER_PATHS, SERVICE_DHW
from .menu_controls import MenuControls, MenuUnavailable
from .weekly_program import read_slots, set_slot, validate_slot

_LOGGER = logging.getLogger(__name__)


class CTS600Coordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=entry.data.get("name", "Nilan VP18 K"),
                         config_entry=entry, update_interval=timedelta(seconds=5))
        self.entry = entry
        self.config = entry.data
        self.sensor_entity_id, self.backup_sensor_entity_id = configured_t15_sources(
            self.config, entry.options, DEFAULT_T15, DEFAULT_T15_BACKUP)
        self.selected_sensor_entity_id = None
        self.gate_entity = self.config.get("commands_enabled_entity", DEFAULT_GATE)
        self.max_age = self.config.get("t15_max_age_seconds", DEFAULT_MAX_AGE)
        self.transport = TransparentTCP(self.config["host"], self.config["tcp_port"])
        self.cts600 = CTS600(port=f"socket://{self.config['host']}:{self.config['tcp_port']}",
                             client=self.transport, logger=_LOGGER.debug)
        self.menu = MenuControls(self.cts600)
        self.menu_errors = {}
        self.alarms = None
        self._menu_cursor = 0
        self._next_menu_scan = 0
        self._next_alarm_scan = 0
        self._lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._manual_until = 0
        self._next_scan = 0
        self._transient_failures = 0
        self.last_success = None
        self.last_show_data_success = None
        self._last_profile_poll = None
        self.last_error = None
        self.last_command = None
        self.device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                      manufacturer="Nilan", model="CTS600 / VP18 K (ověření čeká)",
                                      name=self.name)

    def t15_reading(self):
        threshold = self.hass.states.get("input_number.nilan_t15_max_age_minutes")
        max_age = self.max_age
        if threshold is not None:
            try:
                minutes = float(threshold.state)
                if not 1 <= minutes <= 60:
                    raise ValueError("Invalid T15 age threshold")
                max_age = minutes * 60
            except (TypeError, ValueError):
                self.selected_sensor_entity_id = None
                return validate_t15(None, dt_util.utcnow(), self.max_age)
        self.selected_sensor_entity_id, reading = select_t15(
            self.hass.states.get, self.sensor_entity_id, self.backup_sensor_entity_id,
            dt_util.utcnow(), max_age)
        return reading

    def _check_guard(self, require_commands=False):
        reading = self.t15_reading()
        if not reading.valid:
            raise HomeAssistantError(f"T15 není platná: {reading.reason}")
        if require_commands and not self.hass.states.is_state(self.gate_entity, "on"):
            raise HomeAssistantError("Ovládání Nilan je uzamčeno; nejprve dokončete zprovoznění")
        return reading.value

    async def async_restore_options(self):
        """Restore observed choices only for the same controller type/firmware."""
        from homeassistant.helpers.storage import Store
        self._options_store=Store(self.hass,1,f'{DOMAIN}.{self.entry.entry_id}.menu_options')
        try:stored=await self._options_store.async_load()
        except Exception:
            _LOGGER.warning('Uložené volby menu nejsou dostupné; načtěte je znovu z jednotky',exc_info=True)
            return
        identity={key:self.data.get(key) for key in ('unit_type','software_1')}
        if not all(identity.values()) or not isinstance(stored,dict) or stored.get('identity')!=identity:return
        choices=stored.get('choices',{})
        if not isinstance(choices,dict):return
        for key,values in choices.items():
            spec=BY_KEY.get(key)
            if spec is not None and spec.options is None and spec.kind=='select' and isinstance(values,list) and 0<len(values)<=512 and all(isinstance(v,str) and len(v)<=32 for v in values):
                self.menu.choices[key]=tuple(values)

    async def async_save_options(self):
        store=getattr(self,'_options_store',None)
        if store is not None:
            await store.async_save({'identity':{key:self.data.get(key) for key in ('unit_type','software_1')},'choices':self.menu.choices})

    def _initialize_sync(self, value):
        self.transport.connect()
        self.cts600.initialize()
        identity = self.cts600.slaveID()
        # The first setup requires explicit commissioning acknowledgement in the flow.
        if not self.menu.set_language("ENGLISH"):
            raise ValueError("CTS600 language/menu could not be confirmed")
        self.cts600.setT15(value)
        self._initialized = True
        return identity

    def _poll_sync(self, value, scan):
        with self.transport.operation():
            if not self._initialized:
                self._initialize_sync(value)
            self.cts600.setT15(value)
            if time.monotonic() < self._manual_until:
                self.cts600.key(Key.NONE)
                # Preserve the manually selected menu; do not parse it as the top display.
                self.cts600.data["display"] = self.cts600.display().replace("/", "\n")
                self.cts600.data["led"] = self.cts600.led()
            else:
                self.cts600.updateData(False)
                if scan:
                    try:
                        fresh=self.menu.read_show_data()
                    except MenuUnavailable as exc:
                        # SHOW DATA is optional and panel navigation can briefly
                        # miss it while the controller changes screens. Keep the
                        # last measured values and let the next full scan retry;
                        # an absent optional menu must not fail basic telemetry.
                        self.menu_errors['SHOWDATA']=str(exc)
                        self.cts600.resetMenu()
                    else:
                        self.menu_errors.pop('SHOWDATA',None)
                        self.cts600.data={key:value for key,value in self.cts600.data.items() if not (key.startswith('T') and key[1:].isdigit())}
                        self.cts600.data.update(fresh)
                        self._last_profile_poll = dt_util.utcnow().timestamp()
                        for key in fresh:
                            if key.startswith('T') and key[1:].isdigit():self.cts600.metaData[key]={'kind':'temperature'}
                if time.monotonic() >= self._next_menu_scan:
                    path = USER_PATHS[self._menu_cursor % len(USER_PATHS)]
                    label = '/'.join(part[0] for part in path)
                    try:
                        self.menu.read_group(path)
                        self.menu_errors.pop(label,None)
                    except MenuUnavailable as exc:
                        self.menu_errors[label]=str(exc)
                        self.menu.values={key:value for key,value in self.menu.values.items() if key not in {spec.key for spec in BY_KEY.values() if spec.path==path}}
                    self._menu_cursor+=1
                    self._next_menu_scan=time.monotonic()+30
                if time.monotonic() >= self._next_alarm_scan:
                    try:
                        self.alarms=self.menu.read_alarms()
                        self.menu_errors.pop('ALARMS',None)
                    except MenuUnavailable as exc:
                        self.menu_errors['ALARMS']=str(exc)
                        self.alarms=None
                    self._next_alarm_scan=time.monotonic()+60
            return self.cts600.data.copy()

    def _tolerate_transport_failure(self):
        """Use cached telemetry for only the first consecutive TCP failure."""
        self._transient_failures += 1
        return self._transient_failures == 1 and bool(self.data) and self.last_success is not None

    async def _async_update_data(self):
        async with self._lock:
            if self._closed:
                raise UpdateFailed("CTS600 integration unloaded")
            try:
                value = self._check_guard()
                scan = time.monotonic() >= self._next_scan or not self._initialized
                result = await self.hass.async_add_executor_job(self._poll_sync, value, scan)
                self.last_show_data_success = getattr(self, '_last_profile_poll', None)
                if scan:
                    self._next_scan = time.monotonic() + 60
                self.last_success = dt_util.utcnow()
                self.last_error = None
                self._transient_failures = 0
                return result
            except (ConnectionError, TimeoutError, OSError) as exc:
                self.last_error = str(exc)
                self._initialized = False
                await self.hass.async_add_executor_job(self.transport.close)
                # A single broken TCP frame is common with serial gateways.
                # Preserve the last known state for one 5 s cycle and reconnect
                # on the next poll. Persistent failures still become UpdateFailed.
                if self._tolerate_transport_failure():
                    _LOGGER.debug('CTS600 transient transport failure; reconnecting on next poll: %s',exc)
                    return self.data.copy()
                raise UpdateFailed(str(exc)) from exc
            except Exception as exc:
                self.last_error = str(exc)
                self._initialized = False
                await self.hass.async_add_executor_job(self.transport.close)
                raise UpdateFailed(str(exc)) from exc

    async def async_shutdown(self):
        live_runtime = getattr(self, '_tuv_live_runtime', None)
        if live_runtime is not None:
            await live_runtime.async_shutdown()
        filter_runtime = getattr(self, '_filter_runtime', None)
        if filter_runtime is not None:
            await filter_runtime.async_shutdown()
        model_runtime = getattr(self, '_tuv_runtime', None)
        if model_runtime is not None:
            await model_runtime.async_shutdown()
        await super().async_shutdown()
        async with self._lock:
            self._closed = True
            await self.hass.async_add_executor_job(self.transport.close)

    def _command_sync(self, command, value, t15):
        with self.transport.operation():
            if not self._initialized:
                raise ConnectionError("CTS600 must reconnect before accepting commands")
            self.cts600.setT15(t15)
            if command == "button":
                self.cts600.key(value)
                self._manual_until = time.monotonic() + 30
                self.cts600.data["display"] = self.cts600.display().replace("/", "\n")
            else:
                self._manual_until = 0
                if command == "temperature":
                    self.cts600.setThermostat(value)
                elif command == "fan":
                    self.cts600.setFlow(value)
                elif command == "mode":
                    if value == "OFF":
                        self.cts600.key_off()
                    else:
                        self.cts600.resetMenu()
                        if self.cts600.display().startswith("OFF"):
                            self.cts600.key_on()
                        self.cts600.setMode(value)
                self.cts600.updateData(False)
                field = {"temperature": "thermostat", "fan": "flow", "mode": "mode"}[command]
                if self.cts600.data.get(field) != value:
                    raise ValueError("CTS600 command result was not confirmed by display readback")
            return self.cts600.data.copy()

    async def async_command(self, command, value):
        async with self._lock:
            if self._closed:
                raise HomeAssistantError("CTS600 integration unloaded")
            t15 = self._check_guard(require_commands=True)
            try:
                result = await self.hass.async_add_executor_job(self._command_sync, command, value, t15)
            except Exception as exc:
                self.last_error = str(exc)
                self._initialized = False
                await self.hass.async_add_executor_job(self.transport.close)
                self.async_set_update_error(UpdateFailed(str(exc)))
                # Never repeat a possibly-applied button/menu command automatically.
                raise HomeAssistantError(f"Příkaz nebyl potvrzen: {exc}") from exc
            self.last_success = dt_util.utcnow()
            self.last_command = command
            self.async_set_updated_data(result)

    def _menu_command_sync(self,command,payload,t15):
        with self.transport.operation(seconds=90 if command in ('clock','discover_service','discover_choices','weekly_slot') else 40):
            if not self._initialized:
                raise ConnectionError('CTS600 must reconnect before accepting commands')
            self.cts600.setT15(t15)
            self._manual_until=0
            if command=='setting':
                key=payload['key']
                if key=='air_mode':self.menu.set_air_mode_preserving_dhw(payload['value'])
                else:self.menu.set(key,payload['value'])
            elif command=='clock':
                self.menu.set_clock(payload['when'])
                self.menu.read_profile()
            elif command=='discover_choices':self.menu.discover_choices(payload['key'])
            elif command=='discover_service':
                self.cts600.enable_service_menu()
                self.menu.read_group(SERVICE_DHW)
            elif command in ('read_weekly','read_profile','read_alarm_history'):
                try:
                    if command=='read_weekly':read_slots(self.menu)
                    elif command=='read_profile':self.menu.read_profile()
                    else:self.menu.read_alarm_history()
                except MenuUnavailable:
                    self.cts600.resetMenu()
                    raise
            elif command=='weekly_slot':set_slot(self.menu,**payload)
            elif command=='acknowledge_alarm':self.alarms=self.menu.acknowledge_alarm(payload['code'])
            else:raise ValueError('Unsupported menu command')
            self.cts600.updateData(False)
            return self.cts600.data.copy()

    def _live_read_target_sync(self):
        with self.transport.operation(seconds=40):
            if not self._initialized:
                raise ConnectionError('CTS600 must reconnect before restoring the DHW target')
            return self.menu.read('dhw_temperature')

    async def async_live_dhw(self, expected, value, restoring, guard):
        """Serialize a physical compare/read/write; never overwrite a manual target."""
        BY_KEY['dhw_temperature'].validate(value)
        async with self._lock:
            if self._closed:
                raise HomeAssistantError('CTS600 integration unloaded')
            t15 = self._check_guard(require_commands=True)
            try:
                actual = await self.hass.async_add_executor_job(self._live_read_target_sync)
                if actual != expected:
                    self.async_set_updated_data(self.cts600.data.copy())
                    return False
                # Permissions and plan can change while waiting for the physical read.
                t15 = self._check_guard(require_commands=True)
                if not restoring and not guard():
                    raise HomeAssistantError('Live-control permission or plan expired before write')
                result = await self.hass.async_add_executor_job(
                    self._menu_command_sync, 'setting', {'key': 'dhw_temperature', 'value': value}, t15)
            except Exception as exc:
                self.last_error = str(exc)
                self._initialized = False
                await self.hass.async_add_executor_job(self.transport.close)
                self.async_set_update_error(UpdateFailed(str(exc)))
                raise HomeAssistantError(f'Live DHW write/readback failed; restoration retained: {exc}') from exc
            self.last_success = dt_util.utcnow()
            self.last_error = None
            self.last_command = 'tuv_live_restore' if restoring else 'tuv_live_delay'
            self.async_set_updated_data(result)
            return True

    async def async_menu_command(self,command,payload):
        # Validate before entering the executor/socket, then serialize the whole transaction.
        protected=command=='discover_service' or command=='acknowledge_alarm' and payload.get('code')!=19
        if command in ('setting','discover_choices'):
            if payload.get('key') not in BY_KEY:raise HomeAssistantError('Neznámá položka menu')
            spec=BY_KEY[payload['key']]
            protected=spec.protected
            if command=='setting':
                try:spec.validate(payload.get('value'),self.menu.choices.get(spec.key,()))
                except (TypeError,ValueError) as exc:raise HomeAssistantError(str(exc)) from exc
        elif command=='weekly_slot':
            try:validate_slot(**payload)
            except (TypeError,ValueError) as exc:raise HomeAssistantError(str(exc)) from exc
        elif command=='clock':
            when=payload.get('when')
            if not isinstance(when,datetime) or when.tzinfo is None or not 2000<=when.year<=2099:
                raise HomeAssistantError('Neplatné datum/čas s časovou zónou pro CTS600')
        elif command=='acknowledge_alarm':
            code=payload.get('code')
            if isinstance(code,bool) or not isinstance(code,int) or not 1<=code<=99:raise HomeAssistantError('Požadováno číslo alarmu 1–99')
        elif command not in ('clock','discover_service','read_weekly','read_profile','read_alarm_history','acknowledge_alarm'):
            raise HomeAssistantError('Nepodporovaný příkaz menu')
        async with self._lock:
            if self._closed:raise HomeAssistantError('CTS600 integration unloaded')
            t15=self._check_guard(require_commands=command not in ('read_weekly','read_profile','read_alarm_history'))
            if protected and not self.hass.states.is_state('input_boolean.nilan_service_control_enabled','on'):
                raise HomeAssistantError('Servisní nastavení Nilan je uzamčeno')
            try:
                result=await self.hass.async_add_executor_job(self._menu_command_sync,command,payload,t15)
            except Exception as exc:
                if isinstance(exc,MenuUnavailable) and command in ('read_weekly','read_profile','read_alarm_history'):
                    self.menu_errors[command]=str(exc)
                    if command=='read_weekly':self.menu.weekly_slots={}
                    elif command=='read_profile':self.menu.root_menus=None;self.menu.clock=None
                    else:self.menu.alarm_history=None
                    self.async_set_updated_data(self.cts600.data.copy())
                    raise HomeAssistantError(f'Tato nabídka není právě dostupná: {exc}') from exc
                self.last_error=str(exc)
                self._initialized=False
                await self.hass.async_add_executor_job(self.transport.close)
                self.async_set_update_error(UpdateFailed(str(exc)))
                raise HomeAssistantError(f'Změna menu nebyla potvrzena; část mohla být uložena: {exc}') from exc
            self.last_success=dt_util.utcnow()
            self.menu_errors.pop(command,None)
            self.last_error=None
            self.last_command=f'menu:{command}'
            self.async_set_updated_data(result)
            if command=='discover_choices':
                try:await self.async_save_options()
                except Exception:_LOGGER.warning('Načtené volby menu se nepodařilo uložit pro příští start',exc_info=True)
