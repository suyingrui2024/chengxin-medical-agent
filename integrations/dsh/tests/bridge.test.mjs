import { test } from 'node:test';
import assert from 'node:assert/strict';
import { launchOptions } from '../bridge.mjs';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const bridgePath = fileURLToPath(new URL('../bridge.mjs', import.meta.url));

function runBridge(input) {
  // The bridge must see no MEDAGENT_* keys unless a case passes them:
  // missing-config failures are then exercised without a real key.
  const env = { ...process.env };
  for (const key of ['MEDAGENT_LLM_BASE_URL', 'MEDAGENT_LLM_API_KEY', 'MEDAGENT_LLM_MODEL']) {
    delete env[key];
  }
  return spawnSync(process.execPath, [bridgePath], { input, env, encoding: 'utf8' });
}

test('bridge launches shipped profile with isolated home and excludes unrelated credentials', () => {
  process.env.UNRELATED_SECRET_TEST = 'must-not-inherit';
  const options = launchOptions('/tmp/test-medical-dsh', 'https://api.invalid', 'test-only', 'test-model');
  assert.equal(options.env.DSH_HOME, '/tmp/test-medical-dsh/home');
  assert.equal(options.env.UNRELATED_SECRET_TEST, undefined);
  assert.equal(options.env.DEEPSEEK_API_KEY, 'test-only');
  assert.equal(options.profile, 'sdk-minimal');
  assert.equal(options.reasoningEffort, 'off');
  assert.equal(options.patches.length, 1);
  delete process.env.UNRELATED_SECRET_TEST;
});

test('bridge stderr carries error class plus safe code, never the message', () => {
  const badJson = runBridge('not-json {{{');
  assert.equal(badJson.status, 1);
  assert.match(badJson.stderr, /DSH bridge failed \(SyntaxError: DSH_INVALID_JSON\)/);
  assert.equal(badJson.stderr.includes('not-json'), false);
});

test('bridge stderr tags oversize input and missing config without echoing input', () => {
  const big = runBridge('x'.repeat(512001));
  assert.equal(big.status, 1);
  assert.match(big.stderr, /DSH bridge failed \(Error: DSH_INPUT_TOO_LARGE\)/);
  const missing = runBridge(JSON.stringify({ messages: [{ role: 'user', content: 'hi' }] }));
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /DSH bridge failed \(Error: DSH_CONFIG_MISSING\)/);
});

test('medical overlay disables shell, subprocesses and log uploading', async () => {
  const { default: yaml } = await import('js-yaml');
  const rows = yaml.load(await readFile(new URL('../medical.patch.yml', import.meta.url), 'utf8'));
  for (const id of ['persistent-bash', 'persistent-pwsh', 'terminal-bash', 'terminal-pwsh',
    'subprocess', 'pty', 'session-log-deepseek', 'plugin-package-inventory-deepseek']) {
    assert.equal(rows.find(row => row.id === id)?.disabled, true, `${id} must be disabled`);
  }
});
