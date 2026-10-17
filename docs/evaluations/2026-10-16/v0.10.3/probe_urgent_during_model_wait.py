"""Zero-provider-call evidence for the documented in-flight consultation lock gap."""
from pathlib import Path
import json,threading,time
from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
entered=threading.Event();release=threading.Event();urgent_done=threading.Event();values={};errors=[]
class Delayed:
 name='local-blocking-probe'
 calls=0
 def decide(self,session_id,messages):
  self.calls+=1;entered.set()
  if not release.wait(5):raise AssertionError('Probe release missing')
  raise RuntimeError('Synthetic delayed provider failure')
backend=Delayed();engine=AdaptiveConsultationEngine(content=ContentRepository(),triage=TriageService(),self_checks=SelfCheckService(),events=EventStore(),ranker=OrderedQuestionRanker(),backend=backend)
state,_=engine.create_session();text='我现在严重呼吸困难'
assert engine.triage.scan_text(text)
def submit(name,text):
 try:values[name]=engine.submit(state.session_id,UserInput(text)).kind
 except Exception as error:errors.append(type(error).__name__)
 finally:
  if name=='urgent':urgent_done.set()
ordinary=threading.Thread(target=submit,args=('ordinary','咳嗽两天'));ordinary.start();assert entered.wait(3)
started=time.monotonic();urgent=threading.Thread(target=submit,args=('urgent',text));urgent.start()
blocked=not urgent_done.wait(.5)
release.set();ordinary.join(3);urgent.join(3)
assert not errors and not ordinary.is_alive() and not urgent.is_alive()
assert blocked and values['urgent']=='emergency' and backend.calls==1
report={'scope':'Existing deterministic risk phrase, synthetic delayed provider; engineering latency only, no medical accuracy claim.','model_provider_requests':0,'blocked_while_model_waited':blocked,'backend_had_to_be_released':True,'urgent_kind_after_release':values['urgent'],'ordinary_kind':values['ordinary'],'elapsed_seconds':round(time.monotonic()-started,3),'scripted_backend_calls':backend.calls,'current_gate_not_preemptive':True}
print(json.dumps(report,ensure_ascii=False))
