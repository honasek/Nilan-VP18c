"""Optional household inputs, selected explicitly rather than guessed by name."""

SHADOW_FIELDS = {
    'enabled': 'input_boolean', 'day_enabled': 'input_boolean',
    'window_start': 'input_datetime', 'window_end': 'input_datetime',
    'day_start': 'input_datetime', 'day_end': 'input_datetime',
    'reserve_pct': 'input_number', 'top_min_c': 'input_number',
    'prior_daily_l': 'input_number', 'margin_min': 'input_number',
}
NIGHT_FIELDS = {
    'night_enabled_entity': 'input_boolean', 'night_target_entity': 'input_number',
    'night_start_entity': 'input_datetime', 'night_end_entity': 'input_datetime',
    'night_duration_entity': 'input_number', 'price_entity': 'sensor',
}
OPTIONAL_FIELDS = {
    'automatic_control_entity': 'input_boolean', 'service_control_entity': 'input_boolean',
    'power_entity': 'sensor', 't15_max_age_entity': 'input_number',
    'shadow_initialized_entity': 'input_boolean',
    **{f'shadow_{key}_entity': domain for key, domain in SHADOW_FIELDS.items()},
    **NIGHT_FIELDS,
}


def selected(config, key):
    value = config.get(key)
    return value if isinstance(value, str) and value else None


def is_on(hass, config, key):
    entity_id = selected(config, key)
    state = hass.states.get(entity_id) if entity_id else None
    return bool(state and state.state == 'on' and not state.attributes.get('restored'))


def legacy_options(data, options):
    """Preserve v1 implicit bindings, even before helper integrations have loaded.

    Only v1 entries call this migration. New v2 installations have no defaults;
    missing selected legacy inputs retain their previous unavailable behavior.
    """
    result = dict(options)
    legacy = {
        'automatic_control_entity': 'input_boolean.nilan_automatic_control_enabled',
        'service_control_entity': 'input_boolean.nilan_service_control_enabled',
        'power_entity': 'sensor.rekuperace_vykon',
        't15_max_age_entity': 'input_number.nilan_t15_max_age_minutes',
        'shadow_initialized_entity': 'input_boolean.nilan_tuv_shadow_initialized',
        **{f'shadow_{key}_entity': f'{domain}.nilan_tuv_shadow_{"day_window_enabled" if key == "day_enabled" else key}'
           for key, domain in SHADOW_FIELDS.items()},
    }
    for key, entity_id in legacy.items():
        if key not in data and key not in result:
            result[key] = entity_id
    return result
