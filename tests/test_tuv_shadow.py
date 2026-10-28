"""Regression tests for whole-day demand, common horizons and window search."""
import ast
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import types
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

COMPONENT=Path(os.environ.get('NILAN_COMPONENT_PATH',Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600'))
pkg=types.ModuleType('shadow_v2_tests');pkg.__path__=[str(COMPONENT)];sys.modules[pkg.__name__]=pkg
model=importlib.import_module(pkg.__name__+'.tuv_model')
shadow=importlib.import_module(pkg.__name__+'.tuv_shadow')
live=importlib.import_module(pkg.__name__+'.tuv_live')
TZ=ZoneInfo('Europe/Prague')


def at(text='2026-10-24T00:45:00'):
    return datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp()


def tank(temp=48,loss=1.2,gain=2.5,now=None):
    o=model.ThermalObserver()
    o.last=model.Sample(now or at(),54,temp,False,False,'HOTWATER',28,fan_key='1/1')
    o.particles=[model.Particle([temp,temp+1,temp+2,temp+3,54,54],[gain]*3,loss,.01,10,.5)]
    o.phase='idle';o.samples=50
    return o


def settings(**changes):
    return replace(shadow.Settings(window_start='01:00',window_end='05:00',prior_daily_l=0,
                                   top_min_c=41,reserve_pct=35),**changes)


def profile(bins=None,kind='expected'):
    return shadow.DemandProfile(kind,'test',tuple(bins or [0.]*48))


def day(bins,minutes=None):
    return {'minutes':minutes or [30.]*48,'litres':bins[:]}


def test_whole_day_profiles_never_stack_different_days_peaks():
    l=shadow.ShadowLearner()
    for offset,index in enumerate((12,14,18,36,42)):
        bins=[0.]*48;bins[index]=100
        l.days[f'2026-10-{19+offset}']=day(bins)
    expected,stress=shadow.demand_profiles(l,settings(prior_daily_l=160),at(),at('2026-10-24T23:00:00'))
    assert sum(expected.bins)==100 and sum(stress.bins)*stress.factor==120
    assert sum(v>0 for v in expected.bins)==sum(v>0 for v in stress.bins)==1
    assert expected.bins==tuple(l.days[expected.observed_day]['litres'])
    assert expected.public()['draw_is_measured'] is False


def test_expected_medoid_is_not_inflated_by_one_large_day():
    l=shadow.ShadowLearner()
    for i,litres in enumerate((40,45,50,55,240)):
        bins=[0.]*48;bins[14]=litres
        l.days[f'2026-10-{19+i}']=day(bins)
    expected,stress=shadow.demand_profiles(l,settings(),at(),at('2026-10-24T09:00:00'))
    assert sum(expected.bins)==50
    assert sum(stress.bins)*stress.factor==288


def test_stress_day_selected_for_relevant_horizon_not_whole_day_total():
    l=shadow.ShadowLearner()
    for i,(morning,evening) in enumerate(((10,250),(80,0),(20,10))):
        bins=[0.]*48;bins[14]=morning;bins[40]=evening
        l.days[f'2026-10-{20+i}']=day(bins)
    _,stress=shadow.demand_profiles(l,settings(),at(),at('2026-10-24T09:00:00'))
    assert stress.observed_day=='2026-10-21' and stress.bins[14]==80


def test_partial_days_are_not_demand_evidence_and_covered_zero_stays_zero():
    l=shadow.ShadowLearner()
    for i in range(3):
        bins=[0.]*48;bins[14]=100
        minutes=[0.]*48;minutes[14]=30
        l.days[f'2026-10-{20+i}']=day(bins,minutes)
    expected,stress=shadow.demand_profiles(l,settings(prior_daily_l=160),at(),at()+86400)
    assert expected.source=='explicit_prior' and sum(expected.bins)==pytest.approx(160)
    assert stress.public()['daily_tank_l_model']==192
    for row in l.days.values():row['minutes']=[30.]*48;row['litres']=[0.]*48
    expected,_=shadow.demand_profiles(l,settings(prior_daily_l=160),at(),at()+86400)
    assert expected.source=='observed_whole_day' and sum(expected.bins)==0


def test_current_and_future_days_are_excluded_from_training():
    l=shadow.ShadowLearner()
    for date in ('2026-10-23','2026-10-24','2026-10-25'):
        l.days[date]=day([100.]*48)
    expected,_=shadow.demand_profiles(l,settings(prior_daily_l=160),at(),at()+86400)
    assert expected.source=='explicit_prior'


def test_dst_demand_uses_real_elapsed_time_and_normalizes_fold_coverage():
    l=shadow.ShadowLearner()
    for i in range(3):
        bins=[0.]*48;mins=[30.]*48
        bins[4]=20;mins[4]=60
        l.days[f'2026-10-{20+i}']=day(bins,mins)
    expected,_=shadow.demand_profiles(l,settings(),at('2026-10-25T00:45:00'),at('2026-10-25T09:00:00'))
    assert expected.bins[4]==10
    first=at('2026-10-25T02:15:00')
    assert expected.rate(first,settings())==expected.rate(first+3600,settings())==pytest.approx(1/3)


def test_all_scenarios_end_at_same_horizon_even_when_target_unreached():
    o=tank(gain=.02);now=o.last.at;deadline=at('2026-10-24T05:00:00');end=at('2026-10-24T09:00:00')
    common=dict(demand_profile=profile(),horizon=end)
    normal=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,now,deadline,'normal',**common)
    delayed=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,at('2026-10-24T01:00:00'),deadline,'full_delay',**common)
    assert normal['horizon_end']==delayed['horizon_end']==shadow.stamp(end)
    assert delayed['finish_at'] is None
    assert normal['compressor_electric_kwh_model']<=.45*(end-now)/3600+.001
    assert len(normal['_paths'][0]['end'])==6


def test_terminal_heat_and_stratification_deficit_cannot_be_credited_as_saving():
    o=tank();now=o.last.at;deadline=at('2026-10-24T05:00:00')
    a=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,now,deadline,'normal',demand_profile=profile())
    b=copy.deepcopy(a)
    b['_paths'][0]['electric_kwh']-=.5
    b['_paths'][0]['end']=[v-4 for v in b['_paths'][0]['end']]
    result=shadow.compare_energy(b,a)
    assert result['compressor_delta_kwh']==-.5 and result['end_tank_heat_delta_kwh']<0
    assert not result['terminal_state_equivalent'] and result['saving_electric_kwh_model'] is None
    # The same total energy in a differently stratified tank is also distinct.
    b=copy.deepcopy(a);b['_paths'][0]['end'][0]+=2;b['_paths'][0]['end'][-1]-=2
    result=shadow.compare_energy(b,a)
    assert result['end_tank_heat_delta_kwh']==0 and not result['terminal_state_equivalent']


def test_savings_require_both_equal_end_state_and_delivered_service():
    o=tank();now=o.last.at;deadline=at('2026-10-24T05:00:00')
    a=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,now,deadline,'normal',demand_profile=profile())
    for k in ('deadline_met','reserve_met','post_use_comfort_met'):a[k]=True
    b=copy.deepcopy(a);b['_paths'][0]['electric_kwh']-=.1
    assert shadow.compare_energy(b,a)['saving_electric_kwh_model']==.1
    b['post_use_comfort_met']=False
    assert shadow.compare_energy(b,a)['saving_electric_kwh_model'] is None


def test_first_crossing_deadline_readiness_and_post_use_comfort_are_separate():
    now=at();o=tank(50,loss=0,gain=.1)
    o.particles[0].temperatures=[50]*6
    bins=[0.]*48;bins[12]=180  # Draw after 05:00; does not spoil deadline readiness.
    result=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,now,at('2026-10-24T05:00:00'),
                           'normal',demand_profile=profile(bins),horizon=at('2026-10-24T09:00:00'))
    assert result['finish_epoch']==now and result['deadline_met'] and result['target_held_until_deadline']
    assert not result['post_use_comfort_met'] and result['post_use_min_top_c']<41


def test_cooling_within_hysteresis_holds_completed_cycle_until_deadline():
    o=tank(50,loss=1.2);o.particles[0].temperatures=[50]*6;now=o.last.at
    result=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,now,at('2026-10-24T05:00:00'),
                           'normal',demand_profile=profile())
    assert result['target_reached_before_deadline']
    assert result['target_held_until_deadline']
    assert not result['nominal_target_held_until_deadline']


def test_real_thermal_paths_accept_hysteresis_and_still_scan_later_starts():
    o=tank(48,loss=1.5,gain=1);o.last=replace(o.last,top=48);o.particles[0].temperatures=[48]*6
    plan=shadow.make_plan(o,shadow.ShadowLearner(),settings(),50,o.last.at)
    earliest=next(r for r in plan['scenarios'] if r['variant']=='full_delay' and not r.get('latest_start_candidate'))
    chosen=next(r for r in plan['scenarios'] if r.get('latest_start_candidate'))
    assert earliest['deadline_met'] and chosen['eligible']
    assert not earliest['expected']['nominal_target_held_until_deadline']
    assert datetime.fromisoformat(plan['latest_start']).timestamp()>at('2026-10-24T01:00:00')
    assert chosen['expected']['deadline_t12_c']>47


def test_house_heat_is_counted_over_entire_common_horizon():
    o=tank();o.last=replace(o.last,compressor=True,mode='HEATING');now=o.last.at
    deadline=now+3600;end=now+4*3600
    row=shadow.simulate(o,shadow.ShadowLearner(),settings(),50,now,now+1800,deadline,'full_delay',
                        demand_profile=profile(),horizon=end)
    assert row['compressor_electric_kwh_model']==pytest.approx(1.8)


def test_search_covers_full_window_when_earliest_candidate_fails(monkeypatch):
    o=tank();now=o.last.at;actual=shadow.simulate
    # Use one genuine thermal result as the shape, then isolate non-monotonic
    # search behavior from the uncertain thermal parameters.
    template=actual(o,shadow.ShadowLearner(),settings(),50,now,now,at('2026-10-24T05:00:00'),
                    'normal',demand_profile=profile(),horizon=at('2026-10-24T09:00:00'))
    def simulated(model,learner,settings,target,now,start,deadline,variant,*args,**kwargs):
        row=copy.deepcopy(template)
        good=at('2026-10-24T02:00:00')<=start<=at('2026-10-24T02:35:00')
        row.update(variant=variant,start_at=shadow.stamp(start),deadline_met=good,reserve_met=True,
                   post_use_comfort_met=True,cancelled_paths=0,finish_at=shadow.stamp(start+1800))
        return row
    monkeypatch.setattr(shadow,'simulate',simulated)
    plan=shadow.make_plan(o,shadow.ShadowLearner(),settings(),50,now)
    assert plan['latest_start']==shadow.stamp(at('2026-10-24T02:35:00'))
    checks=[r for r in plan['candidate_evaluations'] if r['variant']=='full_delay']
    assert checks[0]['passes'] is False and any(r['passes'] for r in checks)
    assert any(r['start_at']==shadow.stamp(at('2026-10-24T03:00:00')) for r in checks)
    assert plan['status']=='collecting' and plan['validated_for_control'] is False
    assert next(r for r in plan['scenarios'] if r.get('latest_start_candidate'))['eligible']
    json.dumps(plan,allow_nan=False)


def test_window_candidate_bounds_active_window_and_too_short_margin():
    s=at('2026-10-24T01:00:00');d=at('2026-10-24T05:00:00')
    rows=shadow.candidate_times(at(),s,d,30)
    assert rows[0]==s and rows[-1]==d-1800
    assert len(rows)==43
    assert shadow.candidate_times(d-900,s,d,30)==[]
    assert shadow.candidate_times(s+123,s,d,30)[0]==s+123


def test_common_horizon_extends_through_morning_evening_and_clock_change():
    s=settings(day_enabled=True)
    w=shadow.next_window(at('2026-10-25T00:45:00'),s)
    assert shadow.evaluation_horizon(at('2026-10-25T00:45:00'),w,s)==at('2026-10-25T09:00:00')
    assert shadow.evaluation_horizon(at('2026-10-25T00:45:00'),w,s)-at('2026-10-25T00:45:00')==9.25*3600
    w=shadow.next_window(at('2026-10-24T06:00:00'),s)
    assert w['kind']=='day'
    assert shadow.evaluation_horizon(at('2026-10-24T06:00:00'),w,s)==at('2026-10-24T23:00:00')


def test_plan_preserves_learning_and_observer_and_uses_no_hardware():
    o=tank();l=shadow.ShadowLearner();before=copy.deepcopy((o.dump(),l.dump()))
    plan=shadow.make_plan(o,l,settings(),50,o.last.at)
    assert (o.dump(),l.dump())==before
    assert plan['simulation_version']==3 and plan['shadow_only']
    assert all(r['horizon_end']==plan['horizon_end'] for r in plan['scenarios'])
    assert all(r['energy_comparison']['same_horizon'] for r in plan['scenarios'])
    assert plan['demand_profiles'][1]['kind']=='stress'
    assert all('_paths' not in row for row in plan['scenarios'])
    json.dumps(plan,allow_nan=False)
    tree=ast.parse((COMPONENT/'tuv_shadow.py').read_text(encoding='utf-8'))
    assert not any(isinstance(n,ast.ImportFrom) and n.module and 'homeassistant' in n.module for n in ast.walk(tree))


@pytest.mark.parametrize('field', ['post_use_comfort_met','expected_profile_passes','stress_profile_passes'])
def test_new_live_plan_must_pass_post_use_and_both_profiles(field):
    now=at();start=now+3600
    row={'variant':'full_delay','start_at':shadow.stamp(start),'reserve_met':True,'deadline_met':True,
         'cancelled_paths':0,'post_use_comfort_met':True,'expected_profile_passes':True,'stress_profile_passes':True}
    ctx={'permitted':True,'valid':True,'sample_at':now,'target':50,'actual_target':50,'top':55,'bottom':45,'lower':70,
         'plan':{'simulation_version':3,'control_readiness':{'version':1,'ready':True,'reasons':[],'target_c':50,'mode':'HOTWATER','as_of':now,'effective_margin_min':120},'status':'would_delay','selected_variant':'full_delay',
                 'learning':{'learning_ready':True},'issued_epoch':now,'initial_sample':{'at':now,'auxiliary':False,'mode':'HOTWATER'},
                 'settings':{'reserve_pct':35,'top_min_c':41,'margin_min':120},'target_c':50,'latest_start':shadow.stamp(start),
                 'deadline':shadow.stamp(now+7200),'window_start':shadow.stamp(start),'scenarios':[row]}}
    assert live.candidate(ctx,now)[0] is not None
    row[field]=False
    assert live.candidate(ctx,now)==(None,'scenario_not_verified')
