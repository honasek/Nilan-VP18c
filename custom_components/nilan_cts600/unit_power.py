"""Panel-equivalent ON/OFF, with fresh readback and no uncertain key replay."""
import re
from .nilan_cts600 import Key


def power_state(mode):
    if mode == 'OFF':
        return False
    if mode in ('AUTO', 'HEAT', 'COOL'):
        return True
    return None


def _read_mode(panel, expected=None):
    for _ in range(3):
        screen = panel.key(Key.NONE).strip()
        # Read the actual main screen, never an edit field or cached data.
        match = re.match(r'^(AUTO|HEAT|COOL|OFF)(?=\s|/|$)', screen)
        if match and screen.count('/') == 1 and '{' not in screen and '}' not in screen:
            mode = match.group(1)
            if mode == 'OFF' or re.search(r'/>[1-4]<\s+\d{1,2}°C\s*$', screen):
                if expected is None or power_state(mode) is expected:
                    return mode
    raise ValueError('CTS600 power state was not confirmed by a fresh main-screen readback')


def set_power(panel, enabled):
    """Resume the controller's mode with ON; do not replace it with AUTO."""
    if type(enabled) is not bool:
        raise ValueError('CTS600 power target must be a boolean')
    panel.resetMenu()
    before = _read_mode(panel)
    if power_state(before) is not enabled:
        # A protocol-level keypress is sent once. Only no-key reads may repeat.
        panel.key(Key.ON if enabled else Key.OFF)
        _read_mode(panel, enabled)
    # Do not let a failed scan confirm a request using a retained mode value.
    panel.data.pop('mode', None)
    panel.updateData(False)
    if power_state(panel.data.get('mode')) is not enabled:
        raise ValueError('CTS600 power state changed or was absent in the final display readback')
    return panel.data.copy()
