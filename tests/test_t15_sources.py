"""Device-free selection/configuration regressions for primary and backup T15."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

path = Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600/t15_guard.py'
spec = importlib.util.spec_from_file_location('t15_sources_test', path)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
PRIMARY, BACKUP = 'sensor.primary', 'sensor.backup'


@pytest.mark.parametrize('data,options,expected', [
    ({}, {}, (PRIMARY, BACKUP)),
    ({'sensor_T15': 'sensor.legacy'}, {}, ('sensor.legacy', None)),
    ({'sensor_T15': PRIMARY, 'sensor_T15_backup': 'sensor.legacy_backup'}, {}, (PRIMARY, 'sensor.legacy_backup')),
    ({'sensor_T15': PRIMARY, 'sensor_T15_backup': BACKUP}, {'sensor_T15': 'sensor.own', 'sensor_T15_backup': 'sensor.second'}, ('sensor.own', 'sensor.second')),
    ({'sensor_T15_backup': BACKUP}, {'sensor_T15_backup': None}, (PRIMARY, None)),
    ({'sensor_T15_backup': BACKUP}, {'sensor_T15_backup': ''}, (PRIMARY, None)),
])
def test_options_precedence_and_explicit_backup_removal(data, options, expected):
    before = dict(data), dict(options)
    assert guard.configured_t15_sources(data, options, PRIMARY, BACKUP) == expected
    assert (data, options) == before


def state(value='21.5', *, age=0, unit='°C', restored=False):
    return SimpleNamespace(state=value, last_reported=NOW-timedelta(seconds=age),
                           attributes={'unit_of_measurement': unit, 'restored': restored})


@pytest.mark.parametrize('bad', [None, state('unavailable'), state('unknown'), state(age=901),
                               state('NaN'), state('41'), state(unit='°F'), state(restored=True)])
def test_invalid_primary_uses_backup_then_recovers(bad):
    states = {PRIMARY: bad, BACKUP: state('20')}
    selected, reading = guard.select_t15(states.get, PRIMARY, BACKUP, NOW)
    assert selected == BACKUP and reading.valid and reading.value == 20
    states[PRIMARY] = state()
    selected, reading = guard.select_t15(states.get, PRIMARY, BACKUP, NOW)
    assert selected == PRIMARY and reading.value == 21.5


def test_disabled_backup_and_both_invalid_never_substitute_a_value():
    states = {PRIMARY: state('unavailable'), BACKUP: state('20')}
    selected, reading = guard.select_t15(states.get, PRIMARY, None, NOW)
    assert selected is None and not reading.valid and reading.value is None
    states[BACKUP] = state(age=901)
    selected, reading = guard.select_t15(states.get, PRIMARY, BACKUP, NOW)
    assert selected is None and not reading.valid
