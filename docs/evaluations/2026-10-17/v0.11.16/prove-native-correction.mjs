import { _electron as electron, expect } from "@playwright/test";
import { mkdtemp, mkdir, rm, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createLocationAnswersModel, verifyLocationAnswers } from "./actual-correction-replay.mjs";
import { degreeSnapshot } from "./source/tests/fixtures/visual-answer-drafts.mjs";

const work=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(work,"../..");
const out=path.join(work,"native-proof");
await mkdir(out,{recursive:true});
const temporary=await mkdtemp(path.join(work,"proof-profile-"));
const model=await createLocationAnswersModel();
const source=JSON.parse(await readFile(path.join(work,"actual-correction-source.json"),"utf8"));
const binary=path.join(root,"dist/desktop/Chengxin-0.11.16-linux-x64/Chengxin");
const env={...process.env,MEDAGENT_DESKTOP_PROFILE:path.join(temporary,"profile"),MEDAGENT_EXECUTION_ROOT:path.join(temporary,"workspaces"),MEDAGENT_DESKTOP_LIVE:"1",MEDAGENT_BACKEND:"compatible",MEDAGENT_LLM_BASE_URL:model.endpoint,MEDAGENT_LLM_MODEL:"fixture",MEDAGENT_LLM_API_KEY:"native-correction-proof-only"};
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app=await electron.launch({executablePath:binary,args:[],env});
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(900,680));
  const page=await app.firstWindow();
  const initial=await verifyLocationAnswers(page,model);
  const input=page.getByRole('textbox',{name:'输入你的描述'});
  await input.fill(source.patient_text);
  await page.getByRole('button',{name:'发送回答'}).click();
  const pane=page.locator('#active-task');
  await expect(pane.getByRole('button',{name:'左右 · 左侧',exact:true})).toBeVisible();
  await expect(pane.locator('.location-answer-source summary')).toHaveText('拟更正 · 待核对');
  const before=await degreeSnapshot(page,initial.sessionId);
  expect(before.state.facts.symptom_location).toEqual(initial.result);
  await expect(page.locator('#send-button')).toBeEnabled();
  const draft='这段补充尚未发送。';
  await input.fill(draft);
  const measurements=await page.evaluate(()=>['#active-task h3','.location-answer-review','.location-answer-final-actions','#message-input','#urgent-help'].map(selector=>{
    const element=document.querySelector(selector),r=element.getBoundingClientRect();
    return {selector,top:r.top,bottom:r.bottom,visible:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth};
  }));
  expect(measurements.filter(x=>!x.visible)).toEqual([]);
  await page.screenshot({path:path.join(out,'actual-correction-review.png'),fullPage:true});
  await pane.getByRole('button',{name:'核对并记录位置',exact:true}).click();
  await expect(page.locator('#active-question h2')).toBeVisible();
  const final=await degreeSnapshot(page,initial.sessionId);
  const fact=final.state.facts.symptom_location;
  expect(fact.supersedes).toBe(initial.result.fact_id);
  expect(fact.structured_input.answers.side.value).toBe('left');
  expect(fact.quality).toBe('not_assessed');
  expect(final.state.active_question).toBe('onset');
  expect(model.requests).toHaveLength(6);
  await expect(input).toHaveValue(draft);
  await writeFile(path.join(out,'measurements.json'),JSON.stringify({version:'0.11.16',binary,window:[900,680],source,measurements,local_fixture_requests:6,external_provider_requests:0,initial_fact_id:initial.result.fact_id,final_fact_id:fact.fact_id,original_question_restored:true,passed:true},null,2)+'\n');
  process.stdout.write(JSON.stringify({passed:true,local_requests:6,external_provider_requests:0})+'\n');
} finally {
  await app?.close(); await model.close(); await rm(temporary,{recursive:true,force:true});
}
