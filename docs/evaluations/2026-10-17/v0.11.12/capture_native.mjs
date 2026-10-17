// Capture the packaged application's actual viewport without a model provider.
import { _electron as electron } from "@playwright/test";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
const [project, output, binary] = process.argv.slice(2);
if (!project || !output || !binary) throw new Error("Usage: node capture_native.mjs <project> <fresh-output> <packaged-binary>");
const root = path.resolve(project), directory = path.resolve(output);
await mkdir(directory);
const temporary = await mkdtemp(path.join(root, ".runtime/native-location-proof-"));
const { reachSelfCheck } = await import(pathToFileURL(path.join(root, "tests/fixtures/self-check-drafts.mjs")));
const { verifyLocationLayout } = await import(pathToFileURL(path.join(root, "tests/fixtures/location-layout.mjs")));
const env = { ...process.env, MEDAGENT_DESKTOP_PROFILE: path.join(temporary,"profile"), MEDAGENT_EXECUTION_ROOT: path.join(temporary,"workspaces"), MEDAGENT_DESKTOP_LIVE:"0", MEDAGENT_BACKEND:"demo" };
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app = await electron.launch({executablePath:path.resolve(binary),args:[],env});
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(900,680));
  const page=await app.firstWindow();
  const id=await reachSelfCheck(page);
  const measurements=await verifyLocationLayout(page,id,{shot:path.join(directory,"screenshot.png")});
  const health=await page.evaluate(async()=> (await fetch("/api/health")).json());
  const version=JSON.parse(await readFile(path.join(root,"package.json"),"utf8")).version;
  if(health.version!==version || health.backend!=="demo")throw new Error("Captured app version or mode does not match expected package");
  const report={version,health,packaged_binary:path.relative(root,path.resolve(binary)),window_size:{width:900,height:680},viewport:measurements.states[0].geometry[0].viewport,measurements,new_provider_calls:0,scope:"Actual packaged Electron viewport and controlled synthetic selections; not patient understanding or clinical validation.",passed:true};
  await writeFile(path.join(directory,"measurements.json"),JSON.stringify(report,null,2)+"\n");
  process.stdout.write(JSON.stringify({version,viewport:report.viewport,passed:true})+"\n");
} finally { await app?.close();await rm(temporary,{recursive:true,force:true}); }
