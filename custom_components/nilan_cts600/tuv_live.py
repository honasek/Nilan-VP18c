"""Opt-in DHW postponement policy and durable setpoint ownership, without HA I/O."""
from __future__ import annotations

from datetime import datetime
import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def epoch(value):
    try:
        result = datetime.fromisoformat(value)
        return result.timestamp() if result.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def candidate(context, now):
    """Only a learned, fresh full-delay scenario may acquire a setpoint lease."""
    plan = context.get('plan') or {}
    if not context.get('permitted') or not context.get('valid'):
        return None, 'controls_or_telemetry_unavailable'
    if plan.get('status') != 'would_delay' or plan.get('selected_variant') != 'full_delay':
        return None, 'no_eligible_full_delay'
    if plan.get('learning', {}).get('learning_ready') is not True:
        return None, 'learning_not_ready'
    issued = plan.get('issued_epoch')
    sample = plan.get('initial_sample') or {}
    if not number(issued) or not -5 <= now-issued <= 120:
        return None, 'stale_plan'
    if not number(context.get('sample_at')) or not -5 <= now-context['sample_at'] <= 180:
        return None, 'stale_telemetry'
    if not number(sample.get('at')) or not -5 <= now-sample['at'] <= 180:
        return None, 'stale_model_sample'
    if sample.get('auxiliary') is not False or sample.get('mode') not in ('HOTWATER', 'HEATING', 'COOLING'):
        return None, 'unsupported_operating_state'
    settings = plan.get('settings') or {}
    reserve = settings.get('reserve_pct')
    top = settings.get('top_min_c')
    if not number(reserve) or not 5 <= reserve <= 90 or not number(top) or not 30 <= top <= 60:
        return None, 'invalid_reserve_settings'
    if (not number(context.get('lower')) or context['lower'] < reserve+5
            or not number(context.get('top')) or context['top'] < top+2
            or not number(context.get('bottom')) or context['bottom'] < 10):
        return None, 'insufficient_actual_reserve'
    target = context.get('target')
    if type(target) is not int or not 10 <= target <= 60 or plan.get('target_c') != target:
        return None, 'target_changed_or_unsupported'
    start = epoch(plan.get('latest_start'))
    deadline = epoch(plan.get('deadline'))
    window = epoch(plan.get('window_start'))
    if (start is None or deadline is None or window is None
            or not now+60 < start < deadline <= now+28*3600
            or not window <= start or deadline-window > 12*3600):
        return None, 'invalid_deadline'
    rows = [r for r in plan.get('scenarios', [])
            if r.get('variant') == 'full_delay' and epoch(r.get('start_at')) == start]
    if not any(r.get('reserve_met') is True and r.get('deadline_met') is True
               and r.get('cancelled_paths') == 0 for r in rows):
        return None, 'scenario_not_verified'
    return {'target': target, 'applied': 5, 'release_at': start, 'deadline': deadline,
            'reserve_pct': reserve, 'top_min_c': top}, 'eligible'


class LiveControl:
    """A journal is saved before a write; restoration uses compare-and-set."""

    def __init__(self, save, send):
        self.save = save
        self.send = send
        self.enabled = False  # Never arm automatically after a restart.
        self.lease = None
        self.blocked_until = 0
        self.status = 'disabled'
        self.reason = 'Live control is off'

    def restore(self, data):
        if not isinstance(data, dict):
            raise ValueError('Invalid live-control journal')
        blocked = data.get('blocked_until', 0)
        if not number(blocked) or blocked < 0:
            raise ValueError('Invalid live-control cooldown')
        lease = data.get('lease')
        if lease is not None:
            keys = ('target', 'applied', 'release_at', 'deadline', 'reserve_pct', 'top_min_c')
            if not isinstance(lease, dict) or not all(number(lease.get(k)) for k in keys):
                raise ValueError('Invalid live-control lease')
            if (type(lease['target']) is not int or not 10 <= lease['target'] <= 60
                    or lease['applied'] != 5 or not 0 < lease['release_at'] < lease['deadline']
                    or not 5 <= lease['reserve_pct'] <= 90 or not 30 <= lease['top_min_c'] <= 60):
                raise ValueError('Unsafe live-control lease')
            lease = {k: lease[k] for k in keys}
        self.lease = lease
        self.blocked_until = blocked

    def dump(self):
        return {'lease': self.lease, 'blocked_until': self.blocked_until}

    def forecast_target(self, current):
        if self.lease and current in (self.lease['applied'], self.lease['target']):
            return self.lease['target']
        return current

    async def _release(self, reason):
        lease = self.lease
        # False means a fresh physical read found a different target: manual wins.
        matched = await self.send(lease['applied'], lease['target'], True)
        self.lease = None
        self.blocked_until = max(self.blocked_until, lease['deadline'])
        try:
            await self.save(self.dump())
        except Exception:
            self.lease = lease  # Retain recovery information until durably cleared.
            raise
        self.status = 'restored' if matched else 'manual_override'
        self.reason = reason if matched else 'Target changed outside live control; left unchanged'

    async def step(self, context, now):
        try:
            if self.lease:
                lease = self.lease
                proposal, reason = candidate(context, now)
                unsafe = (not self.enabled or proposal is None or now >= lease['release_at']
                          or proposal['deadline'] != lease['deadline']
                          or proposal['release_at'] < lease['release_at']
                          or context.get('target') != lease['target']
                          or context.get('actual_target') != lease['applied']
                          or context.get('lower', -1) < lease['reserve_pct']
                          or context.get('top', -1) < lease['top_min_c'])
                if unsafe:
                    self.status = 'restore_pending'
                    await self._release('Live control stopped or reserve/plan requires normal heating: '+reason)
                else:
                    self.status = 'delaying'
                    self.reason = 'Temporary compressor target 5 °C; original target retained'
                return
            if not self.enabled:
                self.status = 'disabled'
                self.reason = 'Live control is off'
                return
            proposal, reason = candidate(context, now)
            if proposal is None or now < self.blocked_until:
                self.status = 'waiting'
                self.reason = 'Completed/cancelled window cannot be postponed again' if now < self.blocked_until else reason
                return
            self.lease = proposal
            # Claim and recovery target must be durable before touching hardware.
            try:
                await self.save(self.dump())
            except Exception:
                self.lease = None
                raise
            if not await self.send(proposal['target'], proposal['applied'], False):
                await self._release('Target changed before command')
                return
            self.status = 'delaying'
            self.reason = 'Compressor target temporarily lowered to 5 °C'
        except Exception as exc:
            self.enabled = False
            self.status = 'restore_pending' if self.lease else 'error'
            self.reason = str(exc)
