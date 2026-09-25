'use strict';

// External acceptance harness. Never included in the application or preload.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const { _electron } = require('../desktop/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const option = (key) => args.includes(key) ? args[args.indexOf(key) + 1] : null;
const output = path.resolve(option('--output') || 'artifacts/mvp1/desktop-smoke.json');
fs.mkdirSync(path.dirname(output), { recursive: true });
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-desktop-'));
const dataRoot = path.join(temporary, '合成 猫娘 数据');
const report = { schema_version: 1, stage: 'MVP1-actual-electron', status: 'RUNNING',
  environment: { platform: process.platform, arch: process.arch, node: process.version },
  source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  source_dirty: Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim()),
  harness_sha256: crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),
  real_model_calls: 0, real_search_calls: 0, windows_11: 'pending', cases: [], screenshots: [] };
const save = () => fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await predicate()) return; await pause(100); }
  throw new Error('Bounded acceptance wait expired');
}
async function check(name, action) {
  const record = { name, status: 'RUNNING' }; report.cases.push(record); save();
  const start = Date.now();
  try { await action(); record.status = 'PASS'; }
  catch (error) { record.status = 'FAIL'; record.error_type = error.name; throw error; }
  finally { record.elapsed_ms = Date.now() - start; save(); }
}
const env = { ...process.env };
for (const key of Object.keys(env)) {
  if (key.startsWith('AI_NEKO_') || key.startsWith('ELECTRON_') || key.startsWith('LANGSMITH_') ||
      ['PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS'].includes(key)) delete env[key];
}
env.AI_NEKO_DATA_DIR = dataRoot;
env.PYTHONUTF8 = '1';
if (process.platform === 'win32') {
  const profile = path.join(temporary, '合成 用户');
  fs.mkdirSync(path.join(profile, 'AppData', 'Local'), { recursive: true });
  env.USERPROFILE = profile;
  env.LOCALAPPDATA = path.join(profile, 'AppData', 'Local');
  env.APPDATA = path.join(profile, 'AppData', 'Roaming');
}
let executablePath = require('../desktop/node_modules/electron');
let launchArgs = [path.join(root, 'desktop')];
const archive = option('--archive');
if (archive) {
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const script = 'import sys; from pathlib import Path; from scripts.package_smoke import safe_extract; print(safe_extract(Path(sys.argv[1]), Path(sys.argv[2])))';
  const extracted = execFileSync(python, ['-c', script, path.resolve(archive), path.join(temporary, '解压 程序')],
    { cwd: root, encoding: 'utf8' }).trim();
  executablePath = path.join(extracted, 'ai-neko.exe'); launchArgs = [];
  report.archive_sha256 = crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex');
  report.build = JSON.parse(fs.readFileSync(path.join(extracted, 'build-info.json')));
  assert.equal(report.build.source.commit, report.source_commit);
  // The app receives only the system PATH; the test harness itself still uses Node.
  env.PATH = [path.join(env.SYSTEMROOT || 'C:\\Windows', 'System32'), env.SYSTEMROOT || 'C:\\Windows'].join(';');
}
report.executable_sha256 = crypto.createHash('sha256').update(fs.readFileSync(executablePath)).digest('hex');
let electronApp; let page; let modelCalls = 0;
const rendererErrors = [];
const model = http.createServer((request, response) => {
  const body = []; request.on('data', (chunk) => body.push(chunk));
  request.on('end', async () => {
    const value = JSON.parse(Buffer.concat(body).toString()); modelCalls += 1;
    const last = [...value.messages].reverse().find((m) => m.role === 'user')?.content || '';
    let pieces = last.includes('slow') ? Array(35).fill('合成慢速片段。') : ['合成回复：', '你好，', '本轮已收到。'];
    let deltas = pieces.map((content) => ({ content }));
    if (value.tools && !value.messages.some((m) => m.role === 'tool')) {
      deltas = [{ tool_calls: [{ index: 0, id: 'desktop-search', type: 'function',
        function: { name: 'search_web', arguments: JSON.stringify({ query: 'synthetic desktop guide' }) } }] }];
    }
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    for (const delta of deltas) {
      if (response.destroyed) return;
      response.write('data: ' + JSON.stringify({ choices: [{ index: 0, delta }] }) + '\n\n');
      await pause(last.includes('slow') ? 110 : 60);
    }
    if (!response.destroyed) response.end('data: [DONE]\n\n');
  });
});
async function launch() {
  electronApp = await _electron.launch({ executablePath, args: launchArgs, cwd: root, env, timeout: 45000 });
  page = await electronApp.firstWindow({ timeout: 45000 });
  const errors = rendererErrors;
  page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForLoadState('domcontentloaded');
  if (page.url().includes('/consent/')) {
    await page.locator('#accept').click();
  }
  await until(async () => {
    for (const candidate of electronApp.windows()) {
      if (candidate.url().includes('/renderer/')) { page = candidate; return true; }
    }
    return false;
  }, 45000);
  page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForSelector('#pet-stage[data-loaded="true"]', { timeout: 45000 });
  await page.waitForFunction(async () => (await window.aiNeko.status()).state === 'ready', undefined, { timeout: 45000 });
  await page.waitForSelector('body[data-chat-ready="true"]', { timeout: 45000 });
  return errors;
}
async function snap(name) {
  const target = path.join(path.dirname(output), name + '.png');
  await page.screenshot({ path: target, omitBackground: true });
  report.screenshots.push({ file: path.basename(target), sha256: crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex') });
}
async function quit() {
  if (await page.locator('#settings-panel').isHidden()) await page.locator('#open-settings').click();
  const closing = electronApp.waitForEvent('close', { timeout: 15000 });
  await page.locator('#exit-app').click();
  await until(() => !fs.existsSync(path.join(dataRoot, 'runtime', 'connection.json')), 15000);
  await closing;
  await electronApp.close().catch(() => {}); electronApp = null;
}
(async () => {
  await new Promise((resolve) => model.listen(0, '127.0.0.1', resolve));
  let errors;
  await check('consent_real_catgirl_and_owned_backend', async () => {
    errors = await launch();
    assert.equal(await page.locator('#pet-stage').isVisible(), true);
    const pixels = await page.locator('#pet-canvas').evaluate((canvas) => {
      const copy = document.createElement('canvas'); copy.width = canvas.width; copy.height = canvas.height;
      const ctx = copy.getContext('2d'); ctx.drawImage(canvas, 0, 0);
      const data = ctx.getImageData(0, 0, copy.width, copy.height).data;
      let visible = 0; for (let n = 3; n < data.length; n += 4) if (data[n] > 32) visible += 1;
      return { visible, total: copy.width * copy.height };
    });
    assert.ok(pixels.visible > 10000 && pixels.visible < pixels.total * 0.95);
    report.character_pixels = pixels;
    await snap('desktop-catgirl');
  });
  await check('renderer_sandbox_and_ipc_boundaries', async () => {
    const privileges = await page.evaluate(() => ({ node: typeof require, process: typeof process,
      token: Object.keys(window.aiNeko).some((key) => /token|exec|spawn/i.test(key)) }));
    assert.deepEqual(privileges, { node: 'undefined', process: 'undefined', token: false });
    const rejected = await page.evaluate(async () => {
      try { await window.aiNeko.request({ method: 'GET', path: 'http://example.com/' }); return false; }
      catch { return true; }
    });
    assert.equal(rejected, true);
    assert.equal(await page.evaluate(async () => {
      try { await window.aiNeko.openExternal('file:///etc/passwd'); return false; } catch { return true; }
    }), true);
  });
  await check('settings_keep_catgirl_visible_and_configure_synthetic_model', async () => {
    await page.locator('#open-settings').click();
    assert.equal(await page.locator('#pet-stage').isVisible(), true);
    await page.locator('#model-base-url').fill(`http://127.0.0.1:${model.address().port}/v1`);
    await page.locator('#model-name').fill('synthetic-desktop-model');
    await snap('desktop-settings');
    await page.locator('#save-settings').click();
    await page.waitForFunction(() => document.querySelector('#settings-panel').hidden && document.querySelector('#turn-status').textContent.includes('设置已保存'));
  });
  await check('incremental_reply_cancel_and_old_delta_rejection', async () => {
    await page.locator('#message-input').fill('slow synthetic desktop');
    await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => [...document.querySelectorAll('.assistant-output')].some((e) => e.textContent.includes('合成慢速片段')));
    const first = await page.locator('.assistant-output').last().innerText();
    await page.waitForFunction((prior) => [...document.querySelectorAll('.assistant-output')].at(-1).textContent.length > prior, first.length);
    assert.equal(await page.locator('#cancel-turn').isVisible(), true);
    assert.equal(await page.locator('#pet-stage').isVisible(), true);
    await snap('desktop-streaming');
    await page.locator('#cancel-turn').click();
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    const frozen = await page.locator('.assistant-output').last().innerText();
    await pause(650);
    assert.equal(await page.locator('.assistant-output').last().innerText(), frozen);
    assert.ok(frozen.length < '合成慢速片段。'.repeat(35).length);
  });
  await check('normal_reply_after_cancel_and_guide_error_feedback', async () => {
    await page.locator('#message-input').fill('hello synthetic desktop');
    await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => [...document.querySelectorAll('.assistant-output')].at(-1)?.textContent.includes('本轮已收到'));
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    await page.locator('#mode-guide').click();
    await page.locator('#message-input').fill('synthetic guide');
    await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => !document.querySelector('#settings-panel').hidden);
    assert.equal(await page.locator('#pet-stage').isVisible(), true);
    assert.ok(await page.locator('#settings-message').innerText());
    await snap('desktop-guide');
    await page.locator('#close-settings').click();
  });
  await check('fold_keeps_pet_and_preferences_persist', async () => {
    await page.locator('#close-chat').click();
    assert.equal(await page.locator('#chat-panel').isVisible(), false);
    assert.equal(await page.locator('#pet-stage').isVisible(), true);
    await page.locator('#show-chat').click();
    await page.evaluate(() => window.aiNeko.setPreferences({ scale: 0.9, alwaysOnTop: false }));
    assert.equal((await page.evaluate(() => window.aiNeko.getPreferences())).scale, 0.9);
  });
  await check('second_launch_reuses_single_instance', async () => {
    const before = fs.readFileSync(path.join(dataRoot, 'runtime', 'connection.json'), 'utf8');
    const second = spawn(executablePath, launchArgs, { cwd: root, env, stdio: 'ignore' });
    await until(() => second.exitCode !== null || second.signalCode !== null, 20000);
    assert.equal(second.exitCode, 0);
    assert.equal(fs.readFileSync(path.join(dataRoot, 'runtime', 'connection.json'), 'utf8'), before);
  });
  await check('quit_cleans_backend_and_restart_recovers_history', async () => {
    await quit();
    await launch();
    await page.waitForFunction(() => document.querySelector('#messages').textContent.includes('本轮已收到'));
    assert.equal((await page.evaluate(() => window.aiNeko.getPreferences())).scale, 0.9);
    await snap('desktop-restored');
    await quit();
  });
  await check('actual_host_crash_reaps_owned_backend_and_does_not_replay', async () => {
    await launch();
    await page.locator('#message-input').fill('slow host crash synthetic');
    await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => !document.querySelector('#cancel-turn').hidden &&
      [...document.querySelectorAll('.assistant-output')].at(-1)?.textContent.includes('合成慢速片段'));
    const connectionPath = path.join(dataRoot, 'runtime', 'connection.json');
    const owned = JSON.parse(fs.readFileSync(connectionPath, 'utf8'));
    const actualHostPID = await electronApp.evaluate(() => process.pid);
    const before = modelCalls;
    if (process.platform === 'win32') {
      // Deliberately kill only our host, leaving its backend alive to observe pipe EOF.
      execFileSync('taskkill', ['/PID', String(actualHostPID), '/F'], { stdio: 'ignore' });
    } else process.kill(actualHostPID, 'SIGKILL');
    await until(() => !fs.existsSync(connectionPath), 15000);
    await until(() => {
      try { process.kill(owned.pid, 0); return false; } catch (error) { return error.code === 'ESRCH'; }
    }, 15000);
    await electronApp.close().catch(() => {}); electronApp = null;
    await launch();
    await page.waitForFunction(() => document.querySelector('#messages').textContent.includes('slow host crash synthetic'));
    assert.equal(modelCalls, before);
    assert.equal(await page.locator('#cancel-turn').isVisible(), false);
    await quit();
  });
  assert.deepEqual(errors, []);
  report.status = 'PASS';
})().catch((error) => {
  console.error(error.stack);
  report.status = 'FAIL'; report.failure = { name: error.name, message: error.message.slice(0, 1200) };
  process.exitCode = 1;
}).finally(async () => {
  if (electronApp) await electronApp.close().catch(() => {});
  await new Promise((resolve) => model.close(resolve));
  report.synthetic_model_requests = modelCalls; report.finished_utc = new Date().toISOString();
  save();
  // Do not publish the disposable runtime files, credentials, conversation DB or profile.
  fs.rmSync(temporary, { recursive: true, force: true });
  console.log(JSON.stringify({ status: report.status, cases: report.cases.map(({ name, status }) => ({ name, status })), output }));
});
