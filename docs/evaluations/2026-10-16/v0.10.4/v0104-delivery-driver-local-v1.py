import sys,json
from pathlib import Path
sys.path[:0]=[str(Path.cwd()),str(Path.cwd()/'scripts')]
from evaluate_delivery_checks import run_case,CASES
from tests.test_execution_delivery import Provider,tool,final
counts={}
def respond(index,body):
 text=json.dumps(body,ensure_ascii=False)
 case='inspection-only' if '只检查并报告' in text else 'versioned-api-test' if 'project_api.py' in text else 'arithmetic-test'
 step=counts.get(case,0)+1;counts[case]=step
 if step==1:return tool('delivery_check',label='仅检查' if case=='inspection-only' else '新增测试',command='python3 -m unittest tests.test_new -v',required=case!='inspection-only')
 if case=='inspection-only':return final('只报告失败，没有修改文件。')
 if step==2:
  before,after=('typehints=True','show_annotations=True') if case=='versioned-api-test' else ('add(2, 2), 5','add(2, 2), 4')
  command="python3 - <<'PY'\nfrom pathlib import Path\np=Path('tests/test_new.py')\np.write_text(p.read_text().replace("+repr(before)+","+repr(after)+"))\nPY"
  return tool('bash',command=command,description='Correct only the invalid expectation or keyword')
 if step==3:return tool('delivery_check',label='新增测试',command='python3 -m unittest tests.test_new -v')
 return final('修正并复验完成')
provider=Provider(respond)
settings={'MEDAGENT_LLM_BASE_URL':f'http://127.0.0.1:{provider.server_port}/anthropic','MEDAGENT_LLM_API_KEY':'local-only','MEDAGENT_LLM_MODEL':'fixture'}
results=[]
try:
 for case in CASES:
  result=run_case(case,settings,Path('.runtime/v0104-delivery-local-driver')/case);results.append(result)
  print(json.dumps({'case':case,'passed':result['passed'],'checks':result['checks']},ensure_ascii=False),flush=True)
finally:
 provider.shutdown();provider.server_close()
Path('.runtime/v0104-delivery-driver-local.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
assert all(result['passed'] for result in results)
