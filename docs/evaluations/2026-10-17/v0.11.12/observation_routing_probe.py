"""Synthetic semantic-routing probe, independent of graphical layout assertions."""
from pathlib import Path
import argparse
import sys
sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument("--project", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
sys.path[:0] = [str(args.project.resolve() / "src"), str(args.project.resolve())]
from copy import deepcopy
import json
from medagent.consultation import UserInput
from tests import test_patient_calculations as fixture
from tests.test_calculation_supplements import Backend
helper=fixture.PatientCalculationTests();helper.setUp();engine,state=helper.engine,helper.state
backend=Backend();engine.backend=backend
opening='我自己32岁，头疼；上述危险信号都没有；想按图指出部位。'
facts=[{'key':key,'value':value,'assertion':assertion,'evidence':evidence} for key,value,assertion,evidence in [('consult_for','self','present','我自己'),('age_years',32,'present','32岁'),('chief_complaint','头疼','present','头疼'),('emergency_screen',None,'absent','上述危险信号都没有')]]
backend.commands=[{'tool':'record_facts','arguments':{'facts':facts}},{'tool':'show_self_check','arguments':{'task_id':'body_location'}}]
reply=engine.submit(state.session_id,UserInput(opening))
assert reply.kind=='self_check' and state.active_task=='body_location'
before=deepcopy(state.to_dict());count=len(backend.calls)
text='昨晚用过药物甲。'
backend.commands=[{'tool':'record_facts','arguments':{'facts':[{'key':'medications_taken','value':'昨晚用过药物甲','assertion':'present','evidence':'昨晚用过药物甲'}]}},{'tool':'ask_question','arguments':{'question_id':'onset'}}]
reply=engine.submit(state.session_id,UserInput(text,expected_action_id=reply.action_id))
location=state.facts.get('symptom_location')
from medagent import __version__
report={'version': __version__, 'scope':'Local scripted model supplies correct medication extraction. This probe checks host routing, not model intelligence or clinical correctness.','external_model_calls':0,'input':text,'before':before,'after':state.to_dict(),'reply':reply.to_dict(),'scripted_model_calls_after_input':len(backend.calls)-count,'undesired_medication_text_written_as_location':bool(location and '药物甲' in str(location.value)),'medication_fact_also_recorded':state.get('medications_taken')=='昨晚用过药物甲','events':[e.to_dict() for e in engine.events.for_session(state.session_id)]}
output=args.output;assert not output.exists();output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:report[k] for k in ['external_model_calls','scripted_model_calls_after_input','undesired_medication_text_written_as_location','medication_fact_also_recorded']},ensure_ascii=False))
