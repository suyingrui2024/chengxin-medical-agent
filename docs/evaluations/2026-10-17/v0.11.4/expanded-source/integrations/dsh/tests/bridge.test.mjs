import { test } from 'node:test';
import assert from 'node:assert/strict';
import { launchOptions } from '../bridge.mjs';
import { readFile } from 'node:fs/promises';

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

test('medical overlay disables shell, subprocesses and log uploading', async () => {
  const { default: yaml } = await import('js-yaml');
  const rows = yaml.load(await readFile(new URL('../medical.patch.yml', import.meta.url), 'utf8'));
  for (const id of ['persistent-bash', 'persistent-pwsh', 'terminal-bash', 'terminal-pwsh',
    'subprocess', 'pty', 'session-log-deepseek', 'plugin-package-inventory-deepseek']) {
    assert.equal(rows.find(row => row.id === id)?.disabled, true, `${id} must be disabled`);
  }
});
