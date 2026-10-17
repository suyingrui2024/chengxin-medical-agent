import json
from pathlib import Path
from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact
backend = ScriptBackend(); events = EventStore()
engine = AdaptiveConsultationEngine(content=ContentRepository(),triage=TriageService(),self_checks=SelfCheckService(),events=events,ranker=OrderedQuestionRanker(),backend=backend)
state, _ = engine.create_session()
backend.commands = [cmd('record_facts',facts=[fact('consult_for','self','我自己'),fact('age_years',32,'32岁'),fact('chief_complaint','咳嗽发烧','咳嗽发烧'),fact('onset','两天','两天'),fact('emergency_screen',None,'上述危险信号都没有','absent')]),cmd('show_self_check',task_id='axillary_temperature')]
reply = engine.submit(state.session_id,UserInput('我自己32岁，咳嗽发烧两天；上述危险信号都没有；家里有数字体温计，想按图测一下腋温。'))
assert reply.kind == 'self_check', reply.kind
backend.commands = [cmd('ask_question',question_id='trajectory')]
reply = engine.submit(state.session_id,UserInput('腋温38.25℃'))
assert state.facts['temperature_c'].value == 38.25
raw = '总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。'
rows = [('trajectory','stable','变化不大','present'),('severity',3,'不适程度3分','present'),('associated_symptoms',None,'没有其他不适','absent'),('resp_breathing','没有气促，能完整说话','没有气促，能完整说话','present'),('resp_cough_details','干咳，没有咳血','干咳，没有咳血','present'),('resp_intake','能正常喝水吃饭，排尿正常','能正常喝水吃饭，排尿正常','present'),('medications_taken',None,'没有用药','absent'),('allergies',None,'过敏不知道','unknown'),('relevant_history',None,'没有相关既往病史','absent'),('patient_goal','整理信息去就医','想整理信息去就医','present')]
backend.commands = [cmd('record_facts',facts=[fact(k,v,e,a) for k,v,e,a in rows]),cmd('review_summary')]
reply = engine.submit(state.session_id,UserInput(raw)); assert reply.kind == 'summary', reply.kind
old = state.facts['temperature_c']; backend.commands = [cmd('show_self_check',task_id='axillary_temperature'),cmd('review_summary')]
text = '我刚刚又测了一次腋温40.1℃，想重新测量。'
reply = engine.submit(state.session_id,UserInput(text))
errors = [event.payload for event in events.for_session(state.session_id) if event.event_type == 'tool.completed' and event.payload.get('result',{}).get('ok') is False]
result = {'synthetic_only':True,'external_model_calls':0,'scope':'Actual applicable registered fever task -> accepted reading -> history -> summary -> new axillary reading and scripted retest request. Not live-model error-rate or clinical accuracy validation.','new_user_input':text,'reply_kind':reply.kind,'latest_temperature_fact':state.facts['temperature_c'].to_dict(),'previous_reading_unchanged':state.facts['temperature_c'].fact_id == old.fact_id,'retest_tool_errors':errors,'repeat_measurement_update_available':state.facts['temperature_c'].value == 40.1}
Path('.runtime/next-repeated-observation-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'external_model_calls':0,'repeat_measurement_update_available':result['repeat_measurement_update_available'],'latest_temperature_fact_value':state.facts['temperature_c'].value,'retry_error_count':len(errors)}))
