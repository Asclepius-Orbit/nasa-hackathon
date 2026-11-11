/**
 * `pnpm e2e:real`: the end-to-end demo against the real pipeline (local only, not CI).
 *
 * Brings up broker, engine, backend and simulator with Docker Compose (a project of its own, `asclepius-e2e`, so its
 * database is thrown away with `down -v` and never touches a dev stack's volumes), waits until the backend is connected
 * to MQTT and the simulated harness has reported, builds the app with `--mode e2e-real` (real data source, demo crew),
 * runs the `real` Playwright project against it and always takes the stack down. Plain Node, so it runs the same on
 * Windows (Git Bash for `sh`) and Linux. Needs Docker, `sh`, and ports 1883, 8000 and 8001 free.
 *
 * Set E2E_REAL_KEEP=1 to leave the stack running afterwards (to look at it with `pnpm start`).
 */
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import path from 'node:path';

const uiDir = path.resolve(import.meta.dirname, '..');
const appDir = path.resolve(uiDir, '..');
const compose = path.join(appDir, 'docker-compose.yml');
const BACKEND = 'http://127.0.0.1:8000';
const ASTRONAUT = 'AST-01';
const READY_TIMEOUT_MS = 180_000;

const token = randomBytes(24).toString('hex');
const env = {
  ...process.env,
  SPINE_APP_UI_TOKEN: token,
  ASCLEPIUS_BACKEND_TOKEN: token,
  // One preflight baseline session is enough for the demo (the default plan asks for three).
  SPINE_APP_BASELINE_REQUIRED: '1',
};
const composeArgs = ['compose', '-p', 'asclepius-e2e', '-f', compose, '--profile', 'sim'];

function run(command, args, { cwd = uiDir, shell = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: 'inherit', shell });
    child.on('error', reject);
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`${command} ${args.join(' ')} exited with ${String(code)}`))));
  });
}

const pnpm = (args) => run('pnpm', args, { shell: process.platform === 'win32' });

async function until(label, probe) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  for (;;) {
    try {
      if (await probe()) return;
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}`);
    await new Promise((r) => setTimeout(r, 2000));
  }
}

const headers = { authorization: `Bearer ${token}`, 'x-astronaut-id': ASTRONAUT };

async function waitForStack() {
  await until('the backend to be connected to MQTT', async () => {
    const res = await fetch(`${BACKEND}/health`);
    return res.ok && (await res.json()).mqtt_connected === true;
  });
  await until('the simulated harness to report its status', async () => {
    const res = await fetch(`${BACKEND}/ui/crew/${ASTRONAUT}/devices`, { headers });
    return res.ok && (await res.json()).length > 0;
  });
}

let failure = null;
try {
  await run('sh', [path.join(appDir, 'mosquitto', 'setup.sh')], { cwd: appDir });
  await run('docker', [...composeArgs, 'down', '-v', '--remove-orphans']);
  await run('docker', [...composeArgs, 'up', '-d', '--build']);
  await waitForStack();
  await pnpm(['build:e2e-real']);
  await pnpm(['exec', 'playwright', 'test', '--project=real']);
} catch (error) {
  failure = error;
} finally {
  if (process.env.E2E_REAL_KEEP === '1') {
    console.log('E2E_REAL_KEEP=1: the stack is still running (docker compose -p asclepius-e2e down -v to stop it).');
  } else {
    await run('docker', [...composeArgs, 'down', '-v', '--remove-orphans']).catch((error) => {
      console.error(`could not take the stack down: ${error.message}`);
    });
  }
}
if (failure !== null) {
  console.error(failure.message);
  process.exitCode = 1;
}
