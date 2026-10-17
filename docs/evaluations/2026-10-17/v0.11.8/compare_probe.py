import argparse,json,sys
from pathlib import Path
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
assert not args.output.exists()
project=args.project.resolve();sys.path[:0]=[str(project/'src'),str(project)]
from tests import test_patient_calculations as fixture
from medagent.consultation import UserInput
f=fixture.PatientCalculationTests();f.setUp()
text='我自己32岁，咳嗽两天；请把1.25kg换算成g；昨晚用过药物甲。'
commands=[{'tool':'run_capability','arguments':{'id':'calculate.units','input':{'value':'1.25','from_unit':'kg','to_unit':'g'},'evidence':'请把1.25kg换算成g'}},{'tool':'record_facts','arguments':{'facts':[{'key':'consult_for','value':'self','assertion':'present','evidence':'我自己'},{'key':'age_years','value':32,'assertion':'present','evidence':'32岁'},{'key':'chief_complaint','value':'咳嗽','assertion':'present','evidence':'咳嗽'},{'key':'onset','value':'两天','assertion':'present','evidence':'两天'},{'key':'medications_taken','value':'昨晚用过药物甲','assertion':'present','evidence':'昨晚用过药物甲'}]}},{'tool':'ask_question','arguments':{'question_id':'emergency_screen'}}]
f.backend.commands=commands.copy()
draft=f.engine.submit(f.state.session_id,UserInput(text));result=f.engine.submit(f.state.session_id,f.confirm_input(draft));reply=f.engine.submit(f.state.session_id,UserInput('继续问诊','resume_after_calculation',result.action_id,{'execution_id':result.calculation_result['execution_id']}))
report={'synthetic_only':True,'external_model_calls':0,'scripted_commands':commands,'scripted_backend_decisions':len(f.backend.calls),'unused_commands':len(f.backend.commands),'question_id':reply.question_id,'facts':f.state.to_dict()['facts'],'calculation_count':len(f.state.calculations),'followup':getattr(reply,'calculation_followup',None),'events':[e.to_dict() for e in f.engine.events.for_session(f.state.session_id)]}
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'question_id':reply.question_id,'fact_count':len(f.state.facts),'backend_decisions':len(f.backend.calls),'calculation_count':len(f.state.calculations)},ensure_ascii=False))
