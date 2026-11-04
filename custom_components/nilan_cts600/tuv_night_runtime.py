"""HA adapter for the opt-in night DHW target boost."""
import asyncio
from datetime import timedelta, datetime, timezone
import logging
import time

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .entity_config import NIGHT_FIELDS, selected, is_on
from .tuv_night import NightControl, plan, finite, MAX_START_DELAY

_LOGGER = logging.getLogger(__name__)


class TUVNightRuntime(DataUpdateCoordinator):
    def __init__(self, hass, main):
        super().__init__(hass, _LOGGER, name=f'{main.name} night DHW',
                         config_entry=main.entry, update_interval=None)
        self.main = main
        self.control = NightControl(self._save, self._send)
        self.store = Store(hass, 1, f'nilan_cts600.{main.entry.entry_id}.tuv_night')
        self.identity = {key: main.data.get(key) for key in ('unit_type', 'software_1')}
        self._zone = None
        self._unsub = []
        self._task = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._journal_error = None

    @property
    def enabled(self):
        return is_on(self.hass, self.main.config, 'night_enabled_entity')

    async def async_start(self):
        self._zone = await dt_util.async_get_time_zone(self.hass.config.time_zone)
        try:
            stored = await self.store.async_load()
            if stored is not None:
                if not all(self.identity.values()) or stored.get('identity') != self.identity:
                    raise ValueError('Night journal belongs to another controller')
                self.control.restore(stored['control'])
        except Exception:
            self._journal_error = 'Night recovery journal is unreadable; inspect the controller target'
            _LOGGER.exception('Night DHW journal could not be restored')
        self._unsub.append(self.main.async_add_listener(self._schedule))
        self._unsub.append(async_track_time_interval(self.hass, self._schedule, timedelta(seconds=15)))
        watched = [selected(self.main.config, key) for key in
                   (*NIGHT_FIELDS, 'automatic_control_entity', 'commands_enabled_entity')]
        if any(watched):
            self._unsub.append(async_track_state_change_event(self.hass, [v for v in watched if v], self._schedule))
        self._publish(self._context())
        self._schedule()

    async def _save(self, data):
        if not all(self.identity.values()):
            raise ValueError('Controller identity is unavailable')
        await self.store.async_save({'identity': self.identity, 'control': data})

    def _settings(self):
        values = {}
        for key, domain in NIGHT_FIELDS.items():
            if key in ('night_enabled_entity', 'price_entity'):
                continue
            entity_id = selected(self.main.config, key)
            state = self.hass.states.get(entity_id) if entity_id else None
            if not state or state.state in ('unknown', 'unavailable') or state.attributes.get('restored'):
                raise ValueError('Night setting is unavailable: ' + key)
            if domain == 'input_number':
                value = float(state.state)
                if not finite(value) or not value.is_integer():
                    raise ValueError('Night target and duration must be whole numbers')
                values[key] = int(value)
            else:
                if state.attributes.get('has_date') or state.state[5:] not in ('', ':00'):
                    raise ValueError('Select a time-only helper with zero seconds')
                values[key] = state.state[:5]
        if not 5 <= values['night_target_entity'] <= 60:
            raise ValueError('Night target must be between 5 and 60 °C')
        return values

    def _context(self):
        now = time.time()
        live = getattr(self.main, '_tuv_live_runtime', None)
        automatic = selected(self.main.config, 'automatic_control_entity')
        permitted = (is_on(self.hass, self.main.config, 'commands_enabled_entity')
                     and (not automatic or is_on(self.hass, self.main.config, 'automatic_control_entity'))
                     and self.main.t15_reading().valid
                     and not (live and (live.control.enabled or live.control.lease)))
        at = self.main.last_show_data_success
        valid = (self.main.last_update_success and finite(at) and -5 <= now - at <= 180
                 and finite((self.main.data or {}).get('T12'))
                 and (self.main.data or {}).get('mode') in ('AUTO', 'HEAT', 'COOL'))
        result = {'enabled': self.enabled, 'permitted': permitted, 'valid': bool(valid),
                  'actual_target': self.main.menu.values.get('dhw_temperature'),
                  'bottom': (self.main.data or {}).get('T12'), 'sample_at': at,
                  'plan': None, 'error': None, 'settings_key': None}
        try:
            settings = self._settings()
            result['target'] = settings['night_target_entity']
            result['settings_key'] = [settings[key] for key in sorted(settings)]
            entity_id = selected(self.main.config, 'price_entity')
            price = self.hass.states.get(entity_id) if entity_id else None
            if not price or price.attributes.get('restored'):
                raise ValueError('Price curve is unavailable')
            result['plan'], result['error'] = plan(
                price.attributes, now, settings['night_start_entity'], settings['night_end_entity'],
                settings['night_duration_entity'], self._zone)
            result['price_unit'] = price.attributes.get('unit_of_measurement')
        except (ValueError, TypeError, KeyError) as exc:
            result['error'] = str(exc)
        return result

    async def _send(self, expected, value, restoring):
        def guard():
            context = self._context()
            selected_plan = context.get('plan')
            return (not self._journal_error and context['enabled'] and context['valid'] and context['permitted']
                    and context['settings_key'] == self.control.lease['settings_key']
                    and selected_plan is not None and selected_plan['start'] == self.control.lease['start']
                    and time.time() - selected_plan['start'] <= MAX_START_DELAY
                    and time.time() < self.control.lease['end'])
        return await self.main.async_live_dhw(expected, value, restoring, guard)

    @callback
    def _schedule(self, *args):
        if not self._closed and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_task(self._tick(), 'Nilan night DHW')

    async def _tick(self):
        async with self._lock:
            context = self._context()
            if self._journal_error:
                self.control.status, self.control.reason = 'error', self._journal_error
            else:
                selected_plan = context.get('plan')
                now = time.time()
                if (self.control.lease is None and context['enabled'] and context['permitted'] and context['valid']
                        and selected_plan and selected_plan['window_end'] > self.control.consumed_until
                        and 0 <= now - selected_plan['start'] <= MAX_START_DELAY):
                    try:
                        context['actual_target'] = await self.main.async_read_dhw_target()
                    except Exception as exc:
                        context['valid'], context['error'] = False, str(exc)
                await self.control.step(context, time.time())
            self._publish(context)

    def _publish(self, context):
        selected_plan = context.get('plan')
        public_plan = None
        if selected_plan:
            public_plan = {key: (datetime.fromtimestamp(value, timezone.utc).isoformat()
                           if key in ('start', 'end', 'window_start', 'window_end') else value)
                           for key, value in selected_plan.items()}
        self.async_set_updated_data({'status': self.control.status, 'reason': self.control.reason,
            'enabled': context['enabled'], 'permitted': context['permitted'], 'plan': public_plan,
            'plan_error': context.get('error'), 'price_unit': context.get('price_unit'),
            'target_c': context.get('target'), 't12_c': context.get('bottom'),
            'original_target_c': self.control.lease['target'] if self.control.lease else None,
            'pending_restore': self.control.lease is not None and self.control.status == 'restore_pending',
            'outcome': self.control.outcome})

    async def async_shutdown(self):
        if self._closed:
            return
        self._closed = True
        for unsub in self._unsub:
            unsub()
        if self._task is not None and not self._task.done():
            await self._task
        async with self._lock:
            if self.control.lease:
                self.control.recovering = True
                await self.control.step({'enabled': False}, time.time())
        await super().async_shutdown()
