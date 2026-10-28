"""Readiness evidence and fail-closed policy without Home Assistant or device I/O."""
import copy,importlib,sys,types
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
p=types.ModuleType('readiness_tests');p.__path__=[str(ROOT/'custom_components/nilan_cts600')];sys.modules[p.__name__]=p
gate=importlib.import_module(p.__name__+'.tuv_validation')
shadow=importlib.import_module(p.__name__+'.tuv_shadow')
live=importlib.import_module(p.__name__+'.tuv_live')
NOW=1800000000

def learner(errors=None):
 errors=errors or [20]*12
 l=SimpleNamespace(scores=[dict(issued_at=NOW-86400*(i%4)-3600,scored_at=NOW-86400*(i%4)-1200-i,target=52,mode='HOTWATER',outcome='reached',error_min=e) for i,e in enumerate(errors)],comfort={},summary=lambda:{'learning_ready':True})
 for hour in range(int((NOW-72*3600)//3600),int(NOW//3600)+1):l.comfort[str(hour)]={'seconds':3600.,'min_top':50}
 return l

def test_readiness_requires_causal_current_target_mode_unique_completions():
 l=learner();out=gate.evaluate(l,52,'HOTWATER',NOW,41,30)
 assert out['ready'];assert out['effective_margin_min']==120
 l.scores+=copy.deepcopy(l.scores)*4
 assert gate.evaluate(l,52,'HOTWATER',NOW,41,30)['unique_completions']==12
 assert not gate.evaluate(l,50,'HOTWATER',NOW,41,30)['ready']
 assert not gate.evaluate(l,52,'HEATING',NOW,41,30)['ready']

def test_recent_cold_water_and_missing_coverage_block_activation():
 l=learner();l.comfort[str(int(NOW//3600)-5)]['min_top']=28
 assert 'recent_top_temperature_below_minimum' in gate.evaluate(l,52,'HOTWATER',NOW,41,30)['reasons']
 l.comfort={}
 assert 'insufficient_recent_comfort_coverage' in gate.evaluate(l,52,'HOTWATER',NOW,41,30)['reasons']

def test_late_outlier_blocks_and_large_error_increases_margin():
 l=learner([150]*12);a=gate.evaluate(l,52,'HOTWATER',NOW,41,30)
 assert a['effective_margin_min']==165
 l=learner([20]*11+[170]);a=gate.evaluate(l,52,'HOTWATER',NOW,41,30)
 assert not a['ready'];assert 'observed_delay_exceeds_margin' in a['reasons']
 l=learner([220]*12);assert not gate.evaluate(l,52,'HOTWATER',NOW,41,30)['ready']

def test_comfort_credit_splits_hours_and_does_not_bridge_outage():
 d={};gate.credit(d,3590,3610,50)
 assert d['0']['seconds']==10 and d['1']['seconds']==10
 old=copy.deepcopy(d);gate.credit(d,3610,5000,20);assert d==old

def test_old_learner_store_migrates_without_discarding_history():
 old=shadow.ShadowLearner().dump();old.pop('comfort')
 new=shadow.ShadowLearner.restore(old,NOW)
 assert new.comfort=={};assert new.samples==old['samples']

def test_live_policy_denies_old_count_only_plan():
 context={'permitted':True,'valid':True,'plan':{'status':'would_delay','selected_variant':'full_delay','learning':{'learning_ready':True}}}
 assert live.candidate(context,NOW)==(None,'accuracy_or_comfort_not_validated')
