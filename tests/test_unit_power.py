"""Real panel power orchestration against a synthetic controller, without HA/I/O."""
from collections import deque
import importlib
from pathlib import Path
import sys
import types

import pytest

component = Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600'
pkg = types.ModuleType('power_validation'); pkg.__path__ = [str(component)]; sys.modules[pkg.__name__] = pkg
power = importlib.import_module(pkg.__name__+'.unit_power')
Key = importlib.import_module(pkg.__name__+'.nilan_cts600').Key


class Panel:
    def __init__(self, mode='HEAT', resume='HEAT', delay=0):
        self.mode, self.resume, self.delay = mode, resume, delay
        self.data = {'mode': mode, 'thermostat': 22, 'flow': 3}
        self.keys = []; self.frames = deque(); self.ignore = False; self.lost = False
        self.bad_final = False; self.bad_screen = None

    def screen(self):
        return f'{self.mode}   */>3< 22°C'

    def resetMenu(self):
        return self.screen()

    def key(self, key=Key.NONE):
        self.keys.append(key)
        if key is Key.NONE:
            return self.bad_screen or (self.frames.popleft() if self.frames else self.screen())
        before = self.screen()
        if not self.ignore:
            self.mode = self.resume if key is Key.ON else 'OFF'
        self.frames.extend([before]*self.delay)
        if self.lost:
            raise OSError('Key applied but acknowledgement lost')
        return before  # Never trust the key's immediate response.

    def updateData(self, full):
        assert full is False
        if not self.bad_final:
            self.data['mode'] = self.mode


@pytest.mark.parametrize('mode', ['AUTO', 'HEAT', 'COOL'])
@pytest.mark.parametrize('delay', [0, 1, 2])
def test_on_resumes_controller_mode_without_editing_it(mode, delay):
    p = Panel('OFF', resume=mode, delay=delay)
    result = power.set_power(p, True)
    assert result['mode'] == mode and result['thermostat'] == 22 and result['flow'] == 3
    assert [k for k in p.keys if k is not Key.NONE] == [Key.ON]


@pytest.mark.parametrize('delay', [0, 1, 2])
def test_off_is_confirmed_despite_delayed_action_response(delay):
    p = Panel(delay=delay)
    assert power.set_power(p, False)['mode'] == 'OFF'
    assert [k for k in p.keys if k is not Key.NONE] == [Key.OFF]


@pytest.mark.parametrize('mode,enabled', [('HEAT', True), ('AUTO', True), ('COOL', True), ('OFF', False)])
def test_same_state_request_reads_without_repeating_key(mode, enabled):
    p = Panel(mode)
    assert power.set_power(p, enabled)['mode'] == mode
    assert all(k is Key.NONE for k in p.keys)


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('failure', ['ignore', 'lost', 'bad_final'])
def test_failed_or_uncertain_power_write_is_never_replayed(enabled, failure):
    p = Panel('OFF' if enabled else 'HEAT');setattr(p, failure, True)
    with pytest.raises((ValueError, OSError)):
        power.set_power(p, enabled)
    assert [k for k in p.keys if k is not Key.NONE] == [Key.ON if enabled else Key.OFF]
    if failure == 'bad_final':
        assert 'mode' not in p.data  # Cached requested state cannot pass confirmation.


@pytest.mark.parametrize('screen', ['SERVICE/OFF', 'OFFLINE/>3< 22°C', '{HEAT}/>3< 22°C', 'HEAT/invalid', 'HEAT/>3< {22}°C'])
def test_unknown_or_editing_main_screen_prevents_power_write(screen):
    p = Panel();p.bad_screen = screen
    with pytest.raises(ValueError): power.set_power(p, False)
    assert p.keys == [Key.NONE]*3


@pytest.mark.parametrize('value', [0, 1, None, 'ON', 'OFF'])
def test_invalid_power_target_sends_no_keys(value):
    p = Panel()
    with pytest.raises(ValueError): power.set_power(p, value)
    assert not p.keys


@pytest.mark.parametrize('mode,expected', [('AUTO', True), ('HEAT', True), ('COOL', True), ('OFF', False),
                                           (None, None), ('HEATING', None), ('unknown', None)])
def test_state_is_known_only_for_explicit_main_modes(mode, expected):
    assert power.power_state(mode) is expected
