"""Durable last-night result, independent of tomorrow's pending price plan."""
import asyncio
from copy import deepcopy
from test_tuv_night import Device,context,night

def test_reached_result_survives_restart_and_waiting_for_new_prices():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at']
  await c.step(ctx,now);ctx.update(actual_target=55,bottom=55,sample_at=now+60)
  await c.step(ctx,now+60)
  result=c.last_result();assert result['outcome']=='target_reached'
  assert result['start_t12_c']==49 and result['end_t12_c']==55 and result['finished_at']==now+60
  assert result['target_c']==55 and result['original_target_c']==50
  recovered=night.NightControl(d.save,d.send);recovered.restore(c.dump())
  await recovered.step({'enabled':True,'plan':None,'error':'waiting_for_complete_prices'},now+20000)
  assert recovered.status=='waiting' and recovered.last_result()==result
  assert d.writes==[(50,55,False),(55,50,True)]
 asyncio.run(run())

def test_time_limit_preserves_54_as_incomplete_instead_of_claiming_55():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at']
  await c.step(ctx,now);ctx.update(actual_target=55,bottom=54,sample_at=ctx['plan']['end'])
  await c.step(ctx,ctx['sample_at']);r=c.last_result()
  assert r['outcome']=='time_limit' and r['end_t12_c']==54 and r['target_c']==55
 asyncio.run(run())

def test_uncertain_commit_keeps_first_error_and_restores_without_reboost():
 async def run():
  d=Device();d.fail_write=True;c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at']
  await c.step(ctx,now)
  assert c.dump()['lease']['failure_reason']=='Lost readback after write'
  state=deepcopy(c.dump());recovered=night.NightControl(d.save,d.send);recovered.restore(state)
  d.fail_write=False;d.denied=True;await recovered.step(ctx,now+15)
  assert recovered.dump()['lease']['failure_reason']=='Lost readback after write'
  d.denied=False;ctx.update(actual_target=55,sample_at=now+30)
  await recovered.step(ctx,now+30)
  r=recovered.last_result();assert r['outcome']=='recovered_after_restart_or_error'
  assert r['error']=='Lost readback after write' and r['finished_at']==now+30
  assert recovered.lease is None and d.target==50
  assert d.writes==[(50,55,False),(55,50,True)]
 asyncio.run(run())

def test_legacy_journal_retains_outcome_and_window_without_inventing_measurements():
 d=Device();c=night.NightControl(d.save,d.send)
 c.restore({'lease':None,'consumed_until':1794024000.,'outcome':'recovered_after_restart_or_error'})
 assert c.last_result()=={'window_end':1794024000.,'outcome':'recovered_after_restart_or_error'}
 assert c.consumed_until==1794024000. and c.lease is None

def test_new_attempt_does_not_erase_the_previous_legacy_result():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send)
  c.restore({'lease':None,'consumed_until':1000.,'outcome':'time_limit'})
  previous=c.last_result();ctx=context();await c.step(ctx,ctx['sample_at'])
  assert c.status=='heating' and c.last_result()==previous and c.outcome is None
 asyncio.run(run())

def test_stale_temperature_does_not_become_end_measurement():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at']
  await c.step(ctx,now);ctx['actual_target']=55
  await c.step(ctx,ctx['plan']['end'])
  assert c.last_result()['end_t12_c'] is None
 asyncio.run(run())

def test_manual_target_and_result_are_preserved():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at']
  await c.step(ctx,now);d.target=53;ctx.update(actual_target=53,sample_at=now+30)
  await c.step(ctx,now+30)
  assert d.target==53 and len(d.writes)==1
  assert c.last_result()['outcome']=='manual_override_or_already_restored'
 asyncio.run(run())

def test_read_failure_before_write_is_recorded_without_sending():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send);ctx=context();ctx.update(valid=False,error='Fresh target read failed')
  await c.step(ctx,ctx['sample_at'])
  assert not d.writes and c.last_result()['error']=='Fresh target read failed'
  assert c.last_result()['end_t12_c'] is None
 asyncio.run(run())

def test_bad_optional_history_cannot_block_owned_target_recovery():
 async def run():
  d=Device();c=night.NightControl(d.save,d.send);ctx=context();now=ctx['sample_at'];await c.step(ctx,now)
  dump=c.dump();dump['last_run']=['bad'];recovered=night.NightControl(d.save,d.send);recovered.restore(dump)
  await recovered.step({'enabled':False},now+30)
  assert d.target==50 and recovered.lease is None
 asyncio.run(run())
