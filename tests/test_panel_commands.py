"""Real CTS600 command methods against a synthetic panel; no transport or HA."""
from collections import deque
import importlib.util
from pathlib import Path
import sys

import pytest

path = Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600/nilan_cts600.py'
spec = importlib.util.spec_from_file_location('panel_command_test', path)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)
Key = protocol.Key


class Panel(protocol.CTS600):
    def __init__(self, *, temperature=22, flow=3, delayed=0):
        self.values = {'temperature': temperature, 'flow': flow, 'mode': 'HEAT'}
        self.stage = None
        self.buffer = None
        self.calls = []
        self.frames = deque()
        self.delayed = delayed
        self.on_enter = {}
        self.ignore_step = False
        self.jump_step = False
        self.wrong_field = False
        self.corrupt_frame = None
        self.commit_behavior = 'normal'
        self.commits = []

    def screen(self):
        values = {**self.values}
        if self.stage:
            values[self.stage] = self.buffer
        text = {key: str(value) for key, value in values.items()}
        text['flow'] = '>' + text['flow'] + '<'
        if self.stage:
            text[self.stage] = '{' + text[self.stage] + '}'
        return f"{text['mode']}   */{text['flow']} {text['temperature']}°C"

    def resetMenu(self, maxTries=10):
        self.calls.append('RESET')
        self.stage = self.buffer = None
        return self.screen()

    def key(self, key=Key.NONE):
        self.calls.append(key)
        before = self.screen()
        if key is Key.NONE:
            if self.corrupt_frame:
                return self.corrupt_frame
            return self.frames.popleft() if self.frames else self.screen()
        if key is Key.ENTER:
            old_stage = self.stage
            if self.stage in ('temperature', 'flow'):
                if self.commit_behavior == 'lost':
                    return before
                self.values[self.stage] = self.buffer
                self.commits.append((self.stage, self.buffer))
                if self.commit_behavior == 'uncertain':
                    raise OSError('Commit applied but acknowledgement lost')
            self.stage = {None: 'temperature', 'temperature': 'mode',
                          'mode': 'flow', 'flow': None}[self.stage]
            if old_stage in self.on_enter:
                self.values.update(self.on_enter[old_stage])
            self.buffer = self.values[self.stage] if self.stage else None
        elif key in (Key.UP, Key.DOWN):
            assert self.stage in ('temperature', 'flow'), 'Unsafe key in wrong field'
            if self.wrong_field:
                self.stage, self.buffer = 'mode', 'HEAT'
            elif not self.ignore_step:
                self.buffer += (1 if key is Key.UP else -1) * (2 if self.jump_step else 1)
        else:
            raise AssertionError(f'Unexpected key {key}')
        self.frames.extend([before] * self.delayed)
        # The action response itself is deliberately always stale.
        return before


def movements(panel):
    return [key for key in panel.calls if key in (Key.UP, Key.DOWN)]


@pytest.mark.parametrize('delay', [0, 1, 2])
def test_thermostat_uses_fresh_edit_value_instead_of_original_value(delay):
    p = Panel(temperature=22, delayed=delay)
    p.on_enter[None] = {'temperature': 19}
    assert p.setThermostat(22) == 'HEAT   */>3< 22°C'
    assert p.values['temperature'] == 22
    assert movements(p) == [Key.UP] * 3
    assert p.commits == [('temperature', 22)]


@pytest.mark.parametrize('delay', [0, 1, 2])
def test_flow_uses_fresh_one_instead_of_original_three(delay):
    p = Panel(flow=3, delayed=delay)
    p.on_enter['mode'] = {'flow': 1}
    assert p.setFlow(2) == 'HEAT   */>2< 22°C'
    assert movements(p) == [Key.UP]
    assert p.values['temperature'] == 22 and p.values['mode'] == 'HEAT'
    assert p.commits == [('temperature', 22), ('flow', 2)]


@pytest.mark.parametrize('method,initial,target,count,key', [
    ('setThermostat', 5, 30, 25, Key.UP), ('setThermostat', 30, 5, 25, Key.DOWN),
    ('setFlow', 1, 4, 3, Key.UP), ('setFlow', 4, 1, 3, Key.DOWN),
    ('setThermostat', 22, 22, 0, Key.UP), ('setFlow', 3, 3, 0, Key.UP),
])
def test_exact_steps_limits_and_same_target(method, initial, target, count, key):
    p = Panel(**{'temperature' if method == 'setThermostat' else 'flow': initial})
    getattr(p, method)(target)
    assert movements(p) == [key] * count


@pytest.mark.parametrize('method,target', [('setThermostat', 23), ('setFlow', 4)])
@pytest.mark.parametrize('failure', ['ignore_step', 'jump_step', 'wrong_field'])
def test_uncertain_step_is_never_repeated_or_committed(method, target, failure):
    p = Panel()
    setattr(p, failure, True)
    with pytest.raises(protocol.NilanCTS600ProtocolError):
        getattr(p, method)(target)
    assert movements(p) == [Key.UP]
    assert (('temperature', 23) if method == 'setThermostat' else ('flow', 4)) not in p.commits
    assert p.calls[-3:] == [Key.NONE] * 3 or failure == 'jump_step'


@pytest.mark.parametrize('frame', [
    'HEAT   */>3< {22}°C', 'HEAT   */>{3}< 22°C',
    '{HEAT} */>3< 22°C', 'HEAT   */{>3<} {22}°C',
    'SERVICE/{>3<}', 'HEAT   */{>3< 22°C',
])
def test_flow_does_not_step_or_commit_when_wrong_or_malformed_field_blinks(frame):
    p = Panel()
    # Inject only once the intended fan field should be active.
    original = p.key

    def key(value=Key.NONE):
        if value is Key.NONE and p.stage == 'flow':
            p.calls.append(value)
            return frame
        return original(value)

    p.key = key
    with pytest.raises(protocol.NilanCTS600ProtocolError):
        p.setFlow(4)
    assert not movements(p) and all(field != 'flow' for field, _ in p.commits)


@pytest.mark.parametrize('method,target', [('setThermostat', 23), ('setFlow', 4)])
@pytest.mark.parametrize('behavior', ['lost', 'uncertain'])
def test_commit_failure_is_not_retried(method, target, behavior):
    p = Panel()
    original = p.key
    commit_calls = []
    field = 'temperature' if method == 'setThermostat' else 'flow'

    def key(value=Key.NONE):
        if value is Key.ENTER and p.stage == field and p.buffer == target:
            p.commit_behavior = behavior
            commit_calls.append(value)
        return original(value)

    p.key = key
    with pytest.raises((protocol.NilanCTS600ProtocolError, OSError)):
        getattr(p, method)(target)
    assert len(commit_calls) == 1


@pytest.mark.parametrize('method,value', [
    ('setThermostat', 4), ('setThermostat', 31), ('setThermostat', 22.5),
    ('setFlow', 0), ('setFlow', 5), ('setFlow', True),
])
def test_invalid_target_sends_no_keys(method, value):
    p = Panel()
    with pytest.raises(protocol.NilanCTS600Exception):
        getattr(p, method)(value)
    assert not p.calls
