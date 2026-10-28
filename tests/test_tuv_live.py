"""Live policy/recovery tests with a physical-target fake; no device connection."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600/tuv_live.py'
spec = importlib.util.spec_from_file_location('live_test', path)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
NOW = 1800000000


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def context(now=NOW):
    return {'permitted': True, 'valid': True, 'sample_at': now, 'target': 50,
            'actual_target': 50, 'top': 55, 'bottom': 30, 'lower': 65,
            'plan': {'status': 'would_delay', 'selected_variant': 'full_delay',
                     'learning': {'learning_ready': True}, 'issued_epoch': now,
                     'simulation_version':3,'control_readiness':{'version':1,'ready':True,'reasons':[],'target_c':50,'mode':'HOTWATER','as_of':now,'effective_margin_min':120},
                     'initial_sample': {'at': now, 'auxiliary': False, 'mode': 'HOTWATER'},
                     'settings': {'reserve_pct': 35, 'top_min_c': 45,'margin_min':120}, 'target_c': 50,
                     'latest_start': stamp(NOW+3600), 'deadline': stamp(NOW+7200),
                     'window_start': stamp(NOW+1800), 'scenarios': [
                         {'variant': 'full_delay', 'start_at': stamp(NOW+3600),
                          'reserve_met': True, 'deadline_met': True, 'cancelled_paths': 0,
                          'post_use_comfort_met':True,'expected_profile_passes':True,'stress_profile_passes':True}]}}


class Device:
    def __init__(self):
        self.target = 50
        self.records = []
        self.writes = []
        self.fail_save = False
        self.fail_write = False
        self.denied = False

    async def save(self, data):
        if self.fail_save:
            raise OSError('journal unavailable')
        self.records.append(deepcopy(data))

    async def send(self, expected, value, restoring):
        if self.denied:
            raise RuntimeError('control locked or disconnected')
        if self.target != expected:
            return False
        assert self.records[-1]['lease'] is not None  # Durable before any write.
        self.target = value
        self.writes.append((expected, value, restoring))
        if self.fail_write:
            raise OSError('readback lost after write')
        return True


def run(test):
    asyncio.run(test())


def test_default_off_and_enable_causes_one_real_setpoint_write():
    async def check():
        d = Device(); c = live.LiveControl(d.save, d.send)
        await c.step(context(), NOW)
        assert d.writes == [] and c.status == 'disabled'
        c.enabled = True
        await c.step(context(), NOW)
        assert d.writes == [(50, 5, False)] and c.status == 'delaying'
        ctx = context(); ctx['actual_target'] = 5
        await c.step(ctx, NOW+5)
        assert len(d.writes) == 1
        assert c.forecast_target(5) == 50
    run(check)


@pytest.mark.parametrize('field,value', [('permitted', False), ('valid', False),
    ('sample_at', NOW-181), ('sample_at', NOW+6), ('top', 46), ('lower', 39),
    ('bottom', 9), ('target', True), ('target', 5), ('lower', float('nan'))])
def test_invalid_context_never_changes_device(field, value):
    async def check():
        d = Device(); c = live.LiveControl(d.save, d.send); c.enabled = True
        ctx = context(); ctx[field] = value
        await c.step(ctx, NOW)
        assert not d.writes and c.lease is None
    run(check)


@pytest.mark.parametrize('change', ['disabled_shadow', 'learning', 'stale_plan', 'stale_model',
    'partial', 'no_deadline', 'bad_deadline', 'unverified', 'auxiliary', 'unknown_mode'])
def test_ineligible_plan_does_not_become_a_live_command(change):
    ctx = context(); p = ctx['plan']
    if change == 'disabled_shadow': p['status'] = 'disabled'
    elif change == 'learning': p['learning']['learning_ready'] = False
    elif change == 'stale_plan': p['issued_epoch'] = NOW-121
    elif change == 'stale_model': p['initial_sample']['at'] = NOW-181
    elif change == 'partial': p['selected_variant'] = 'partial'
    elif change == 'no_deadline': p['deadline'] = None
    elif change == 'bad_deadline': p['latest_start'] = stamp(NOW+8000)
    elif change == 'unverified': p['scenarios'][0]['deadline_met'] = False
    elif change == 'auxiliary': p['initial_sample']['auxiliary'] = True
    elif change == 'unknown_mode': p['initial_sample']['mode'] = 'unknown'
    assert live.candidate(ctx, NOW)[0] is None


@pytest.mark.parametrize('cause', ['off', 'reserve', 'top', 'expired', 'stale', 'plan', 'permission', 'shadow_off'])
def test_any_loss_of_permission_or_reserve_restores_original_target(cause):
    async def check():
        d = Device(); c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        ctx = context(); ctx['actual_target'] = 5
        at = NOW+5
        if cause == 'off': c.enabled = False
        elif cause == 'reserve': ctx['lower'] = 34
        elif cause == 'top': ctx['top'] = 44
        elif cause == 'expired': at = NOW+3600
        elif cause == 'stale': ctx['sample_at'] = NOW-200
        elif cause == 'plan': ctx['plan']['status'] = 'would_top_up'
        elif cause == 'permission': ctx['permitted'] = False
        elif cause == 'shadow_off': ctx['plan']['status'] = 'disabled'
        await c.step(ctx, at)
        assert d.target == 50 and c.lease is None
        assert d.writes[-1] == (5, 50, True)
        c.enabled = True
        await c.step(context(), NOW+10)
        assert len(d.writes) == 2  # Never postpone the same cancelled window again.
    run(check)


def test_manual_override_is_never_overwritten_or_readopted():
    async def check():
        d = Device(); c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        d.target = 47
        ctx = context(); ctx.update(target=47, actual_target=47)
        await c.step(ctx, NOW+5)
        assert d.target == 47 and c.lease is None and c.status == 'manual_override'
        assert c.forecast_target(47) == 47
    run(check)


def test_restart_recovers_target_without_rearming():
    async def check():
        d = Device(); c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        restarted = live.LiveControl(d.save, d.send)
        restarted.restore(d.records[-1])
        await restarted.step(context(), NOW+30)
        assert not restarted.enabled and restarted.lease is None and d.target == 50
    run(check)


def test_journal_failure_prevents_any_hardware_write():
    async def check():
        d = Device(); d.fail_save = True
        c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        assert not d.writes and not c.enabled and c.lease is None
    run(check)


def test_uncertain_applied_write_keeps_lease_and_recovers_after_reconnect():
    async def check():
        d = Device(); d.fail_write = True
        c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        assert d.target == 5 and not c.enabled and c.status == 'restore_pending'
        d.fail_write = False
        await c.step(context(), NOW+30)
        assert d.target == 50 and c.lease is None
    run(check)


def test_locked_restoration_stays_pending_until_guard_recovers():
    async def check():
        d = Device(); c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        c.enabled = False; d.denied = True
        await c.step(context(), NOW+5)
        assert c.lease is not None and c.status == 'restore_pending'
        d.denied = False
        await c.step(context(), NOW+30)
        assert d.target == 50 and c.lease is None
    run(check)


def test_target_changed_between_plan_and_write_wins():
    async def check():
        d = Device(); d.target = 48
        c = live.LiveControl(d.save, d.send); c.enabled = True
        await c.step(context(), NOW)
        assert d.target == 48 and not d.writes and c.lease is None
    run(check)


@pytest.mark.parametrize('lease', [{}, {'target': 99}, {'target': 50, 'applied': 0,
    'release_at': NOW+60, 'deadline': NOW+600, 'reserve_pct': 35, 'top_min_c': 45}])
def test_invalid_recovery_journal_is_rejected(lease):
    c = live.LiveControl(None, None)
    with pytest.raises(ValueError):
        c.restore({'lease': lease, 'blocked_until': 0})
