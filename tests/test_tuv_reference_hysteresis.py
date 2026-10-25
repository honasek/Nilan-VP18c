"""Device-free regression tests for dynamic stock and observed thermostat state."""
import copy,importlib,json,os,sys,types
from pathlib import Path
from dataclasses import replace
import pytest

COMPONENT=Path(os.environ.get('NILAN_COMPONENT_PATH',Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600'))
pkg=types.ModuleType('reference_hysteresis_test');pkg.__path__=[str(COMPONENT)];sys.modules[pkg.__name__]=pkg
m=importlib.import_module(pkg.__name__+'.tuv_model')
h=importlib.import_module(pkg.__name__+'.tuv_thermostat')
eta=importlib.import_module(pkg.__name__+'.tuv_eta')
shadow=importlib.import_module(pkg.__name__+'.tuv_shadow')
NOW=1792950000.

def sample(at=NOW,bottom=50,top=55,on=False,mode='HEATING',aux=False):
    return m.Sample(at,top,bottom,on,aux,mode,28 if not on else 500)

def calibrated():
    obj=h.ThermostatObserver()
    for i in range(3):
        start=NOW+i*600
        for s in (sample(start,bottom=46),sample(start+60,bottom=46,on=True,mode='HOTWATER'),
                  sample(start+120,bottom=50,on=True,mode='HOTWATER'),sample(start+180,bottom=50)):
            obj.observe(s,50)
    return obj

def tank(s):
    obj=m.ThermalObserver();obj.last=s;obj.phase='idle'
    obj.particles=[m.Particle([s.bottom]*6,[2.]*3,0.,0.,10.,.5)]
    return obj

@pytest.mark.parametrize('target,temp,value',[(52,52,100),(52,51,100*21/22),(50,40,50),(60,40,100/3)])
def test_stock_uses_compressor_reference(target,temp,value):
    assert m.soc([temp]*6,temp,target)==pytest.approx(value)

@pytest.mark.parametrize('target',[None,30,29,True,float('nan')])
def test_missing_or_low_reference_is_unavailable_not_zero_or_60(target):
    obj=tank(sample());obj.particles*=m.COUNT;result=obj.snapshot(target)
    assert result['soc'] is None and result['lower'] is None and result['full_c'] is None
    assert not result['stock_reference_valid']

def test_zero_anchor_and_projection_do_not_change_physical_learning():
    assert m.soc([20]*6,30,52)==0
    obj=tank(sample(bottom=51,top=54));obj.particles*=m.COUNT;before=copy.deepcopy(obj.dump())
    assert obj.snapshot(52)['soc']>obj.snapshot(60)['soc']
    assert obj.snapshot(52)['layers_bottom_to_top']==obj.snapshot(60)['layers_bottom_to_top']
    assert obj.dump()==before

def test_repeated_clean_starts_and_stops_measure_four_degree_band():
    obj=calibrated();v=obj.snapshot(50)
    assert v['status']=='measured' and v['restart_drop_c']==4 and v['restart_c']==46
    assert v['start_samples']==v['stop_samples']==3
    assert v['restart_range_c']==[45,47]

def test_new_target_transfer_is_explicit_and_counts_direct_measurements():
    obj=calibrated();obj.observe(sample(NOW+2000,bottom=52,on=True,mode='HOTWATER'),52)
    obj.observe(sample(NOW+2060,bottom=52),52)
    v=obj.snapshot(52)
    assert v['status']=='transferred' and v['source_target_c']==50
    assert v['restart_c']==48 and v['current_target_starts']==0 and v['current_target_stops']==1
    assert v['observed_stop_c']==52 and not v['exact_firmware_setting']

def test_one_degree_cooling_after_confirmed_completion_is_reached():
    obj=calibrated()
    for s in (sample(NOW+2000,bottom=52,on=True,mode='HOTWATER'),sample(NOW+2060,bottom=52),sample(NOW+2120,bottom=51)):
        obj.observe(s,52)
    model=tank(s);result=eta.forecast(model,52,thermostat=obj.snapshot(52))
    assert result['minimum_heat_remaining']['status']=='reached_hysteresis'
    assert result['minimum_heat_remaining']['minutes']==0
    assert result['full_heat_remaining']['status']=='reached'

def test_an_off_compressor_without_observed_completion_is_not_reached():
    obj=calibrated();s=sample(NOW+2000,bottom=51);obj.observe(s,52)
    assert not obj.snapshot(52)['reached_probes']['T12']
    assert eta.forecast(tank(s),52,thermostat=obj.snapshot(52))['minimum_heat_remaining']['status']=='heating_off'

def test_new_heating_request_and_temperature_below_band_reopen_cycle():
    obj=calibrated();s=sample(NOW+2000,bottom=50);obj.observe(s,50)
    s=sample(NOW+2060,bottom=46,on=True,mode='HOTWATER');obj.observe(s,50)
    assert not obj.snapshot(50)['reached_probes']['T12']
    assert eta.forecast(tank(s),50,thermostat=obj.snapshot(50))['minimum_heat_remaining']['status']=='forecast'
    s=sample(NOW+2120,bottom=43);obj.observe(s,50)
    assert eta.forecast(tank(s),50,thermostat=obj.snapshot(50))['minimum_heat_remaining']['status']=='heating_off'

def test_context_change_manual_stop_and_auxiliary_do_not_claim_hysteresis():
    for s in (sample(NOW+2000,bottom=49,mode='OFF'),sample(NOW+2000,bottom=49,aux=True)):
        obj=calibrated();obj.observe(s,50)
        assert not obj.snapshot(50)['reached_probes']['T12']
    obj=calibrated();s=sample(NOW+2000,bottom=49);obj.observe(s,50)
    assert eta.forecast(tank(s),50,context_changed=True,thermostat=obj.snapshot(50))['minimum_heat_remaining']['status']=='context_changed'

def test_rapid_draw_target_change_gap_and_controlled_cycles_do_not_calibrate():
    obj=h.ThermostatObserver();obj.observe(sample(bottom=50),50)
    obj.observe(sample(NOW+60,bottom=30,on=True,mode='HOTWATER'),50)
    assert obj.events[-1]['qualified'] is False
    count=len(obj.events)
    obj.observe(sample(NOW+120,bottom=52),52)  # Changed goal.
    obj.observe(sample(NOW+1000,bottom=48,on=True,mode='HOTWATER'),52)  # Gap.
    obj.observe(sample(NOW+1060,bottom=52),52,inhibited=True)
    assert len(obj.events)==count

def test_duplicate_sample_is_not_independent_evidence():
    obj=calibrated();before=obj.dump();obj.observe(sample(NOW+1380,bottom=50),50)
    assert obj.dump()==before


def test_telemetry_gap_keeps_calibration_but_requires_new_completion():
    obj=calibrated();obj.observe(sample(NOW+2000,bottom=49),50)
    assert obj.snapshot(50)['restart_drop_c']==4
    assert not obj.snapshot(50)['reached_probes']['T12']

def test_restore_preserves_completion_and_detects_corrupt_evidence():
    obj=calibrated();s=sample(NOW+2000,bottom=49);obj.observe(s,50)
    restored=h.ThermostatObserver.restore(json.loads(json.dumps(obj.dump())),s.at+60)
    assert restored.dump()==obj.dump() and restored.snapshot(50)==obj.snapshot(50)
    corrupted=obj.dump();corrupted['events'][0]['at']=s.at+10000
    with pytest.raises(ValueError):h.ThermostatObserver.restore(corrupted,s.at+60)

def test_shadow_stock_and_readiness_share_dynamic_reference_and_hysteresis():
    obj=calibrated()
    for s in (sample(NOW+2000,bottom=52,on=True,mode='HOTWATER'),sample(NOW+2060,bottom=52),sample(NOW+2120,bottom=51)):
        obj.observe(s,52)
    model=tank(s);info=obj.snapshot(52)
    settings=shadow.Settings(prior_daily_l=0,top_min_c=41,reserve_pct=35)
    p=shadow.DemandProfile('expected','test',tuple([0.]*48))
    r=shadow.simulate(model,shadow.ShadowLearner(),settings,52,s.at,s.at+300,s.at+3600,'full_delay',
                      demand_profile=p,horizon=s.at+7200,thermostat=info)
    assert r['stock_reference_c']==52 and r['hysteresis_drop_c']==4
    assert r['deadline_met'] and not r['nominal_target_at_deadline'] and r['target_held_until_deadline']
    assert r['compressor_electric_kwh_model']==0
    assert r['end_stock_pct']==pytest.approx(round(100*21/22,1))
    stress=shadow.simulate(model,shadow.ShadowLearner(),settings,52,s.at,s.at+300,s.at+3600,'full_delay',
                           demand_profile=replace(p,kind='stress'),thermostat=info)
    assert stress['hysteresis_drop_c']==5

def test_unknown_completion_cannot_seed_shadow_readiness_from_off_flag_alone():
    s=sample(bottom=51);model=tank(s)
    p=shadow.DemandProfile('expected','test',tuple([0.]*48))
    r=shadow.simulate(model,shadow.ShadowLearner(),shadow.Settings(prior_daily_l=0),52,s.at,s.at,s.at+3600,
                      'normal',demand_profile=p,thermostat=calibrated().snapshot(52))
    assert not r['deadline_met'] and not r['target_reached_before_deadline']
