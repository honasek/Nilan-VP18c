"""TCP setup is saved only after physical commissioning acknowledgement."""
from __future__ import annotations
import ipaddress
import re
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector
from homeassistant.util import dt as dt_util
from .const import DOMAIN, DEFAULT_T15, DEFAULT_T15_BACKUP, DEFAULT_MAX_AGE
from .t15_guard import configured_t15_sources, select_t15
from .entity_config import OPTIONAL_FIELDS, SHADOW_FIELDS, NIGHT_FIELDS


def _excluded_sources(hass, entry_id=None):
    """The integration's own registered T15 cannot feed itself after a rename."""
    excluded = []
    if entry_id is not None:
        entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}-sensor-T15")
        if entity_id:
            excluded.append(entity_id)
    return excluded


def _t15_errors(hass, primary, backup, excluded, max_age=DEFAULT_MAX_AGE):
    errors = {}
    for field, entity_id in (("sensor_T15", primary), ("sensor_T15_backup", backup)):
        if entity_id is None and field == "sensor_T15_backup":
            continue
        if not isinstance(entity_id, str) or not re.fullmatch(r"sensor\.[a-z0-9_]+", entity_id):
            errors[field] = "invalid_t15"
        elif entity_id in excluded:
            errors[field] = "recursive_t15"
    if primary and backup == primary:
        errors["sensor_T15_backup"] = "duplicate_t15"
    if errors:
        return errors
    _, reading = select_t15(hass.states.get, primary, backup, dt_util.utcnow(), max_age)
    if not reading.valid:
        errors["sensor_T15"] = "invalid_t15"
    return errors


class CTS600ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CTS600OptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            host = user_input["host"].strip()
            try:
                ipaddress.ip_address(host)
                host_ok = True
            except ValueError:
                host_ok = bool(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", host))
            if not host_ok:
                errors["host"] = "invalid_host"
            errors.update(_t15_errors(self.hass, user_input["sensor_T15"],
                                      user_input.get("sensor_T15_backup") or None,
                                      _excluded_sources(self.hass)))
            if not user_input.get("panel_disconnected"):
                errors["base"] = "panel_required"
            if not errors:
                data = {k: v for k, v in user_input.items() if k != "panel_disconnected"}
                data["host"] = host
                await self.async_set_unique_id(f"{host}:{data['tcp_port']}")
                self._abort_if_unique_id_configured()
                # No test frame is sent by this form. Entry setup starts emulation.
                return self.async_create_entry(title=data["name"], data=data)
        schema = vol.Schema({
            vol.Required("name", default="Nilan CTS600"): str,
            vol.Required("host"): str,
            vol.Required("tcp_port", default=4196): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required("sensor_T15"): selector({"entity": {"filter": {"domain": "sensor"}}}),
            vol.Optional("sensor_T15_backup"): selector({"entity": {"filter": {"domain": "sensor"}}}),
            vol.Required("commands_enabled_entity"): selector({"entity": {"filter": {"domain": "input_boolean"}}}),
            vol.Required("panel_disconnected", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class CTS600OptionsFlow(OptionsFlowWithReload):
    """Select external inputs without requiring any particular household IDs."""

    async def async_step_init(self, user_input=None):
        excluded = _excluded_sources(self.hass, self.config_entry.entry_id)
        errors = {}
        if user_input is not None:
            primary = user_input.get("sensor_T15")
            backup = user_input.get("sensor_T15_backup") or None
            errors = _t15_errors(self.hass, primary, backup, excluded,
                                 user_input.get('t15_max_age_seconds',DEFAULT_MAX_AGE))
            if not errors:
                self._pending={**self.config_entry.options,**user_input,'sensor_T15_backup':backup}
                for key in ('power_entity','service_control_entity','automatic_control_entity','t15_max_age_entity'):
                    self._pending[key]=user_input.get(key) or None
                return await self.async_step_shadow()
            suggested = dict(user_input)
        else:
            primary, backup = configured_t15_sources(
                self.config_entry.data, self.config_entry.options, DEFAULT_T15, DEFAULT_T15_BACKUP)
            suggested = {**self.config_entry.data,**self.config_entry.options,
                         'sensor_T15':primary,'sensor_T15_backup':backup}
        schema = vol.Schema({
            vol.Required("sensor_T15"): selector({"entity": {
                "filter": {"domain": "sensor"}, "exclude_entities": excluded,
            }}),
            vol.Optional("sensor_T15_backup"): selector({"entity": {
                "filter": {"domain": "sensor"}, "exclude_entities": excluded,
            }}),
            vol.Required('commands_enabled_entity'): selector({'entity':{'filter':{'domain':'input_boolean'}}}),
            vol.Required('t15_max_age_seconds',default=DEFAULT_MAX_AGE): vol.All(vol.Coerce(int),vol.Range(min=60,max=3600)),
            **{vol.Optional(key):selector({'entity':{'filter':{'domain':OPTIONAL_FIELDS[key]}}})
               for key in ('power_entity','service_control_entity','automatic_control_entity','t15_max_age_entity')},
        })
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(
            schema, {key: value for key, value in suggested.items() if value is not None}), errors=errors)

    async def _selectors(self,step,fields,user_input,next_step=None):
        if user_input is not None:
            self._pending.update({key:user_input.get(key) or None for key in fields})
            if next_step:return await next_step()
            return self.async_create_entry(title='',data=self._pending)
        suggested={**self.config_entry.data,**self.config_entry.options,**self._pending}
        schema=vol.Schema({vol.Optional(key):selector({'entity':{'filter':{'domain':domain}}})
                           for key,domain in fields.items()})
        return self.async_show_form(step_id=step,data_schema=self.add_suggested_values_to_schema(
            schema,{key:suggested[key] for key in fields if suggested.get(key)}))

    async def async_step_shadow(self,user_input=None):
        fields={f'shadow_{key}_entity':domain for key,domain in SHADOW_FIELDS.items()}
        fields['shadow_initialized_entity']='input_boolean'
        return await self._selectors('shadow',fields,user_input,self.async_step_night)

    async def async_step_night(self,user_input=None):
        return await self._selectors('night',NIGHT_FIELDS,user_input)
