"""Pure validation shared by initialization, polling and user commands."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import math


@dataclass(frozen=True)
class T15Reading:
    value: float | None
    age_seconds: float | None
    reason: str

    @property
    def valid(self):
        return self.reason == "ok"


def validate_t15(state, now: datetime, max_age: float = 900) -> T15Reading:
    if state is None:
        return T15Reading(None, None, "missing")
    if state.attributes.get("restored"):
        return T15Reading(None, None, "restored")
    reported = getattr(state, "last_reported", None)
    if reported is None:
        return T15Reading(None, None, "missing_timestamp")
    age = (now - reported).total_seconds()
    if not math.isfinite(age) or age < -5:
        return T15Reading(None, age, "invalid_timestamp")
    if state.state in (None, "unknown", "unavailable"):
        return T15Reading(None, age, "unavailable")
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return T15Reading(None, age, "not_numeric")
    if not math.isfinite(value):
        return T15Reading(None, age, "not_finite")
    unit = state.attributes.get("unit_of_measurement")
    if unit != "°C":
        return T15Reading(None, age, "wrong_unit")
    if not 5 <= value <= 40:
        return T15Reading(None, age, "out_of_range")
    if age > max_age:
        return T15Reading(None, age, "stale")
    return T15Reading(value, max(0, age), "ok")


def select_t15(get_state, primary_id: str, backup_id: str | None,
               now: datetime, max_age: float = 900) -> tuple[str | None, T15Reading]:
    """Prefer a valid primary and revert to it as soon as it recovers."""
    primary = validate_t15(get_state(primary_id), now, max_age)
    if primary.valid:
        return primary_id, primary
    if backup_id and backup_id != primary_id:
        backup = validate_t15(get_state(backup_id), now, max_age)
        if backup.valid:
            return backup_id, backup
    return None, primary
