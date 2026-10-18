import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
const followup=process.argv.includes('--followup');
const funded=process.argv.includes('--funded');
const scratch=await mkdtemp(join(tmpdir(),'chengxin-child-owner-'));
let rootCalls=0,childCalls=0,releaseChild;
const held=new Promise(r=>releaseChild=r);
const server=createServer(async(req,res)=>{
 let raw='';for await(const part of req)raw+=part;
 const body=JSON.parse(raw);const root=JSON.stringify(body.messages||[]).includes('ROOT_BUDGET_OWNER');
 const step=root?++rootCalls:++childCalls;
 let block;
 if(root&&step===1)block={type:'tool_use',id:'spawn-child',name:'subagent',input:{description:'Independent synthetic child',prompt:'CHILD_BUDGET_OWNER: run the fixed synthetic command when returned and finish.',run_in_background:true}};
 else if(!root&&step===1){await held;block={type:'tool_use',id:'child-shell',name:'bash',input:{command:'printf child > child.txt',description:'Synthetic child output'}};}
 else block={type:'text',text:root?'Root acknowledgement.':'Child completed.'};
 const events=[{type:'message_start',message:{id:(root?'root':'child')+step,type:'message',role:'assistant',model:'fixture',content:[],usage:{input_tokens:10,output_tokens:1}}},{type:'content_block_start',index:0,content_block:block},{type:'content_block_stop',index:0},{type:'message_delta',delta:{stop_reason:block.type==='tool_use'?'tool_use':'end_turn'},usage:{output_tokens:10}},{type:'message_stop'}];
 if(res.destroyed)return;res.writeHead(200,{'Content-Type':'text/event-stream'});res.end(events.map(e=>'event: '+e.type+'\ndata: '+JSON.stringify(e)+'\n\n').join(''));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const child=spawn(process.execPath,['--no-warnings','--loader',new URL('./sdk-loader.mjs',import.meta.url).href,new URL('../../integrations/dsh/execution-worker.mjs',import.meta.url).pathname],{stdio:['pipe','pipe','pipe'],env:{...process.env,NODE_OPTIONS:''}});
const exited=new Promise(r=>child.once('exit',r));const lines=createInterface({input:child.stdout});const events=[];const waiters=new Set();let diagnostic='';
child.stderr.on('data',b=>diagnostic+=b.toString());lines.on('line',l=>{const v=JSON.parse(l);events.push(v);for(const f of waiters)f(v);});
const waitFor=(match,timeout=15000)=>new Promise((resolve,reject)=>{const prior=events.find(match);if(prior)return resolve(prior);const timer=setTimeout(()=>{waiters.delete(check);reject(new Error('Missing expected event'));},timeout);const check=e=>{if(!match(e)&&e.type!=='worker.error')return;clearTimeout(timer);waiters.delete(check);if(match(e))resolve(e);else reject(new Error(JSON.stringify(e)));};waiters.add(check);});
const send=v=>child.stdin.write(JSON.stringify(v)+'\n');
try{
 const original=randomUUID(),next=randomUUID();
 send({method:'configure',id:randomUUID(),workspace:scratch,state:join(scratch,'state'),endpoint:`http://127.0.0.1:${server.address().port}/anthropic`,api_key:'synthetic-child-owner-only',model:'fixture',python:'/usr/bin/python3'});
 await waitFor(e=>e.type==='ready');
 send({method:'run',id:original,text:'ROOT_BUDGET_OWNER: start the synthetic child and acknowledge. Keep its original shared three-call limit unchanged.',max_calls:funded?8:3,time_budget_seconds:15});
 await waitFor(e=>e.type==='turn.completed'&&e.turn_id===original);
 const observed=Date.now();while(childCalls<1&&Date.now()-observed<3000)await new Promise(r=>setTimeout(r,10));if(childCalls!==1)throw new Error('First child request was not held');
 if(followup){send({method:'run',id:next,text:'ROOT_BUDGET_OWNER: acknowledge an independent follow-up. Do not change the old child budget.',max_calls:funded?1:8,time_budget_seconds:2});await waitFor(e=>e.type==='turn.completed'&&e.turn_id===next);}
 await new Promise(r=>setTimeout(r,3000)); releaseChild();
 const finished=await waitFor(e=>e.type==='runtime.event'&&e.notification?.method==='subagent.finished');
 await new Promise(r=>setTimeout(r,100));
 let artifact=null;try{artifact=await readFile(join(scratch,'child.txt'),'utf8');}catch{}
 console.log(JSON.stringify({real_sdk:true,outer_isolation:false,followup,funded,original,next:followup?next:null,rootCalls,childCalls,artifact,finished:finished.notification.params,requests:events.filter(e=>e.type==='model.request_started').map(e=>({turn_id:e.turn_id,index:e.index})),childEvents:events.filter(e=>e.type==='runtime.event'&&e.notification?.method==='session.event'&&e.notification?.params?.sessionId!==events.find(e=>e.notification?.method==='session.event')?.notification?.params?.sessionId).map(e=>({turn_id:e.turn_id,session_id:e.notification.params.sessionId,type:e.notification.params.event.type}))}));
}catch(error){console.log(JSON.stringify({error_type:error.constructor.name,message:error.message.slice(0,1500),diagnostic:diagnostic.slice(-1500)}));process.exitCode=1;}
finally{releaseChild();await writeFile(new URL(followup?'./child-followup-events.json':'./child-control-events.json',import.meta.url),JSON.stringify({events,diagnostic},null,2));send({method:'close'});child.stdin.end();const timer=setTimeout(()=>child.kill('SIGKILL'),3000);await exited;clearTimeout(timer);lines.close();server.closeAllConnections();await new Promise(r=>server.close(r));await rm(scratch,{recursive:true,force:true});}
