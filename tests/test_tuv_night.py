"""Device-free regressions for prices, DST, target ownership and crash recovery."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

import pytest

component = Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600'
package = types.ModuleType('night_tests'); package.__path__ = [str(component)]
sys.modules[package.__name__] = package
night = importlib.import_module(package.__name__ + '.tuv_night')
shadow = importlib.import_module(package.__name__ + '.tuv_shadow')
config = importlib.import_module(package.__name__ + '.entity_config')
TZ = ZoneInfo('Europe/Prague')


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp()


def curve(first, end, values=None):
    return {datetime.fromtimestamp(stamp, timezone.utc).isoformat():
            values[i] if values is not None else 1.0
            for i, stamp in enumerate(range(int(first), int(end), 900))}


@pytest.mark.parametrize('day,hours', [('2026-03-29', 3), ('2026-10-25', 5), ('2026-11-04', 4)])
def test_real_duration_across_both_dst_transitions(day, hours):
    first, end = at(day+'T01:00'), at(day+'T05:00')
    assert (end-first)/3600 == hours
    prices = curve(first, end)
    result, error = night.plan(prices, first-3600, '01:00', '05:00', 180, TZ)
    assert error is None and result['end']-result['start'] == 3*3600
    assert result['end'] == end  # Later block wins an equal-price tie.


def test_contiguous_block_not_individual_cheapest_slots():
    first, end = at('2026-11-04T01:00'), at('2026-11-04T03:00')
    prices = curve(first, end, [-10, 20, -10, 20, 2, 2, 2, 2])
    result, _ = night.plan(prices, first-3600, '01:00', '03:00', 60, TZ)
    assert result['start'] == first+3600 and result['average_price'] == 2


def test_whole_window_required_including_missing_outside_candidate():
    first, end = at('2026-11-04T01:00'), at('2026-11-04T05:00')
    prices = curve(first, end)
    del prices[next(iter(prices))]
    assert night.plan(prices, first-3600, '01:00', '05:00', 60, TZ) == (None, 'waiting_for_complete_prices')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, '0', None])
def test_invalid_price_never_becomes_zero(bad):
    first, end = at('2026-11-04T01:00'), at('2026-11-04T05:00')
    prices = curve(first, end); prices[next(iter(prices))] = bad
    assert night.plan(prices, first-3600, '01:00', '05:00', 60, TZ)[0] is None


def test_midnight_window_and_negative_prices():
    first, end = at('2026-11-04T22:00'), at('2026-11-05T06:00')
    prices = curve(first, end, [1]*8+[-2]*12+[3]*12)
    selected, _ = night.plan(prices, first-3600, '22:00', '06:00', 180, TZ)
    assert selected['start'] == first+2*3600 and selected['average_price'] == -2


def test_conflicting_offset_aliases_do_not_authorize_a_plan():
    first,end=at('2026-11-04T01:00'),at('2026-11-04T05:00')
    prices=curve(first,end)
    prices['2026-11-04T01:00:00+01:00']=99
    assert night.plan(prices,first-3600,'01:00','05:00',60,TZ)==(None,'conflicting_prices')


@pytest.mark.parametrize('start,end,duration', [('01:00','01:00',60),('01:01','05:00',60),
    ('01:00','05:00',61),('01:00','05:00',300),('25:00','05:00',60)])
def test_invalid_settings(start,end,duration):
    with pytest.raises(ValueError):
        night.plan({},at('2026-11-04T00:00'),start,end,duration,TZ)


def context(now=1800000000):
    return {'enabled':True,'permitted':True,'valid':True,'actual_target':50,'target':55,
            'bottom':49,'sample_at':now,'settings_key':[55,180,'01:00','05:00'],
            'plan':{'start':now,'end':now+10800,'window_start':now-1800,'window_end':now+12600}}


class Device:
    def __init__(self):
        self.target=50; self.records=[]; self.writes=[]
        self.fail_save=False; self.fail_write=False; self.denied=False
    async def save(self,data):
        if self.fail_save: raise OSError('Cannot persist')
        self.records.append(deepcopy(data))
    async def send(self,expected,target,restoring):
        if self.denied: raise OSError('Offline')
        if expected!=self.target:return False
        assert self.records[-1]['lease'] is not None
        self.target=target; self.writes.append((expected,target,restoring))
        if self.fail_write:raise OSError('Lost readback after write')
        return True


def test_completes_on_new_t12_sample_restores_original_once():
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at']
        await c.step(ctx,now)
        assert d.target==55 and c.reference_target(55)==50
        ctx.update(actual_target=55,bottom=55)
        await c.step(ctx,now+1)
        assert d.target==55  # A stale temperature cannot establish completion.
        ctx['sample_at']=now+60
        await c.step(ctx,now+60)
        assert d.target==50 and c.outcome=='target_reached' and c.lease is None
        await c.step(context(),now+70)
        assert len(d.writes)==2
    asyncio.run(run())


@pytest.mark.parametrize('change,reason', [({'enabled':False},'cancelled'),
    ({'valid':False},'control_or_data_unavailable'),({'permitted':False},'control_or_data_unavailable'),
    ({'settings_key':[56]},'settings_changed')])
def test_cancel_restores_and_consumes_window(change,reason):
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context()
        await c.step(ctx,ctx['sample_at']);ctx.update(actual_target=55,**change)
        await c.step(ctx,ctx['sample_at']+60)
        assert d.target==50 and c.outcome==reason
    asyncio.run(run())


def test_deadline_restore_does_not_claim_target_reached():
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context()
        await c.step(ctx,ctx['sample_at']);ctx['actual_target']=55
        await c.step(ctx,ctx['plan']['end'])
        assert d.target==50 and c.outcome=='time_limit'
    asyncio.run(run())


def test_manual_target_is_not_overwritten():
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context()
        await c.step(ctx,ctx['sample_at']);d.target=53;ctx['actual_target']=53
        await c.step(ctx,ctx['sample_at']+30)
        assert d.target==53 and len(d.writes)==1 and c.lease is None
    asyncio.run(run())


def test_rejected_acquisition_never_restores_a_manually_selected_boost_target():
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context()
        # A manual change after the snapshot but before the physical compare.
        d.target=55
        await c.step(ctx,ctx['sample_at'])
        assert d.target==55 and not d.writes and c.lease is None
        assert c.outcome=='target_changed_before_start'
    asyncio.run(run())


def test_rejected_acquisition_cleanup_retries_without_touching_manual_target():
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context()
        original_send=d.send
        async def changed_before_compare(expected,target,restoring):
            d.target=55
            d.fail_save=True
            return await original_send(expected,target,restoring)
        c.send=changed_before_compare
        await c.step(ctx,ctx['sample_at'])
        assert c.lease['cancelled_before_write'] and not d.writes
        d.fail_save=False
        await c.step(ctx,ctx['sample_at']+15)
        assert c.lease is None and d.target==55 and not d.writes
    asyncio.run(run())


def test_restart_releases_even_with_helper_off_then_never_restarts_window():
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context()
        await c.step(ctx,ctx['sample_at'])
        recovered=night.NightControl(d.save,d.send);recovered.restore(c.dump())
        await recovered.step({'enabled':False},ctx['sample_at']+30)
        assert d.target==50 and recovered.lease is None
        await recovered.step(context(),ctx['sample_at']+60)
        assert len(d.writes)==2
    asyncio.run(run())


def test_uncertain_write_never_replays_boost_and_retains_restore_until_online():
    async def run():
        d=Device();d.fail_write=True;c=night.NightControl(d.save,d.send);ctx=context()
        await c.step(ctx,ctx['sample_at'])
        assert c.status=='restore_pending' and c.lease and d.target==55
        d.fail_write=False;d.denied=True
        await c.step(ctx,ctx['sample_at']+15)
        assert c.lease and len(d.writes)==1
        d.denied=False
        await c.step(ctx,ctx['sample_at']+30)
        assert d.target==50 and c.lease is None and len(d.writes)==2
    asyncio.run(run())


def test_journal_failure_prevents_boost():
    async def run():
        d=Device();d.fail_save=True;c=night.NightControl(d.save,d.send)
        await c.step(context(),1800000000)
        assert not d.writes and c.lease is None
    asyncio.run(run())


@pytest.mark.parametrize('update,late', [({'enabled':False},0),({'permitted':False},0),
    ({'target':50},0),({'target':61},0),({'bottom':55},0),({},91)])
def test_no_unwanted_or_late_start(update,late):
    async def run():
        d=Device();c=night.NightControl(d.save,d.send);ctx=context();ctx.update(update)
        await c.step(ctx,1800000000+late)
        assert not d.writes and c.lease is None
    asyncio.run(run())


def test_validate_with_resolved_zone_never_loads_database(monkeypatch):
    monkeypatch.setattr(shadow,'ZoneInfo',lambda _:pytest.fail('ZoneInfo constructed on event loop'))
    assert shadow.Settings().validate(TZ).zone is TZ


def test_legacy_mapping_is_independent_of_helper_startup_order_and_preserves_removal():
    options=config.legacy_options({}, {'power_entity':None})
    assert options['power_entity'] is None
    assert options['shadow_enabled_entity']=='input_boolean.nilan_tuv_shadow_enabled'
    assert options['automatic_control_entity']=='input_boolean.nilan_automatic_control_enabled'
    assert config.legacy_options({'power_entity':'sensor.own_power'},{}).get('power_entity') is None


def test_new_install_inputs_have_no_implicit_household_bindings():
    assert config.selected({},'power_entity') is None
    assert config.selected({},'shadow_enabled_entity') is None
