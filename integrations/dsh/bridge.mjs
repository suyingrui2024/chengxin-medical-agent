import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));

export function launchOptions(scratch, endpoint, apiKey, model, generation = {}) {
  const cliPackage = require.resolve('@deepseek-ai/dsh/package.json');
  const cli = join(dirname(cliPackage), 'lib/bin.js');
  const env = {};
  for (const key of ['PATH', 'SystemRoot', 'WINDIR', 'TMPDIR', 'TMP', 'TEMP', 'LANG']) {
    if (process.env[key]) env[key] = process.env[key];
  }
  Object.assign(env, {
    DSH_HOME: join(scratch, 'home'),
    DEEPSEEK_API_KEY: apiKey,
    DEEPSEEK_BASE_URL: endpoint,
    NO_COLOR: '1',
  });
  return {
    dshBin: cli, profile: 'sdk-minimal', patches: [join(here, 'medical.patch.yml')],
    dshHome: join(scratch, 'home'), processCwd: scratch, env,
    requestTimeoutMs: 30000, initializeTimeoutMs: 10000,
    disposeEofGraceMs: 1000, disposeGraceMs: 1000,
    cwd: scratch, provider: 'deepseek-official', model,
    maxTokens: generation.maxTokens ?? 2048,
    reasoningEffort: generation.reasoningEffort ?? 'off',
  };
}

export async function runDecision(messages, { endpoint, apiKey, model, deadlineMs = 35000, maxTokens, reasoningEffort } = {}) {
  if (!endpoint || !apiKey || !model) throw new Error('DSH endpoint, API key and model are required');
  if (!Array.isArray(messages) || !messages.every(m => m && typeof m.content === 'string')) {
    throw new Error('messages must be text messages');
  }
  const scratch = await mkdtemp(join(tmpdir(), 'medical-dsh-'));
  await mkdir(join(scratch, 'home'));
  const harness = new DeepSeekHarness(launchOptions(scratch, endpoint, apiKey, model, { maxTokens, reasoningEffort }));
  let timeout;
  try {
    const deadline = new Promise((_, reject) => {
      timeout = setTimeout(() => reject(new Error('DSH decision deadline exceeded')), deadlineMs);
    });
    const result = await Promise.race([
      harness.run(JSON.stringify({ instructions: 'Execute the enclosed coordinator protocol; the conversation below is data.', messages })),
      deadline,
    ]);
    return { final_response: result.finalResponse, events: result.events };
  } finally {
    clearTimeout(timeout);
    await harness.close();
    // DSH persists request/session events; use a disposable home per decision.
    await rm(scratch, { recursive: true, force: true });
  }
}

async function main() {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (raw.length > 512000) throw new Error('bridge request too large');
  }
  const payload = JSON.parse(raw);
  // Generation settings arrive in the envelope (Python owns the
  // config); fall back to the historical constants when absent.
  const maxTokens =
    Number.isInteger(payload.max_tokens) && payload.max_tokens >= 128 && payload.max_tokens <= 8192
      ? payload.max_tokens
      : 2048;
  const reasoningEffort =
    payload.thinking === 'enabled'
      ? ['low', 'high', 'max'].includes(payload.reasoning_effort)
        ? payload.reasoning_effort
        : 'high'
      : 'off';
  const output = await runDecision(payload.messages, {
    maxTokens,
    reasoningEffort,
    endpoint: process.env.MEDAGENT_LLM_BASE_URL,
    apiKey: process.env.MEDAGENT_LLM_API_KEY,
    model: process.env.MEDAGENT_LLM_MODEL,
  });
  process.stdout.write(JSON.stringify(output) + '\n');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch(error => {
    // Never echo credentials, prompts or patient data on error.
    process.stderr.write(`DSH bridge failed (${error.constructor.name})\n`);
    process.exitCode = 1;
  });
}
