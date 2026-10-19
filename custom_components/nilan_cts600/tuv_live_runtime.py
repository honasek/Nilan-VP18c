"""HA lifecycle for opt-in DHW delay; recovery survives disable/reload/restart."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError

from .tuv_live import LiveControl, candidate

_LOGGER = logging.getLogger(__name__)
AUTOMATIC_GATE = 'input_boolean.nilan_automatic_control_enabled'


class TUVLiveRuntime(DataUpdateCoordinator):
    def __init__(self, hass, main):
        super().__init__(hass, _LOGGER, name=f'{main.name} DHW live control',
                         config_entry=main.entry, update_interval=None)
        self.main = main
        self.store = Store(hass, 1, f'nilan_cts600.{main.entry.entry_id}.tuv_live')
        self.identity = {key: main.data.get(key) for key in ('unit_type', 'software_1')}
        self.control = LiveControl(self._save, self._send)
        self._lock = asyncio.Lock()
        self._unsub = []
        self._task = None
        self._closed = False
        self._journal_error = None

    async def async_start(self):
        try:
            data = await self.store.async_load()
            if data is not None:
                if not all(self.identity.values()) or data.get('identity') != self.identity:
                    raise ValueError('Live-control journal belongs to a different controller/firmware')
                self.control.restore(data['control'])
        except Exception:
            self._journal_error = 'Live-control recovery journal is unreadable; inspect the stored target before enabling'
            _LOGGER.exception('DHW live-control journal could not be restored')
        self._unsub.append(self.main.async_add_listener(self._schedule))
        self._unsub.append(async_track_time_interval(self.hass, self._schedule, timedelta(seconds=30)))
        self._unsub.append(async_track_state_change_event(
            self.hass, [self.main.gate_entity, AUTOMATIC_GATE, 'input_boolean.nilan_tuv_shadow_enabled'], self._schedule))
        self._publish()
        self._schedule()

    async def _save(self, data):
        if not all(self.identity.values()):
            raise HomeAssistantError('Controller identity is not confirmed')
        await self.store.async_save({'identity': self.identity, 'control': data})

    def _context(self):
        runtime = getattr(self.main, '_tuv_runtime', None)
        data = (runtime.data or {}) if runtime else {}
        actual = self.main.menu.values.get('dhw_temperature')
        target = self.control.forecast_target(actual)
        return {'permitted': self.hass.states.is_state(self.main.gate_entity, 'on')
                and self.hass.states.is_state(AUTOMATIC_GATE, 'on')
                and self.hass.states.is_state('input_boolean.nilan_tuv_shadow_enabled', 'on')
                and self.main.t15_reading().valid,
                'valid': bool(data.get('valid')) and self.main.last_update_success,
                'sample_at': self.main.last_show_data_success,
                'plan': data.get('shadow'), 'target': target, 'actual_target': actual,
                'top': (self.main.data or {}).get('T11'), 'bottom': (self.main.data or {}).get('T12'),
                'lower': data.get('lower')}

    async def _send(self, expected, value, restoring):
        def guard():
            proposal, _ = candidate(self._context(), time.time())
            return self.control.enabled and proposal is not None and self._journal_error is None
        return await self.main.async_live_dhw(expected, value, restoring, guard)

    def _schedule(self, *args):
        if not self._closed and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_task(self._tick(), 'Nilan DHW live control')

    async def _tick(self):
        async with self._lock:
            if self._closed:
                return
            if self._journal_error:
                self.control.enabled = False
                self.control.status = 'error'
                self.control.reason = self._journal_error
            else:
                await self.control.step(self._context(), time.time())
            self._publish()

    def _publish(self):
        self.async_set_updated_data({'enabled': self.control.enabled, 'status': self.control.status,
                                    'reason': self.control.reason, **self.control.dump()})

    async def async_enable(self, enabled):
        async with self._lock:
            if enabled and self._journal_error:
                raise HomeAssistantError(self._journal_error)
            if enabled and not self._context()['permitted']:
                raise HomeAssistantError('Enable manual controls, automatic controls and DHW shadow collection; T15 must be valid')
            self.control.enabled = enabled
            await self.control.step(self._context(), time.time())
            self._publish()
            if self.control.status in ('error', 'restore_pending'):
                raise HomeAssistantError(self.control.reason)

    async def async_shutdown(self):
        if self._closed:
            return
        self._closed = True
        for unsub in self._unsub:
            unsub()
        self._unsub = []
        if self._task is not None and not self._task.done():
            await self._task
        async with self._lock:
            self.control.enabled = False
            await self.control.step(self._context(), time.time())
            self._publish()
        await super().async_shutdown()
