"""Exercise the real coordinator with slow synthetic I/O, never a device socket."""
import asyncio
from contextlib import contextmanager
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.update_coordinator import UpdateFailed


async def validate():
    package = ModuleType('nilan_poll_validation')
    package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600')]
    sys.modules[package.__name__] = package
    co = importlib.import_module(package.__name__ + '.coordinator')
    clock = SimpleNamespace(now=1000.0)
    events = []
    durations = []

    def spend(label, seconds):
        events.append(label)
        clock.now += seconds

    class Transport:
        def __init__(self):
            self.closes = 0

        def connect(self):
            pass

        def close(self):
            self.closes += 1

        @contextmanager
        def operation(self, seconds=25):
            start = clock.now
            try:
                yield
            finally:
                elapsed = clock.now - start
                durations.append(elapsed)
                assert elapsed <= seconds, f'Combined polling exceeded deadline: {elapsed}'

    class Panel:
        def __init__(self):
            self.data = {'mode': 'HEAT', 'thermostat': 22, 'flow': 3, 'T99': 99}
            self.metaData = {}
            self.fail_basic = False

        def initialize(self):
            spend('initialize', 8)

        def slaveID(self):
            return 'synthetic'

        def setT15(self, value):
            assert value == 21
            events.append('T15')

        def updateData(self, full):
            assert full is False
            spend('basic', 2)
            if self.fail_basic:
                raise ConnectionResetError('synthetic reset')

        def key(self, value):
            assert value is co.Key.NONE
            events.append('manual')

        def display(self):
            return 'HEAT/3 22C'

        def led(self):
            return 1

        def resetMenu(self):
            events.append('reset')

    class Menu:
        def __init__(self, panel):
            self.values = {}
            self.fail = None
            self.groups = []

        def set_language(self, value):
            assert value == 'ENGLISH'
            spend('language', 2)
            return True

        def read_show_data(self):
            spend('show', 16)
            if self.fail:
                raise self.fail
            return {'T11': 51, 'T12': 47, 'status': 'HOTWATER',
                    'unit_type': 'VP18', 'software_1': '1.0'}

        def read_group(self, path):
            self.groups.append(path)
            spend('group', 12)
            if self.fail:
                raise self.fail

        def read_alarms(self):
            spend('alarms', 10)
            if self.fail:
                raise self.fail
            return []

    cases = 0
    with tempfile.TemporaryDirectory(prefix='nilan-poll-') as directory:
        hass = HomeAssistant(directory)
        hass.set_state(CoreState.running)
        hass.states.async_set('sensor.room', '21', {'unit_of_measurement': '°C'})
        entry = SimpleNamespace(
            entry_id='poll-test', options={}, async_on_unload=lambda fn: None,
            state=ConfigEntryState.SETUP_IN_PROGRESS,
            data={'host': 'example.invalid', 'tcp_port': 4196,
                  'sensor_T15': 'sensor.room', 'sensor_T15_backup': None},
        )
        panel = Panel()
        transport = Transport()
        with patch.object(co, 'time', SimpleNamespace(monotonic=lambda: clock.now)), \
                patch.object(co, 'CTS600', return_value=panel), \
                patch.object(co, 'TransparentTCP', return_value=transport), \
                patch.object(co, 'MenuControls', Menu):
            main = co.CTS600Coordinator(hass, entry)

            async def poll():
                events.clear()
                result = await main._async_update_data()
                main.data = result
                assert 'T15' in events
                assert sum(events.count(x) for x in ('show', 'group', 'alarms')) <= 1
                return events[:]

            def due():
                main._next_scan = main._next_menu_scan = main._next_alarm_scan = 0

            try:
                assert await poll() == ['initialize', 'language', 'T15', 'T15', 'basic']
                assert main._next_scan == 0 and main.last_show_data_success is None
                assert 'unit_type' not in main.data
                cases += 1

                # First identity/temperature failure cannot pass setup using
                # only cached basic telemetry from the initialization cycle.
                main.menu.fail = TimeoutError('synthetic startup timeout')
                try:
                    await poll()
                except UpdateFailed:
                    pass
                else:
                    raise AssertionError('Startup accepted without the first SHOW DATA read')
                main.menu.fail = None
                main._transient_failures = 0
                transport.closes = 0
                await poll()  # Reconnect-only cycle, then retry startup scan.
                cases += 1

                assert await poll() == ['T15', 'basic', 'show']
                assert main.data['T12'] == 47 and 'T99' not in main.data
                assert main.data['unit_type'] == 'VP18' and main.data['software_1'] == '1.0'
                assert main.cts600.metaData['T12'] == {'kind': 'temperature'}
                measured_at = main.last_show_data_success
                assert measured_at is not None
                cases += 1

                show_deadline = main._next_scan
                assert await poll() == ['T15', 'basic', 'group']
                assert main._next_scan == show_deadline
                assert main.last_show_data_success == measured_at
                assert await poll() == ['T15', 'basic', 'alarms']
                assert main.alarms == [] and main.last_show_data_success == measured_at
                cases += 1

                assert await poll() == ['T15', 'basic']
                cases += 1

                # Every task gets a turn even when all deadlines remain overdue.
                main._poll_cursor = 0
                for expected in ['show', 'group', 'alarms'] * 3:
                    due()
                    assert await poll() == ['T15', 'basic', expected]
                assert main.menu.groups == [co.USER_PATHS[i % len(co.USER_PATHS)] for i in range(4)]
                cases += 1

                # Manual display access must not silently postpone SHOW DATA.
                due()
                main._manual_until = clock.now + 20
                for _ in range(2):
                    assert await poll() == ['T15', 'manual']
                    assert main._next_scan == main._next_menu_scan == main._next_alarm_scan == 0
                main._manual_until = 0
                assert await poll() == ['T15', 'basic', 'show']
                cases += 1

                # Missing optional menus retain their existing fallback semantics.
                due()
                main._poll_cursor = 0
                old_data = panel.data.copy()
                measured_at = main.last_show_data_success
                main.menu.fail = co.MenuUnavailable('synthetic missing menu')
                assert await poll() == ['T15', 'basic', 'show', 'reset']
                assert panel.data == old_data and main.last_show_data_success == measured_at
                assert 'SHOWDATA' in main.menu_errors and main._next_scan > clock.now
                cases += 1

                path = co.USER_PATHS[main._menu_cursor % len(co.USER_PATHS)]
                group_keys = {spec.key for spec in co.BY_KEY.values() if spec.path == path}
                assert group_keys
                main.menu.values.update(dict.fromkeys(group_keys, 50))
                main.menu.values['unrelated'] = 123
                assert await poll() == ['T15', 'basic', 'group']
                assert not group_keys.intersection(main.menu.values)
                assert main.menu.values['unrelated'] == 123
                assert await poll() == ['T15', 'basic', 'alarms']
                assert main.alarms is None and 'ALARMS' in main.menu_errors
                cases += 1

                # An I/O failure leaves the unfinished scan due. A successful
                # initialization-only cycle must not reset repeated-error grace.
                due()
                main._poll_cursor = 0
                main.menu.fail = TimeoutError('synthetic scan timeout')
                assert await poll() == ['T15', 'basic', 'show']
                assert not main._initialized and transport.closes == 1
                assert main._next_scan == 0 and main._transient_failures == 1
                await poll()
                assert main._initialized and main._transient_failures == 1
                try:
                    await poll()
                except UpdateFailed as exc:
                    assert 'synthetic scan timeout' in str(exc)
                else:
                    raise AssertionError('Repeated scan failure was hidden by reconnection')
                assert transport.closes == 2 and main._transient_failures == 2
                cases += 1

                main.menu.fail = None
                await poll()  # Reconnect only.
                assert await poll() == ['T15', 'basic', 'show']
                assert main._transient_failures == 0 and main.last_error is None
                assert 'SHOWDATA' not in main.menu_errors
                cases += 1

                # Lost basic telemetry still propagates after one transient miss.
                panel.fail_basic = True
                await poll()
                try:
                    await poll()
                except UpdateFailed as exc:
                    assert 'synthetic reset' in str(exc)
                else:
                    raise AssertionError('Persistent reset was hidden')
                assert not main._initialized
                cases += 1

                # Exercise real entry setup: both bounded startup polls finish
                # before restoring options or constructing identity-bound stores.
                setup = importlib.import_module(package.__name__ + '.__init__')
                panel.fail_basic = False
                main.menu.fail = None
                main = co.CTS600Coordinator(hass, entry)
                hass.config_entries = SimpleNamespace(async_forward_entry_setups=AsyncMock())

                class Runtime:
                    def __init__(self, hass, coordinator):
                        assert coordinator.data['unit_type'] == 'VP18'
                        assert coordinator.data['software_1'] == '1.0'

                    async def async_start(self):
                        pass

                    async def async_shutdown(self):
                        pass

                events.clear()
                with patch.object(setup, 'CTS600Coordinator', return_value=main), \
                        patch.object(setup, 'FilterMaintenance', Runtime), \
                        patch.object(setup, 'TUVLiveRuntime', Runtime), \
                        patch.object(setup, 'TUVNightRuntime', Runtime), \
                        patch.object(setup, 'register_services'):
                    assert await setup.async_setup_entry(hass, entry)
                assert events == ['initialize', 'language', 'T15', 'T15', 'basic', 'T15', 'basic', 'show']
                assert entry.runtime_data is main and main.last_show_data_success is not None
                cases += 1
            finally:
                await main.async_shutdown()

    return {'home_assistant': version('homeassistant'), 'cases': cases,
            'max_simulated_operation_seconds': max(durations),
            'one_optional_scan_per_cycle': True, 'freshness_preserved': True,
            'repeated_failures_reported': True, 'device_socket_connections': 0}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(validate())))
