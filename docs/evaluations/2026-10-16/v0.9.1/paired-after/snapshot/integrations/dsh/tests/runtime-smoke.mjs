// Real DSH process + real official adapter, with a deterministic loopback model.
// This verifies integration and isolation, not the clinical quality of a live model.
import { createServer } from 'node:http';
import { once } from 'node:events';
import assert from 'node:assert/strict';
import { runDecision } from '../bridge.mjs';

const requests = [];
const proposed = JSON.stringify({tool: 'ask_question', arguments: {question_id: 'onset'}});
const server = createServer(async (req, res) => {
  try {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || '{}');
    requests.push({path: req.url, body});
    if (!req.url.endsWith('/messages')) {
      res.writeHead(404); res.end('{}'); return;
    }
    assert.equal(body.tools?.length || 0, 0, 'medical DSH must have no model-facing host tools');
    assert.ok(!raw.includes('unrelated-secret-marker'));
    const events = [
      {type:'message_start', message:{id:'medical-smoke',type:'message',role:'assistant',model:'deepseek-v4-flash',content:[],usage:{input_tokens:10,output_tokens:1}}},
      {type:'content_block_start',index:0,content_block:{type:'text',text:''}},
      {type:'content_block_delta',index:0,delta:{type:'text_delta',text:proposed}},
      {type:'content_block_stop',index:0},
      {type:'message_delta',delta:{stop_reason:'end_turn',stop_sequence:null},usage:{output_tokens:20}},
      {type:'message_stop'},
    ];
    res.writeHead(200, {'content-type':'text/event-stream'});
    res.end(events.map(e => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join(''));
  } catch (error) { res.destroy(error); }
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
try {
  const result = await runDecision([{role:'user',content:'咳嗽两天，请选择下一问。'}], {
    endpoint:`http://127.0.0.1:${server.address().port}/anthropic`,apiKey:'local-smoke-only',model:'deepseek-v4-flash',
  });
  assert.deepEqual(JSON.parse(result.final_response), JSON.parse(proposed));
  assert.ok(result.events.some(e => e.type === 'assistant/message'));
  assert.ok(result.events.some(e => e.type === 'turn/end'));
  assert.equal(requests.length, 1, 'no log uploads, catalog uploads, or additional model requests');
  console.log(JSON.stringify({status:'passed', real_dsh_runtime:true, model:'loopback_fixture',
    returned_tool:'ask_question', model_facing_host_tools:0, dsh_events:result.events.length}));
} finally {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
