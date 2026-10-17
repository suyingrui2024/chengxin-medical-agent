import json, threading, tempfile, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0,str(Path.cwd()/'scripts'))
from evaluate_priority_help import run_case, CASES
class Provider(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert not body.get('tools')
  with self.server.lock:
   number=self.server.count; self.server.count+=1
  facts=[{'key':key,'value':value,'assertion':'present','evidence':evidence} for key,value,evidence in [('consult_for','self','我自己'),('age_years',32,'32岁'),('chief_complaint','咳嗽','咳嗽'),('onset','两天','两天')]]
  proposal={'tool':'ask_question','arguments':{'question_id':'emergency_screen'}} if number==1 else {'tool':'record_facts','arguments':{'facts':facts}}
  events=[{'type':'message_start','message':{'id':'local-driver','model':'fixture','usage':{'input_tokens':10,'output_tokens':1}}},{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':json.dumps(proposal,ensure_ascii=False)}},{'type':'content_block_stop','index':0},{'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':10}},{'type':'message_stop'}]
  data=''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
  self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
provider=ThreadingHTTPServer(('127.0.0.1',0),Provider); provider.count=0; provider.lock=threading.Lock()
threading.Thread(target=provider.serve_forever,daemon=True).start()
settings={'MEDAGENT_LLM_BASE_URL':f'http://127.0.0.1:{provider.server_port}/anthropic','MEDAGENT_LLM_API_KEY':'local-only','MEDAGENT_LLM_MODEL':'fixture'}
results=[]
try:
 with tempfile.TemporaryDirectory() as raw:
  for name in CASES:
   result=run_case(name,settings,Path(raw)/name); results.append(result)
   print(json.dumps({'case':name,'passed':result['passed'],'checks':result['checks']},ensure_ascii=False),flush=True)
finally:
 provider.shutdown();provider.server_close()
Path('.runtime/v0103-priority-driver-local.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
assert all(r['passed'] for r in results)
