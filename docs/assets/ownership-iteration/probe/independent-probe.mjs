import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
const followup=process.argv.includes('--followup');
const scratch=await mkdtemp(join(tmpdir(),'chengxin-real-sdk-mac-'));
let calls=0;
const server=createServer(async(req,res)=>{
  for await(const _ of req){}
  const step=++calls;
  const content=step===1?{type:'tool_use',id:'probe-shell',name:'bash',input:{command:"sleep 4; printf original > lease-late.txt",run_in_background:true,description:'Synthetic background write with original two-second deadline'}}:{type:'text',text:'Synthetic probe finished.'};
  const followupCommand={type:'tool_use',id:'followup-shell',name:'bash',input:{command:"sleep 3; printf followup > followup.txt",description:'Independent foreground work outlives the original background deadline'}};
  const chosen=followup&&step===3?followupCommand:content;
  const events=[{type:'message_start',message:{id:'probe-'+step,type:'message',role:'assistant',model:'fixture',content:[],usage:{input_tokens:10,output_tokens:1}}},{type:'content_block_start',index:0,content_block:chosen},{type:'content_block_stop',index:0},{type:'message_delta',delta:{stop_reason:chosen.type==='tool_use'?'tool_use':'end_turn'},usage:{output_tokens:10}},{type:'message_stop'}];
  res.writeHead(200,{'Content-Type':'text/event-stream'});res.end(events.map(e=>'event: '+e.type+'\ndata: '+JSON.stringify(e)+'\n\n').join(''));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const child=spawn(process.execPath,['--no-warnings','--loader',new URL('./sdk-loader.mjs',import.meta.url).href,new URL('../../integrations/dsh/execution-worker.mjs',import.meta.url).pathname],{stdio:['pipe','pipe','pipe'],env:{...process.env,NODE_OPTIONS:''}});
const exited=new Promise(r=>child.once('exit',r));
const lines=createInterface({input:child.stdout});const events=[];const waiters=new Set();
let diagnostic=''; child.stderr.on('data',b=>diagnostic+=b.toString());
lines.on('line',l=>{const v=JSON.parse(l);events.push(v);for(const f of waiters)f(v);});
const wait=(kind,timeout=20000,after=0)=>new Promise((resolve,reject)=>{const prior=events.find(e=>e.type===kind&&e.sequence>after);if(prior)return resolve(prior);const t=setTimeout(()=>{waiters.delete(check);reject(new Error('Missing '+kind));},timeout);const check=e=>{if(e.sequence<=after||(e.type!==kind&&e.type!=='worker.error'&&e.type!=='turn.failed'))return;clearTimeout(t);waiters.delete(check);if(e.type===kind)resolve(e);else reject(new Error(JSON.stringify(e)));};waiters.add(check);});
const send=v=>child.stdin.write(JSON.stringify(v)+'\n');
try{
 send({method:'configure',id:randomUUID(),workspace:scratch,state:join(scratch,'state'),endpoint:`http://127.0.0.1:${server.address().port}/anthropic`,api_key:'synthetic-basic-probe-only',model:'fixture',python:'/usr/bin/python3'});
 await wait('ready');
 const original=randomUUID();
 const started=Date.now();
 send({method:'run',id:original,text:'Start the known synthetic background command. Its two-second deadline must remain unchanged.',max_calls:2,time_budget_seconds:2});
 const done=await wait('turn.completed',30000);
 const initialJobs=events.filter(e=>e.type==='jobs.snapshot').at(-1)?.jobs;
 if(!initialJobs?.some(j=>j.status==='running'))throw new Error('Actual background job not observed');
 let followupId=null;
 if(followup){
   followupId=randomUUID();
   send({method:'run',id:followupId,text:'Run the independent synthetic follow-up command without restarting, extending, or changing the original background job.',max_calls:2,time_budget_seconds:10});
   await wait('turn.completed',10000,done.sequence);
 }
 await new Promise(r=>setTimeout(r,Math.max(0,4800-(Date.now()-started))));
 let artifact=null;try{artifact=await readFile(join(scratch,'lease-late.txt'),'utf8');}catch{}
 let followupArtifact=null;try{followupArtifact=await readFile(join(scratch,'followup.txt'),'utf8');}catch{}
 console.log(JSON.stringify({real_sdk:true,host_worker:true,outer_isolation:false,followup,calls,artifact,followupArtifact,elapsed_ms:Date.now()-started,original,followupId,initialJobs,deadlines:events.filter(e=>e.type==='turn.deadline'),cancelled:events.filter(e=>e.type==='jobs.cancelled')}));
} catch(e){console.log(JSON.stringify({error_type:e.constructor.name,message:e.message.slice(0,1000),diagnostic:diagnostic.slice(-1500)}));process.exitCode=1;}
finally{
 await writeFile(new URL(followup?'./followup-events.json':'./control-events.json',import.meta.url),JSON.stringify({events,diagnostic},null,2));
 send({method:'close'});child.stdin.end();const timer=setTimeout(()=>child.kill('SIGKILL'),3000);await exited;clearTimeout(timer);lines.close();server.closeAllConnections();await new Promise(r=>server.close(r));await rm(scratch,{recursive:true,force:true});
}
