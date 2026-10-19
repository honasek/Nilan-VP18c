import importlib.util
import json
from pathlib import Path
import sys
import time
import pytest

MODULE=Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600/tuv_model.py'
spec=importlib.util.spec_from_file_location('tuv_model_test',MODULE)
model=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=model
spec.loader.exec_module(model)

def sample(at=1800000000,top=59,bottom=22,compressor=False,auxiliary=False,power_w=40,mode='WAIT',**kw):
    return model.Sample(at,top,bottom,compressor,auxiliary,mode,power_w,**kw)

@pytest.mark.parametrize('top,bottom,expected',[(30,20,0),(29,10,0),(60,60,100),(40,40,33),(45,45,50)])
def test_reference_scale(top,bottom,expected):
    o=model.ThermalObserver();r=o.step(sample(top=top,bottom=bottom))
    assert r['soc']==expected
    assert r['lower']<=expected<=r['upper']
    if top<=30:assert r['lower']==r['upper']==0

def test_same_two_probes_admit_different_internal_states_and_soc():
    a=[22,22,22,22,59,59]
    b=[22,59,59,59,59,59]
    assert a[0]==b[0] and a[-1]==b[-1]
    assert model.soc(b,59)>model.soc(a,59)+40
    o=model.ThermalObserver();r=o.step(sample())
    assert r['upper']-r['lower']>=50
    assert r['quality']=='learning' and not r['interval_is_calibrated_confidence']

def test_advection_mass_balance_and_mixing_conserve_energy():
    t=[10,20,30,40,50,60]
    out=model.advect(t,3,10)
    # One explicit substep: removed top-water heat equals incoming cold replacement.
    assert sum(out)==pytest.approx(sum(t)+(10-60)*3/model.CELL_L)
    inverted=[50,40,20,30,65,60]
    mixed=model.mix_unstable(inverted)
    assert sum(mixed)==pytest.approx(sum(inverted))
    assert mixed==sorted(mixed)

def test_heat_balance_known_auxiliary_and_passive_loss():
    p=model.Particle([40]*6,[2,1,2],0,0,10,.5)
    r=model.predict(p,sample(top=40,bottom=40,auxiliary=True,power_w=1540),1,0,40)
    assert (sum(r)-240)*model.CELL_L*model.CP_WH_L_K==pytest.approx(1500/60)
    p.loss_w_k=2
    r=model.predict(p,sample(top=40,bottom=40),1,0,40)
    assert sum(r)<240

def test_large_draw_is_stable_and_does_not_heat_the_tank():
    t=[60]*6
    r=model.advect(t,180,10)
    assert 10<=min(r)<=max(r)<=60
    assert sum(r)<sum(t)

def test_bottom_drop_is_not_an_instant_soc_reset():
    o=model.ThermalObserver();o.step(sample(top=59,bottom=55))
    for i,bottom in enumerate([53,49,44,38,33,28,24,22],1):
        r=o.step(sample(at=1800000000+i*80,top=59,bottom=bottom))
        assert r['soc']>0
        assert r['lower']<=r['soc']<=r['upper']
    assert r['draw_events']>=1

def test_restoration_preserves_internal_state_and_parameters():
    o=model.ThermalObserver();o.step(sample(top=59,bottom=40))
    for i in range(1,12):o.step(sample(at=1800000000+i*80,top=59,bottom=40))
    restored=model.ThermalObserver.restore(json.loads(json.dumps(o.dump())))
    assert restored.dump()==o.dump()
    r1=o.step(sample(at=1800001000,top=59,bottom=39))
    r2=restored.step(sample(at=1800001000,top=59,bottom=39))
    assert r1==r2

@pytest.mark.parametrize('change',[{'top':float('nan')},{'bottom':None},{'top':True},{'bottom':96},{'at':float('inf')},{'top':20,'bottom':50}])
def test_invalid_input_does_not_advance_or_create_zero(change):
    o=model.ThermalObserver();o.step(sample())
    previous=o.dump()
    with pytest.raises(ValueError):o.step(sample(at=1800000100,**change)) if 'at' not in change else o.step(sample(**change))
    assert o.dump()==previous

def test_duplicate_future_rollback_and_gap_do_not_integrate_fake_heating():
    o=model.ThermalObserver();first=o.step(sample(compressor=True,power_w=550,mode='HOTWATER'))
    for at in [1800000000,1799999999,1800000005]:assert o.step(sample(at=at))==first
    assert o.samples==1
    later=o.step(sample(at=1800100000,top=59,bottom=22))
    assert later['gaps']==1 and later['phase']=='gap'
    assert later['upper']-later['lower']>=50

def test_fan_baseline_learns_without_labeling_effective_gain_as_cop():
    o=model.ThermalObserver()
    for i in range(5):o.step(sample(at=1800000000+i*80,top=50,bottom=50,power_w=39,fan_key='1/1'))
    r=o.snapshot()
    assert r['fan_baselines']['1/1']['watts']==39
    assert 'effective_heat_gains' in r and 'cop' not in r
    assert not r['calibrated_by_measured_draw'] and not r['sensor_positions_verified']

def test_snapshot_and_store_do_not_alias_live_training_parameters():
    o=model.ThermalObserver();r=o.step(sample(fan_key='1/1'))
    stored=o.dump()
    o.fan_baselines['1/1']['watts']=99
    assert r['fan_baselines']['1/1']['watts']==40
    assert stored['fan_baselines']['1/1']['watts']==40

@pytest.mark.parametrize('mutation',[
    lambda d:d.update(version=999),lambda d:d.update(particles=[]),
    lambda d:d['particles'][0]['temperatures'].__setitem__(2,float('nan')),
    lambda d:d['particles'][0]['gains'].__setitem__(0,-1),lambda d:d.update(samples=-1),
    lambda d:d.update(last_draw_at=1900000000),
])
def test_corrupt_store_is_rejected(mutation):
    o=model.ThermalObserver();o.step(sample());d=o.dump();mutation(d)
    with pytest.raises((TypeError,ValueError)):model.ThermalObserver.restore(d)

def test_priors_adapt_but_quality_never_claims_measured_calibration():
    o=model.ThermalObserver()
    start=time.monotonic()
    for i in range(40):
        o.step(sample(at=1800000000+i*80,top=50,bottom=50))
    # Flat, quantized temperatures do not identify thermal loss parameters.
    assert o.adaptations==0
    assert o.snapshot()['quality']=='learning'
    assert time.monotonic()-start<12
