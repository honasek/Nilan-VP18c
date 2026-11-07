"""DHW menu navigation with delayed LCD frames; never connects to a device."""
from collections import deque
import importlib,sys,types
from pathlib import Path
import pytest

component=Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600'
package=types.ModuleType('fresh_menu_tests');package.__path__=[str(component)];sys.modules[package.__name__]=package
menu=importlib.import_module(package.__name__+'.menu_controls')

class Panel:
 def __init__(self,delay=0,two_enter=False):
  self.value=51;self.root=0;self.inside=False;self.index=0;self.edit=False;self.buffer=None
  self.ignore=False;self.jump=False;self.wrong=False;self.uncertain=False;self.bad_readback=False;self.bad_root=False
  self.delay=delay;self.two_enter=two_enter;self.armed=False;self.frames=deque();self.cached=self.screen();self.calls=[];self.commits=[]
 def screen(self):
  if self.bad_root:return 'UNKNOWN/UNAVAILABLE'
  if not self.inside:return ['AUTO */>1< 22°C','HOTWATER','COOLING'][self.root]
  if self.index:return 'EL.SUP/T11 --°C'
  if self.wrong and self.edit:return 'PRIORITY/{WATER}'
  value='{'+str(self.buffer)+'}' if self.edit else str(self.value)
  return 'COMP/T12 '+value+'°C'
 def display(self):return self.cached
 def queue(self,before):self.frames=deque([before]*self.delay+[self.screen()]);self.cached=before
 def resetMenu(self):
  before=self.screen();self.inside=False;self.root=0;self.index=0;self.edit=False;self.armed=False;self.queue(before);self.calls.append('RESET')
 def key(self,key):
  name=key.name;self.calls.append(name)
  if name=='NONE':self.cached=self.frames.popleft() if self.frames else self.screen();return self.cached
  before=self.screen()
  if name=='ESC':self.edit=False;self.inside=False;self.root=0;self.armed=False
  elif name=='ENTER':
   if self.edit:
    self.value=self.buffer;self.edit=False;self.commits.append(self.value)
    if self.uncertain:raise OSError('Commit applied but acknowledgement lost')
    if self.bad_readback:self.bad_root=True
   elif self.inside:
    if self.two_enter and not self.armed:self.armed=True
    else:self.edit=True;self.buffer=self.value
   elif self.root==1:self.inside=True;self.index=0
  elif name in ('UP','DOWN'):
   delta=1 if name=='UP' else -1
   if self.edit:
    if not self.ignore:self.buffer+=delta*(2 if self.jump else 1)
   elif self.inside:self.index=(self.index+(1 if name=='DOWN' else -1))%2
   else:self.root=(self.root+(1 if name=='DOWN' else -1))%3
  self.queue(before)
  return self.cached  # Deliberately stale action response even with delay=0.

@pytest.mark.parametrize('delay',[0,1,2])
@pytest.mark.parametrize('two_enter',[False,True])
def test_verified_51_to_55_with_delayed_frames_and_single_commit(delay,two_enter):
 p=Panel(delay,two_enter);c=menu.MenuControls(p)
 assert c.read('dhw_temperature')==51
 assert c.set('dhw_temperature',55)==55
 assert p.commits==[55] and c.values['dhw_temperature']==55
 assert p.calls.count('UP')==4
 assert c.set('dhw_temperature',51)==51
 assert p.commits==[55,51] and p.calls.count('DOWN')>=4

@pytest.mark.parametrize('delay',[0,1,2])
def test_already_correct_goal_is_read_without_entering_edit_or_commit(delay):
 p=Panel(delay);c=menu.MenuControls(p)
 assert c.set('dhw_temperature',51)==51 and p.commits==[]

@pytest.mark.parametrize('fault',['ignore','jump','wrong'])
def test_unconfirmed_step_is_never_repeated_or_committed(fault):
 p=Panel(2);setattr(p,fault,True);c=menu.MenuControls(p)
 with pytest.raises(menu.MenuWriteError,match='phase='):
  c.set('dhw_temperature',55)
 assert not p.commits and p.calls.count('UP')<=1
 assert c.values.get('dhw_temperature')!=55

def test_uncertain_commit_is_never_replayed_and_has_phase_context():
 p=Panel(2);p.uncertain=True;c=menu.MenuControls(p)
 with pytest.raises(menu.MenuWriteError,match='phase=commit commit_attempted=True'):
  c.set('dhw_temperature',55)
 assert p.commits==[55] and c.values.get('dhw_temperature')!=55

def test_final_unconfirmed_edit_is_cancelled_without_committing(monkeypatch):
 p=Panel(2);c=menu.MenuControls(p);read=c._read_edit
 def fail_final(spec,*,expected=None,previous=None):
  if expected==55 and previous is None:raise menu.MenuWriteError('Final edit proof missing')
  return read(spec,expected=expected,previous=previous)
 monkeypatch.setattr(c,'_read_edit',fail_final)
 with pytest.raises(menu.MenuWriteError,match='phase=edit_value commit_attempted=False'):
  c.set('dhw_temperature',55)
 assert not p.commits and p.edit is False and p.value==51

def test_readback_navigation_failure_does_not_replay_the_write():
 p=Panel(2);p.bad_readback=True;c=menu.MenuControls(p)
 with pytest.raises(menu.MenuWriteError,match='phase=readback commit_attempted=True'):
  c.set('dhw_temperature',55)
 assert p.commits==[55]
 assert p.calls.count('RESET')<=4

def test_absent_item_fails_before_edit_with_bounded_navigation():
 p=Panel(2);p.bad_root=True;c=menu.MenuControls(p)
 with pytest.raises(menu.MenuWriteError,match='phase=navigate commit_attempted=False'):
  c.set('dhw_temperature',55)
 assert not p.commits and p.calls.count('ENTER')==0
 assert len(p.calls)<100

def test_old_cached_menu_path_reproduces_navigation_failure():
 # Keep the former navigation algorithm here so a clean checkout needs no
 # household deployment baseline or Git history to reproduce the regression.
 class CachedNavigation(menu.MenuControls):
  def _find(self,predicate,limit=64):
   display=self.panel.display()
   if predicate(display):return display
   for direction in (menu.Key.DOWN,menu.Key.UP):
    seen={display}
    for _ in range(limit):
     display=self.panel.key(direction)
     if predicate(display):return display
     if display in seen:break
     seen.add(display)
   raise menu.MenuUnavailable('Cached navigation did not find the item')
  def enter_path(self,path):
   self.panel.resetMenu()
   for aliases in path:
    self._find(lambda text:self._path_match(text,aliases))
    self.panel.key(menu.Key.ENTER)
  def locate(self,spec):
   self.enter_path(spec.path)
   self._find(lambda text:spec.parse(text)[0])
   return spec.parse(self.panel.display())[1]
 p=Panel(2)
 with pytest.raises(menu.MenuUnavailable):CachedNavigation(p).locate(menu.BY_KEY['dhw_temperature'])
 assert not p.commits

@pytest.mark.parametrize('delay',[3,5,10])
def test_unconfirmed_long_delay_cannot_commit_wrong_field(delay):
 p=Panel(delay);c=menu.MenuControls(p)
 with pytest.raises(menu.MenuWriteError):c.set('dhw_temperature',55)
 assert not p.commits
