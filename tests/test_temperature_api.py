"""Exercise both import-time API branches without installing Home Assistant."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('native_api', [False, True])
@pytest.mark.parametrize('module,class_name', [
    ('climate', 'CTS600Climate'),
    ('water_heater', 'CTS600WaterHeater'),
    ('water_heater', 'CTS600SeparateHeater'),
])
def test_temperature_contract(native_api, module, class_name):
    class Base:
        pass

    class Coordinator:
        pass

    if native_api:
        Base.native_temperature_unit = property(lambda self: self._attr_native_temperature_unit)
    else:
        Base.temperature_unit = property(lambda self: self._attr_temperature_unit)

    class Setting(Coordinator):
        @property
        def controller_value(self):
            return self.coordinator.menu.values['target']

    path = Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600' / f'{module}.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    env = {'ClimateEntity': Base, 'WaterHeaterEntity': Base,
           'CoordinatorEntity': Coordinator, 'CTS600SettingEntity': Setting,
           'UnitOfTemperature': SimpleNamespace(CELSIUS='°C'),
           'ClimateEntityFeature': SimpleNamespace(TARGET_TEMPERATURE=1, FAN_MODE=2, TURN_ON=4,
                                                  TURN_OFF=8, PRESET_MODE=16),
           'WaterHeaterEntityFeature': SimpleNamespace(TARGET_TEMPERATURE=1),
           'HVACMode': SimpleNamespace(AUTO='auto', HEAT='heat', COOL='cool', OFF='off')}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), env)
    cls = env[class_name]
    entity = object.__new__(cls)
    reading = SimpleNamespace(valid=True, value=21)
    entity.coordinator = SimpleNamespace(data={'thermostat': 22, 'T11': 51, 'T12': 47},
                                        t15_reading=lambda: reading,
                                        menu=SimpleNamespace(values={'target': 52}))
    entity.probe = 'T12'
    expected = (21, 22) if module == 'climate' else (47 if class_name == 'CTS600SeparateHeater' else 51, 52)
    assert (entity.native_current_temperature, entity.native_target_temperature) == expected
    old_names = ('_attr_temperature_unit', 'current_temperature', 'target_temperature')
    if native_api:
        assert not any(name in cls.__dict__ for name in old_names)
        assert entity.native_temperature_unit == '°C'
    else:
        assert (entity.current_temperature, entity.target_temperature) == expected
        assert entity.temperature_unit == '°C'
    if module == 'climate':
        reading.valid = False
        assert entity.native_current_temperature is None
    else:
        entity.coordinator.data = {}
        entity.coordinator.menu.values['target'] = 'OFF'
        assert entity.native_current_temperature is None and entity.native_target_temperature is None
