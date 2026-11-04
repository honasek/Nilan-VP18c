"""Contiguous-price planning and durable DHW boost ownership; no HA or I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, time
import math

from .tuv_shadow import wall_timestamp

SLOT = 900
MAX_START_DELAY = 90


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def clock(value):
    if not isinstance(value, str) or len(value) != 5:
        raise ValueError('Time must use HH:MM')
    parsed = time.fromisoformat(value)
    if parsed.second or parsed.minute % 15:
        raise ValueError('Window boundaries must align to 15 minutes')
    return value


def plan(prices, now, start, end, minutes, zone):
    """Compare equal real durations, require every price in the entire window."""
    clock(start); clock(end)
    if start == end or not finite(minutes) or minutes % 15 or not 15 <= minutes <= 1440:
        raise ValueError('Invalid window or block duration')
    today = datetime.fromtimestamp(now, zone).date()
    windows = []
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        first = wall_timestamp(day, start, zone)
        last = wall_timestamp(day + timedelta(days=end <= start), end, zone)
        if first < last and last > now:
            windows.append((first, last))
    first, last = min(windows, key=lambda row: max(now, row[0]))
    duration = int(minutes * 60)
    if duration > last - first:
        raise ValueError('Block is longer than this window')
    # Attribute timestamps must carry an offset. A fold has two distinct epochs.
    curve = {}
    for key, value in prices.items():
        try:
            parsed = datetime.fromisoformat(key)
            if parsed.tzinfo is None or not finite(value):
                continue
            at = parsed.timestamp()
        except (TypeError, ValueError):
            continue
        if at in curve and curve[at] != value:
            return None, 'conflicting_prices'
        curve[at] = value
    slots = list(range(int(first), int(last), SLOT))
    if not slots or last - first != len(slots) * SLOT or any(at not in curve for at in slots):
        return None, 'waiting_for_complete_prices'
    count = duration // SLOT
    choices = [(sum(curve[at] for at in slots[i:i+count]) / count, -slots[i])
               for i in range(len(slots) - count + 1)]
    average, negative = min(choices)
    begins = -negative
    return {'window_start': first, 'window_end': last, 'start': begins,
            'end': begins + duration, 'average_price': average}, None


class NightControl:
    """Save before sending; never replay an uncertain boost or overwrite a manual edit."""
    def __init__(self, save, send):
        self.save, self.send = save, send
        self.lease = None
        self.consumed_until = 0
        self.recovering = False
        self.status = 'disabled'
        self.reason = 'disabled'
        self.outcome = None

    def dump(self):
        return {'lease': deepcopy(self.lease), 'consumed_until': self.consumed_until,
                'outcome': self.outcome}

    def restore(self, data):
        if not isinstance(data, dict) or not finite(data.get('consumed_until')) or data['consumed_until'] < 0:
            raise ValueError('Invalid night heating journal')
        lease = data.get('lease')
        if lease is not None:
            keys = ('target', 'applied', 'start', 'end', 'window_start', 'window_end', 'sample_at')
            if (not isinstance(lease, dict) or not all(finite(lease.get(key)) for key in keys)
                    or type(lease['target']) is not int or type(lease['applied']) is not int
                    or not 5 <= lease['target'] < lease['applied'] <= 60
                    or not 0 < lease['window_start'] <= lease['start'] < lease['end'] <= lease['window_end']
                    or lease['window_end'] - lease['window_start'] > 25 * 3600
                    or not 0 < lease['sample_at'] <= lease['end']
                    or type(lease.get('cancelled_before_write', False)) is not bool
                    or data['consumed_until'] < lease['window_end']):
                raise ValueError('Unsafe night heating journal')
        self.lease = deepcopy(lease)
        self.consumed_until = data['consumed_until']
        self.outcome = data.get('outcome') if isinstance(data.get('outcome'), str) else None
        self.recovering = bool(lease)

    def reference_target(self, actual):
        if self.lease and actual in (self.lease['applied'], self.lease['target']):
            return self.lease['target']
        return actual

    async def release(self, reason):
        lease = self.lease
        matched = await self.send(lease['applied'], lease['target'], True)
        self.lease = None
        self.outcome = reason if matched else 'manual_override_or_already_restored'
        try:
            await self.save(self.dump())
        except Exception:
            self.lease = lease
            raise
        self.recovering = False
        self.status, self.reason = 'finished', self.outcome

    async def abandon(self, reason):
        """A rejected compare never acquired the target, so it cannot restore it."""
        lease = self.lease
        lease['cancelled_before_write'] = True
        self.lease = None
        self.outcome = reason
        try:
            await self.save(self.dump())
        except Exception:
            self.lease = lease
            raise
        self.recovering = False
        self.status, self.reason = 'finished', reason

    async def step(self, context, now):
        try:
            if self.lease:
                if self.lease.get('cancelled_before_write'):
                    await self.abandon('target_changed_before_start')
                    return
                reason = None
                if self.recovering: reason = 'recovered_after_restart_or_error'
                elif not context.get('enabled'): reason = 'cancelled'
                elif now >= self.lease['end']: reason = 'time_limit'
                elif not context.get('permitted') or not context.get('valid'): reason = 'control_or_data_unavailable'
                elif context.get('actual_target') != self.lease['applied']: reason = 'manual_override'
                elif (context.get('settings_key') != self.lease.get('settings_key')): reason = 'settings_changed'
                elif (finite(context.get('sample_at')) and context['sample_at'] > self.lease['sample_at']
                      and finite(context.get('bottom')) and context['bottom'] >= self.lease['applied']):
                    reason = 'target_reached'
                if reason:
                    self.status, self.reason = 'restore_pending', reason
                    await self.release(reason)
                else:
                    self.status, self.reason = 'heating', 'boost_target_active'
                return
            if not context.get('enabled'):
                self.status, self.reason = 'disabled', 'disabled'
                return
            selected = context.get('plan')
            if not selected:
                self.status, self.reason = 'waiting', context.get('error') or 'waiting_for_plan'
                return
            if selected['window_end'] <= self.consumed_until:
                self.status, self.reason = 'finished', self.outcome or 'window_already_used'
                return
            if now < selected['start']:
                self.status, self.reason = 'scheduled', 'waiting_for_block'
                return
            self.consumed_until = selected['window_end']
            original, target = context.get('actual_target'), context.get('target')
            reason = None
            if now - selected['start'] > MAX_START_DELAY: reason = 'missed_start'
            elif not context.get('permitted') or not context.get('valid'): reason = 'control_or_data_unavailable'
            elif type(original) is not int or type(target) is not int or not 5 <= original < target <= 60:
                reason = 'target_not_higher_than_normal'
            elif not finite(context.get('bottom')) or context['bottom'] >= target: reason = 'already_reached'
            if reason:
                self.outcome = reason
                await self.save(self.dump())
                self.status, self.reason = 'finished', reason
                return
            self.lease = {key: selected[key] for key in ('start', 'end', 'window_start', 'window_end')}
            self.lease.update(target=original, applied=target, sample_at=context['sample_at'],
                              settings_key=context.get('settings_key'))
            self.outcome = None
            try:
                await self.save(self.dump())
            except Exception:
                self.lease = None
                raise
            if not await self.send(original, target, False):
                await self.abandon('target_changed_before_start')
                return
            self.status, self.reason = 'heating', 'boost_target_active'
        except Exception as exc:
            self.recovering = bool(self.lease)
            self.status = 'restore_pending' if self.lease else 'error'
            self.reason = str(exc)
