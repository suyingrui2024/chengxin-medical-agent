import { _electron as electron } from '@playwright/test';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [project, output, binary] = process.argv.slice(2).map(value => path.resolve(value));
await mkdir(output);
const { createSelfCheckConversationModel, verifySelfCheckConversation } = await import(pathToFileURL(path.join(project, 'tests/fixtures/self-check-conversation.mjs')));
const model = await createSelfCheckConversationModel();
const temporary = await mkdtemp(path.join(project, '.runtime/native-conversation-proof-'));
const env = {...process.env, MEDAGENT_DESKTOP_PROFILE: path.join(temporary,'profile'),
  MEDAGENT_EXECUTION_ROOT: path.join(temporary,'workspaces'), MEDAGENT_DESKTOP_LIVE: '1',
  MEDAGENT_BACKEND: 'compatible', MEDAGENT_LLM_BASE_URL: model.endpoint,
  MEDAGENT_LLM_MODEL: 'fixture', MEDAGENT_LLM_API_KEY: 'local-native-conversation'};
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app = await electron.launch({executablePath: binary, args: [], env});
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(900,680));
  const page = await app.firstWindow();
  const originalScreenshot = page.screenshot.bind(page);
  let screenshotGeometry;
  page.screenshot = async options => {
    screenshotGeometry = await page.evaluate(()=>{
      const pane=document.querySelector('#active-task').getBoundingClientRect();
      return ['#active-task h3','.body-proposal','.body-map-canvas','.map-confirm','#message-input','#urgent-help'].map(selector=>{
        const rect=document.querySelector(selector).getBoundingClientRect();
        return {selector,x:rect.x,y:rect.y,width:rect.width,height:rect.height,
          visible:rect.top>=0&&rect.bottom<=innerHeight&&
            (['#message-input','#urgent-help'].includes(selector)||(rect.top>=pane.top&&rect.bottom<=pane.bottom))};
      });
    });
    return originalScreenshot(options);
  };
  await verifySelfCheckConversation(page,model,{shot:path.join(output,'screenshot.png')});
  const health=await page.evaluate(async()=> (await fetch('/api/health')).json());
  const version=JSON.parse(await readFile(path.join(project,'package.json'),'utf8')).version;
  if(health.version!==version||!screenshotGeometry.every(item=>item.visible))throw new Error('Native proof failed');
  const report={version,health,packaged_binary:path.relative(project,binary),viewport:page.viewportSize(),
    window_size:{width:900,height:680},geometry:screenshotGeometry,local_fixture_model_requests:model.requests.length,
    external_provider_calls:0,scope:'Actual packaged Electron with a local scripted HTTP model; shared assertions cover pause, resume, draft, explicit confirmation, original source and stale controls.',passed:true};
  await writeFile(path.join(output,'measurements.json'),JSON.stringify(report,null,2)+'\n');
  process.stdout.write(JSON.stringify({version,passed:true,visible_elements:screenshotGeometry.length})+'\n');
} finally {
  await app?.close();await model.close();await rm(temporary,{recursive:true,force:true});
}
