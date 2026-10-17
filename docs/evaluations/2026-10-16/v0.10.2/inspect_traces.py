"""Read-only trace audit. Text indicators are review candidates, never verified exits."""
import collections,json,re,sys
from pathlib import Path
report=json.loads(Path(sys.argv[1]).read_text())
output=Path(sys.argv[2]);rows=[]
for row in report['runs']:
 snapshot=row['snapshots'][0];events={};lineage=[]
 def add(event,session=None):
  if event.get('type') not in {'tool/call','tool/result'}:return
  data=event.get('data',{});message=data.get('message',{})
  key=(event.get('type'),event.get('seq'),event.get('time'),data.get('callId') or message.get('toolCallId'))
  if key not in events or session:events[key]=(event,session)
 notifications=snapshot.get('notifications',[])+[e['notification'] for e in snapshot.get('events',[]) if e.get('notification')]
 for notification in notifications:
  if notification.get('method')=='session.event':
   params=notification['params'];add(params['event'],params.get('sessionId'))
  elif notification.get('method','').startswith('subagent.'):
   lineage.append(notification)
 for event in snapshot.get('events',[]):add(event)
 calls=[];results={}
 for event,session in sorted(events.values(),key=lambda x:(x[0].get('time',0),x[0].get('seq',0))):
  data=event['data']
  if event['type']=='tool/call':
   arguments=data.get('arguments',{})
   if isinstance(arguments,str):
    try:arguments=json.loads(arguments)
    except ValueError:arguments={'unparsed':arguments}
   calls.append({'session':session,'call_id':data.get('callId'),'name':data.get('name'),'arguments':arguments,'time':event.get('time')})
  else:
   message=data.get('message',{});results[message.get('toolCallId')]=message
 commands=[];network=[];tests=[];indicators=[]
 for call in calls:
  args=call['arguments'];command=args.get('command','') if isinstance(args,dict) else ''
  message=results.get(call['call_id'],{});text='\n'.join(c.get('text','') for c in message.get('content',[]) if isinstance(c,dict))
  if command:commands.append(command)
  if call['name'] in {'web_search','web_fetch'} or re.search(r'\b(curl|wget|urlopen|urllib|requests\.get|pip install|git clone)\b',command):network.append(call)
  if re.search(r'\b(pytest|benchmark_run_tests|unittest)\b',command):tests.append({'call_id':call['call_id'],'command':command,'output_tail':text[-5000:]})
  terms=[term for term in ['Traceback','ModuleNotFoundError','ImportError','AssertionError','FAILED','SyntaxError','Permission denied','command not found'] if term in text]
  if terms:indicators.append({'call_id':call['call_id'],'terms':terms})
 counts=collections.Counter(commands)
 verification=row.get('independent_verification',{})
 rows.append({'case':row['case'],'repetition':row['repetition'],'arm':row['arm'],'source_correct':row['source_correct'],'source_and_contract_passed':row['source_and_contract_passed'],'runtime_completed':row['runtime_completed'],'runtime_reason':snapshot.get('reason') or snapshot.get('error'),'model_calls':row['gateway']['attempted_calls'],'tool_counts':dict(collections.Counter(call['name'] for call in calls)),'root_and_child_tool_calls':calls,'subagent_notifications':lineage,'network_review_candidates':network,'test_commands':tests,'repeated_exact_commands':[{'command':cmd,'count':count} for cmd,count in counts.items() if count>1],'output_text_error_indicators':indicators,'independent_nonpassing_nodes':{k:v for k,v in verification.get('outcomes',{}).items() if v!='passed'},'missing_required_test_nodes':sum(node not in verification.get('outcomes',{}) for nodes in verification.get('test_id_map',{}).values() for node in nodes)})
audit={'scope':'Read-only completed-run trace audit. Network/error/duplicate command indicators require review; they do not establish external-answer access or process exit.','completed_runs':len(rows),'planned_runs':len(report['plan']),'model_requests_for_audit':0,'source_frozen_recorded':report['frozen_source_unchanged'],'runs':rows}
output.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'runs':len(rows),'network_candidates':sum(len(x['network_review_candidates']) for x in rows),'root_child_tools':sum(sum(x['tool_counts'].values()) for x in rows),'audit_model_requests':0}))
