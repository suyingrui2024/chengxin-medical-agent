from pathlib import Path
import ast,hashlib,json,re,shutil
ROOT=Path.cwd();WORK=ROOT/'.runtime/engineering-completion-20261017';OUT=ROOT/'docs/evaluations/2026-10-17/engineering-completion'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
assert not OUT.exists()
logs=ROOT/'docs/evaluations/2026-10-17/v0.11.17/logs'
for name,count in [('ui-mono.log',74),('native-source-mono.log',36),('native-package-mono.log',36)]:assert re.search(rf'\b{count} passed\b',(logs/name).read_text()),name
assert 'Ran 398 tests' in (logs/'backend-mono.log').read_text() and 'OK (skipped=54)' in (logs/'backend-mono.log').read_text()
assert '# pass 21' in (logs/'node-bridge.log').read_text() and '# fail 0' in (logs/'node-bridge.log').read_text()
for name in ['package-verification.json','v01116-evidence.json','v01117-evidence.json','review-packet-verification.json']:assert read(WORK/name)['passed']
checks=ROOT/'docs/review-packets/2026-10-17-v0.11.17-checks'
assert read(checks/'ui-verification.json')['passed'] and read(checks/'exporter-verification.json')['passed']
base=ROOT/'docs/evaluations/2026-10-17/v0.11.16';old=read(base/'source-files.json')
core=[n for n in old if n.startswith('src/medagent/') and n.endswith('.py') and n!='src/medagent/__init__.py']
assert all(sha(ROOT/n)==old[n] for n in core)
tree=ast.parse((ROOT/'src/medagent/agent_tools.py').read_text())
tools=next(node.value for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOOL_DEFINITIONS' for t in node.targets))
assert len(tools.elts)==13
matrix=[
('research_design','研究与架构设计',['docs/研究与可行性评估.md','docs/门诊式问诊与图示自查背调.md','docs/首版产品与技术设计.md','docs/结构化界面项目调研与接入方案.md'],[], '设计、调研和可行性说明已交付；不把研究材料作为临床有效性证据。'),
('desktop','独立图形桌面',['desktop/main.cjs','desktop/preload.cjs','scripts/start-desktop.sh'],['tests/desktop/app.spec.mjs','tests/desktop/fonts.spec.mjs'],'Linux x64独立窗口、私有服务、启动与关闭；36组源码和36组打包版检查，独立Debian系统文件补验。'),
('medical_tool_loop','问诊与显式工具循环',['src/medagent/agent.py','src/medagent/agent_tools.py','src/medagent/content/questions.json'],['tests/test_agent.py','tests/test_consultation.py'],'13个受控工具；批量事实、来源校验、工具拒绝后重规划、问诊及预算终止。'),
('continuation','问诊纠错和任务接续',['src/medagent/task_conversation.py','src/medagent/location_revisions.py','src/medagent/calculation_followup.py'],['tests/desktop/location-revisions.spec.mjs','tests/desktop/repeated-observations.spec.mjs','tests/desktop/calculation-supplements.spec.mjs'],'已确认定位更正、重复测量和同轮补充保留原问诊位置；旧动作失效，失败与未知状态分别处理。'),
('visual_tasks','图示自查与低文字交互',['src/medagent/web/visual-question.js','src/medagent/web/location-answer-ui.js','src/medagent/extensions/packs/temperature.json','src/medagent/extensions/packs/location.json'],['tests/ui/visual-consultation.spec.mjs','tests/desktop/self-check-conversation.spec.mjs'],'固定图示、分步操作、明确确认与跳过、程度刻度和原话按需展开；布局检查不代表患者理解改善。'),
('structured_rendering','结构化输出与渲染',['src/medagent/presentation.py','src/medagent/web/structured-ui.js'],['tests/test_presentation.py','tests/ui/glucose-calculation.spec.mjs'],'注册组件、结构化输入输出、惰性文本及回退；SVG图示17处文字标签保留为矢量。'),
('plugins_calculations','插件模板与可解释计算',['src/medagent/extensions/registry.py','src/medagent/extensions/builtin.py','src/medagent/extensions/glucose.py','src/medagent/extensions/templates/general.json','src/medagent/extensions/templates/history.json'],['tests/test_capabilities.py','tests/test_runtime.py','tests/test_glucose_capability.py','tests/desktop/patient-calculation.spec.mjs'],'受信能力契约、模板组合、会谈配置版本固定；单位和血糖换算展示核对输入、公式、出处、结果与限制。'),
('execution','DSH文件、终端、联网与子任务底座',['src/medagent/execution.py','integrations/dsh/execution-worker.mjs','integrations/dsh/execution.patch.yml','integrations/dsh/execution-control.mjs'],['tests/desktop/execution.spec.mjs','tests/desktop/projects.spec.mjs'],'官方SDK执行工作台可操作项目文件、代码及子任务；搜索和网页工具保留官方配置；依用户安排暂不自动接入患者路径，不声称总体任务准确率等同DSH或Codex。'),
('cache_models','DeepSeek接入、缓存与实际迭代',['src/medagent/backends.py','src/medagent/env_config.py','integrations/dsh/medical.patch.yml'],['tests/test_cache_config.py','tests/test_recorded_recovery.py'],'固定共享前缀、患者上下文分离、缓存用量核算；既有真实API原始失败及复验保留，51个运行时Python文件与该医疗验收版本一致。'),
('failure_isolation','错误、过期结果及优先中断',['src/medagent/decision_wait.py','src/medagent/execution_store.py'],['tests/test_consultation_interrupt.py','tests/ui/urgent-interrupt.spec.mjs','tests/ui/consultation-subjects.spec.mjs'],'普通等待、明确求助中断、迟到结果隔离、对象与草稿分离、故障重试；仅验证工程处理，不证明危险信号临床覆盖率。'),
('delivery','工程验收与可复查交付',['scripts/verify_desktop_package.py','scripts/verify_clean_desktop.py','scripts/check-bundled-fonts.mjs','docs/实验与验收方案.md'],[],'默认后端344通过54跳过、浏览器74通过、源码和程序原生各36通过、Node21通过、冻结220通过；程序120文件及544适用生产依赖版本匹配。'),
('review_preparation','版本化审阅材料',['scripts/export_review_packet.py','docs/当前版本完成审查与后续接续.md'],[],'28项离线内容材料、原始来源摘要和空白审核表已交付；离线显示与导出一致性通过，审核状态保持待审。'),
]
requirements=[]
for key,title,sources,tests,scope in matrix:
 for name in sources+tests:assert (ROOT/name).is_file(),name
 requirements.append({'id':key,'requirement':title,'engineering_status':'accepted','evidence_scope':scope,'sources':{n:sha(ROOT/n) for n in sources},'tests_inspected':{n:sha(ROOT/n) for n in tests}})
for version in ['v0.11.16','v0.11.16-clean-environment','v0.11.17']:
 a=ROOT/'docs/evaluations/2026-10-17'/version;m=read(a/'archive-manifest.json');assert all(sha(a/n)==h for n,h in m['files'].items())
OUT.mkdir()
for name in ['package-verification.json','v01116-evidence.json','v01117-evidence.json','review-packet-verification.json']:shutil.copyfile(WORK/name,OUT/name)
for name in ['docs/当前版本完成审查与后续接续.md','docs/实验与验收方案.md']:
 dest=OUT/'documents'/Path(name).name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(ROOT/name,dest)
report={'date':'2026-10-17','app_version':'0.11.17','scope_confirmed_by_user':'工程原型设计与工程验收','engineering_goal_complete':True,'requirements':requirements,'mandatory_engineering_items_remaining':[],'medical_python_files_unchanged_since_live_evaluation':len(core),'external_model_requests_this_completion_audit':0,'verification_records':{name:sha(OUT/name) for name in ['package-verification.json','v01116-evidence.json','v01117-evidence.json','review-packet-verification.json']},'later_phases':['具名临床审核和独立临床病例评价','患者图示理解与操作研究','生产患者数据与正式产品边界评估','其他平台／独立机器安装验证','摄像头、手环、医保、SAM3D及第三方插件安装','患者路径自动调用通用执行工作台'],'limits':'Engineering prototype acceptance only. No clinical effectiveness, general diagnosis, prescribing, cross-platform support or DSH/Codex performance equivalence claim.'}
(OUT/'completion-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
shutil.copyfile(WORK/'record.py',OUT/'record.py')
print(json.dumps({'passed':True,'engineering_requirements_accepted':len(requirements),'mandatory_engineering_items_remaining':0,'unchanged_runtime_python_files':len(core),'model_requests':0},ensure_ascii=False))
