"""TCP setup is saved only after physical commissioning acknowledgement."""
from __future__ import annotations
import ipaddress
import re
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.selector import selector
from homeassistant.util import dt as dt_util
from .const import DOMAIN, DEFAULT_T15, DEFAULT_T15_BACKUP, DEFAULT_GATE, DEFAULT_MAX_AGE
from .t15_guard import select_t15


class CTS600ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
            max_age = DEFAULT_MAX_AGE
            threshold = self.hass.states.get("input_number.nilan_t15_max_age_minutes")
            if threshold is not None:
                try:
                    minutes = float(threshold.state)
                    if not 1 <= minutes <= 60:
                        raise ValueError("Invalid T15 age threshold")
                    max_age = minutes * 60
                except (TypeError, ValueError):
                    errors["sensor_T15"] = "invalid_t15"
            _, reading = select_t15(self.hass.states.get, user_input["sensor_T15"],
                                    user_input.get("sensor_T15_backup"), dt_util.utcnow(), max_age)
            if not reading.valid:
                errors["sensor_T15"] = "invalid_t15"
            if not user_input.get("panel_disconnected"):
                errors["base"] = "panel_required"
            if not errors:
                data = {k: v for k, v in user_input.items() if k != "panel_disconnected"}
                data["host"] = host
                await self.async_set_unique_id(f"{host}:{data['tcp_port']}")
                self._abort_if_unique_id_configured()
                # No test frame is sent by this form. Entry setup starts emulation.
                return self.async_create_entry(title=data["name"], data=data)
        host_helper = self.hass.states.get("input_text.nilan_gateway_host")
        host_default = host_helper.state if host_helper and host_helper.state not in ("unknown", "unavailable") else ""
        port_helper = self.hass.states.get("input_number.nilan_gateway_port")
        try:
            port_default = int(float(port_helper.state)) if port_helper else 4196
        except (TypeError, ValueError):
            port_default = 4196
        schema = vol.Schema({
            vol.Required("name", default="Nilan VP18 K"): str,
            vol.Required("host", default=host_default): str,
            vol.Required("tcp_port", default=port_default): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required("sensor_T15", default=DEFAULT_T15): selector({"entity": {"filter": {"domain": "sensor"}}}),
            vol.Optional("sensor_T15_backup", default=DEFAULT_T15_BACKUP): selector({"entity": {"filter": {"domain": "sensor"}}}),
            vol.Required("commands_enabled_entity", default=DEFAULT_GATE): selector({"entity": {"filter": {"domain": "input_boolean"}}}),
            vol.Required("panel_disconnected", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
