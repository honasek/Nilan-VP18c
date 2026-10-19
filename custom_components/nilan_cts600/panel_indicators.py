"""Only infer operating flags while the verified main display is visible."""
import re

def indicators(data):
    display=data.get('display','').splitlines()
    top=re.fullmatch(r'(?:AUTO|HEAT|COOL|OFF)\s*(.*)',display[0]) if display else None
    flags=top[1] if top else None
    led=data.get('led')
    return {'compressor_running':led=='on' if led in ('on','off') else None,
            'dhw_aux_running':'W' in flags if flags is not None else None,
            'user_choice_active':'*' in flags if flags is not None else None,
            'winter_low_active':'L' in flags if flags is not None else None,
            'weekly_program_active':bool(re.search('[123]',flags)) if flags is not None else None}
