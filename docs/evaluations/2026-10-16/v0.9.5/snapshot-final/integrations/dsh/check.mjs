import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const exec = promisify(execFile);
const scratch = await mkdtemp(join(tmpdir(), 'medical-dsh-check-'));
try {
  const manifest = require.resolve('@deepseek-ai/dsh/package.json');
  const pkg = JSON.parse(await readFile(manifest));
  const cli = join(dirname(manifest), 'lib/bin.js');
  const { stdout, stderr } = await exec(process.execPath, [cli, '--profile', 'sdk-minimal', '--patch', join(here, 'medical.patch.yml'), '--dump-config'], {
    cwd: scratch, env: { PATH: process.env.PATH, DSH_HOME: scratch, DEEPSEEK_API_KEY: 'installation-check-only' }, timeout: 20000,
  });
  const { default: yaml } = await import('js-yaml');
  // Inspect expressions as strings; never evaluate JavaScript while auditing config.
  const schema = yaml.DEFAULT_SCHEMA.extend([new yaml.Type('tag:yaml.org,2002:js', {kind: 'scalar', construct: value => value})]);
  const rows = yaml.load(stdout, {schema});
  if (!Array.isArray(rows)) throw new Error('Unexpected DSH config output: ' + stdout.slice(0, 600) + stderr.slice(0, 1000));
  const active = rows.filter(row => row.disabled !== true);
  const forbidden = active.filter(row => /tool-|terminal|subprocess|mcp-client|session-log-deepseek|plugin-package-inventory/.test(row.name));
  if (forbidden.length) throw new Error(`Forbidden plugins active: ${forbidden.map(row => row.id).join(',')}`);
  console.log(JSON.stringify({ dsh_version: pkg.version, active_plugins: active.map(row => row.id), model_facing_host_tools: 0 }, null, 2));
} finally {
  await rm(scratch, { recursive: true, force: true });
}
