"""Same scripted model proposals against copied old and frozen new controllers."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();assert not args.output.exists()
project=args.project.resolve();sys.path[:0]=[str(project/'src'),str(project)]
from medagent import __version__
from medagent.consultation import UserInput
from tests import test_patient_calculations as fixture
from tests.test_calculation_supplements import Backend
helper=fixture.PatientCalculationTests();helper.setUp();engine,state=helper.engine,helper.state;backend=Backend();engine.backend=backend

def record(person,age,complaint,correction=False):
 return {'tool':'record_facts','arguments':{'facts':[{'key':key,'value':value,'assertion':'present','evidence':evidence,'correction':correction} for key,value,evidence in [('consult_for','other_adult',person),('age_years',age,str(age)+'岁'),('chief_complaint',complaint,complaint)]]}}
ask={'tool':'ask_question','arguments':{'question_id':'emergency_screen'}}
commands=[record('我父亲',68,'头疼'),ask,record('我母亲',65,'肚子痛',True),ask]
backend.commands=deepcopy(commands)
engine.submit(state.session_id,UserInput('我父亲68岁，头疼。'));before=deepcopy(state.to_dict());calls=len(backend.calls)
reply=engine.submit(state.session_id,UserInput('改问我母亲，她65岁，肚子痛。'))
report={'version':__version__,'scripted_commands':commands,'external_model_calls':0,'before':before,'after':state.to_dict(),'reply':reply.to_dict(),'extra_backend_decisions':len(backend.calls)-calls,'facts_unchanged':before['facts']==state.to_dict()['facts']}
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'version':__version__,'age_before':before['facts']['age_years']['value'],'age_after':state.get('age_years'),'reply':reply.kind,'facts_unchanged':report['facts_unchanged'],'extra_backend_decisions':report['extra_backend_decisions']},ensure_ascii=False))
