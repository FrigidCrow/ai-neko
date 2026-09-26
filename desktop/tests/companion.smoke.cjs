'use strict';
// External synthetic acceptance harness: no user microphone, screen, credentials or data.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { _electron } = require('../node_modules/playwright');
const root = path.resolve(__dirname, '../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-companion-'));
const args = process.argv.slice(2);
const option = (name) => args.includes(name) ? args[args.indexOf(name) + 1] : null;
const output = path.resolve(option('--output') || path.join(root, 'artifacts/mvp1/companion-ui-smoke.json'));
const screenshotPath = output.replace(/\.json$/, '') + '.png';
fs.mkdirSync(path.dirname(output), { recursive: true });
const report = { status: 'RUNNING', platform: process.platform, node: process.version,
  source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  source_dirty: Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim()),
  harness_sha256: crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'), real_model_calls: 0, real_audio_service_calls: 0,
  actual_user_microphone_captures: 0, actual_desktop_captures: 0, windows_11: 'pending', cases: [] };
const save = () => fs.writeFileSync(output, JSON.stringify(report, null, 2));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const calls = { models: [], asr: 0, tts: 0 };
let asrText = '合成语音问题，请看当前棋盘。';
let failHistoricalQuestion = false;
const mp3 = fs.readFileSync(path.join(__dirname, 'fixtures/synthetic-tone.mp3'));
const server = http.createServer((req, res) => {
  const chunks = []; req.on('data', (chunk) => chunks.push(chunk)); req.on('end', async () => {
    if (req.url === '/v1/audio/transcriptions') { calls.asr++; res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ text: asrText })); return; }
    if (req.url === '/v1/audio/speech') { calls.tts++; res.writeHead(200, { 'Content-Type': 'audio/mpeg' }); res.end(mp3); return; }
    if (req.url !== '/v1/chat/completions') { res.writeHead(404); res.end(); return; }
    const body = JSON.parse(Buffer.concat(chunks)); calls.models.push(body);
    const lastUser = body.messages.findLast((message) => message.role === 'user')?.content;
    const userText = Array.isArray(lastUser) ? lastUser.filter((part) => part.type === 'text').map((part) => part.text).join(' ') : lastUser || '';
    if (failHistoricalQuestion && userText.includes('合成失败历史重试问题')) {
      failHistoricalQuestion = false; res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'Synthetic provider failure' } })); return;
    }
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (body.tools && userText.includes('合成语音查规则') && !body.messages.some((message) => message.role === 'tool')) {
      res.end('data: ' + JSON.stringify({ choices: [{ delta: { tool_calls: [{ index: 0, id: 'synthetic-voice-search', type: 'function', function: { name: 'search_web', arguments: JSON.stringify({ query: 'synthetic latest game rules' }) } }] } }] }) + '\n\ndata: [DONE]\n\n');
      return;
    }
    const answer = userText.includes('合成实际听到测试') ? ['**第一句🐾已经听完。', '**\n第二句还没有听完。'] : ['合成场景建议：先观察。', '再决定下一步。'];
    for (const content of answer) { res.write('data: ' + JSON.stringify({ choices: [{ delta: { content } }] }) + '\n\n'); await pause(180); }
    res.end('data: [DONE]\n\n');
  });
});
let application; let page; const errors = [];
async function check(name, operation) {
  const value = { name, status: 'RUNNING' }; report.cases.push(value); save();
  try { await operation(); value.status = 'PASS'; } catch (error) { value.status = 'FAIL'; throw error; } finally { save(); }
}
async function waitForBackend(predicate, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await page.evaluate(predicate)) return;
    await pause(100);
  }
  throw new Error(`Backend condition timed out: ${String(predicate)}`);
}
const env = { ...process.env };
for (const key of Object.keys(env)) if (/^(AI_NEKO_|ELECTRON_|LANGSMITH_)/.test(key) || ['PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS'].includes(key)) delete env[key];
env.AI_NEKO_DATA_DIR = path.join(temporary, 'synthetic'); env.PYTHONUTF8 = '1';
if (process.platform === 'win32') {
  const profile = path.join(temporary, 'synthetic-user');
  fs.mkdirSync(path.join(profile, 'AppData', 'Local'), { recursive: true });
  env.USERPROFILE = profile;
  env.LOCALAPPDATA = path.join(profile, 'AppData', 'Local');
  env.APPDATA = path.join(profile, 'AppData', 'Roaming');
}
let executablePath = require('../node_modules/electron');
let launchArgs = [path.join(root, 'desktop'), '--use-fake-device-for-media-stream'];
const archive = option('--archive');
if (archive) {
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const extract = 'import sys; from pathlib import Path; from scripts.package_smoke import safe_extract; print(safe_extract(Path(sys.argv[1]), Path(sys.argv[2])))';
  const extracted = execFileSync(python, ['-c', extract, path.resolve(archive), path.join(temporary, 'unpacked')], { cwd: root, encoding: 'utf8' }).trim();
  executablePath = path.join(extracted, 'ai-neko.exe');
  launchArgs = ['--use-fake-device-for-media-stream'];
  report.archive_sha256 = crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex');
  report.build = JSON.parse(fs.readFileSync(path.join(extracted, 'build-info.json')));
  assert.equal(report.build.source.commit, report.source_commit);
  env.PATH = [path.join(env.SYSTEMROOT || 'C:\\Windows', 'System32'), env.SYSTEMROOT || 'C:\\Windows'].join(';');
}
report.executable_sha256 = crypto.createHash('sha256').update(fs.readFileSync(executablePath)).digest('hex');
(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  application = await _electron.launch({ executablePath, args: launchArgs, cwd: root, env, timeout: 45000 });
  report.runtime_versions = await application.evaluate(() => process.versions);
  page = await application.firstWindow(); await page.waitForLoadState('domcontentloaded');
  if (page.url().includes('/consent/')) await page.locator('#accept').click();
  for (let i = 0; i < 200; i++) { const found = application.windows().find((item) => item.url().includes('/renderer/')); if (found) { page = found; break; } await pause(100); }
  page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForSelector('body[data-chat-ready="true"]', { timeout: 45000 });
  await page.waitForSelector('#pet-stage[data-loaded="true"]', { timeout: 45000 });
  const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
  await check('native_permissions_deny_unarmed_microphone_camera_and_unselected_capture', async () => {
    const result = await page.evaluate(async () => {
      await window.aiNeko.microphone(false);
      const denied = async (constraints) => { try { const stream = await navigator.mediaDevices.getUserMedia(constraints); stream.getTracks().forEach((track) => track.stop()); return false; } catch { return true; } };
      const audioDenied = await denied({ audio: true, video: false });
      await window.aiNeko.microphone(true);
      const videoDenied = await denied({ video: true, audio: false });
      await window.aiNeko.microphone(false);
      let captureDenied = false;
      try { await window.aiNeko.captureVision(); } catch { captureDenied = true; }
      return { audioDenied, videoDenied, captureDenied };
    });
    assert.deepEqual(result, { audioDenied: true, videoDenied: true, captureDenied: true });
  });
  await check('persona_edit_and_versioned_reload', async () => {
    await page.locator('#open-settings').click();
    await page.waitForFunction(() => document.querySelector('#persona-status').textContent.includes('版本'));
    await page.locator('#persona-name').fill('合成小猫'); await page.locator('#persona-traits').fill('温柔、机灵');
    await page.locator('#persona-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector('#persona-status').textContent.includes('下一轮生效'));
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/persona'))).name, '合成小猫');
  });
  await check('memory_save_correct_forget_and_auto_extract_setting', async () => {
    await page.locator('#memory-content').fill('合成测试偏好：先给结论。');
    await page.locator('#memory-form').evaluate((form) => form.requestSubmit());
    await page.waitForSelector('.memory-card');
    await page.locator('.memory-card button').filter({ hasText: '查看依据' }).click();
    await page.waitForFunction(() => document.querySelector('.memory-evidence').textContent.includes('合成测试偏好：先给结论。'));
    await page.locator('.memory-card textarea').fill('合成测试偏好：简短解释。');
    await page.locator('.memory-card button').first().click();
    await page.waitForFunction(() => document.querySelector('#memory-status').textContent.includes('纠正'));
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/memories'))).memories[0].content, '合成测试偏好：简短解释。');
    await page.locator('.memory-card button').filter({ hasText: '遗忘' }).click(); await page.waitForSelector('.memory-card', { state: 'detached' });
    await page.locator('#memory-auto-extract').check();
    await page.waitForFunction(() => document.querySelector('#memory-status').textContent.includes('自动整理'));
    await page.locator('#memory-auto-extract').uncheck();
    await page.waitForFunction(() => document.querySelector('#memory-status').textContent.includes('关闭自动'));
  });
  await check('memory_snapshot_ui_requires_confirmation_and_preserves_corrections_and_forgetting', async () => {
    for (const content of ['快照偏好：喜欢慢节奏。', '快照遗忘：旧的事件。']) {
      await page.locator('#memory-content').fill(content); await page.locator('#memory-form').evaluate((form) => form.requestSubmit());
      await page.waitForFunction((text) => [...document.querySelectorAll('.memory-card textarea')].some((item) => item.value === text), content);
    }
    await page.locator('#create-memory-backup').click();
    await page.waitForFunction(() => document.querySelector('#memory-backup-status').textContent.includes('已创建'));
    const snapshotId = await page.locator('.backup-card').first().getAttribute('data-backup-id');
    assert.match(snapshotId, /^memory-[a-f0-9]{32}\.sqlite$/);
    await page.locator('#persona-name').fill('快照之后的名字'); await page.locator('#persona-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector('#persona-status').textContent.includes('下一轮'));
    // Locate by persisted content rather than relying on SQLite row order.
    const memoryRows = await page.evaluate(() => window.aiNekoChat.api('/api/memories'));
    const correctId = memoryRows.memories.find((item) => item.content.includes('快照偏好')).id;
    const forgetId = memoryRows.memories.find((item) => item.content.includes('快照遗忘')).id;
    await page.evaluate(async ({ correctId, forgetId }) => {
      await window.aiNekoChat.api(`/api/memories/${correctId}`, { method: 'PUT', body: { content: '快照偏好：现在喜欢快节奏。' } });
      await window.aiNekoChat.api(`/api/memories/${forgetId}`, { method: 'DELETE' });
    }, { correctId, forgetId });
    const revision = (await page.evaluate(() => window.aiNekoChat.api('/api/memory/backups'))).revision;
    await page.locator('#refresh-memory-backups').click();
    await page.waitForFunction((revision) => document.querySelector('#memory-backup-list').dataset.revision === String(revision), revision);
    await page.locator('.backup-card').first().getByRole('button', { name: '恢复', exact: true }).click();
    await page.waitForSelector('#memory-backup-confirmation:not([hidden])');
    assert.ok((await page.locator('#memory-backup-confirm-detail').innerText()).includes(snapshotId));
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/persona'))).name, '快照之后的名字');
    await page.locator('#cancel-memory-backup').click();
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/persona'))).name, '快照之后的名字');
    await page.locator('.backup-card').first().getByRole('button', { name: '恢复', exact: true }).click();
    await page.evaluate(async (id) => window.aiNekoChat.api(`/api/memories/${id}`, { method: 'PUT', body: { content: '快照偏好：现在喜欢快节奏。' } }), correctId);
    await page.locator('#confirm-memory-backup').click();
    await page.waitForFunction(() => document.querySelector('#memory-backup-status').textContent.includes('记忆已经变化'));
    assert.equal(await page.locator('#memory-backup-confirmation').isHidden(), true);
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/persona'))).name, '快照之后的名字');
    await page.locator('.backup-card').first().getByRole('button', { name: '恢复', exact: true }).click();
    await page.locator('#confirm-memory-backup').click();
    await page.waitForFunction(() => document.querySelector('#memory-backup-status').textContent.includes('已恢复'));
    assert.equal(await page.locator('#persona-name').inputValue(), '合成小猫');
    const restored = await page.evaluate(() => window.aiNekoChat.api('/api/memories'));
    assert.equal(restored.memories.find((item) => item.id === correctId)?.content, '快照偏好：现在喜欢快节奏。');
    assert.equal(restored.memories.some((item) => item.id === forgetId), false);
    const visible = await page.locator('.memory-card textarea').evaluateAll((items) => items.map((item) => item.value));
    assert.ok(visible.includes('快照偏好：现在喜欢快节奏。')); assert.ok(!visible.some((text) => text.includes('快照遗忘')));
    await page.locator('.backup-card').first().getByRole('button', { name: '删除快照', exact: true }).click();
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/memory/backups'))).backups.length, 1);
    await page.locator('#confirm-memory-backup').click();
    await page.waitForFunction(() => document.querySelector('#memory-backup-status').textContent.includes('已删除'));
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/memory/backups'))).backups.length, 0);
    report.memory_snapshot_ui = { explicit_restore_confirmation: true, stale_revision_did_not_restore: true, restored_persona: true, correction_preserved: true, forgotten_fact_absent: true, explicit_delete_confirmation: true };
  });
  await check('damaged_owned_snapshot_is_visible_deletable_and_cannot_restore', async () => {
    await page.locator('#create-memory-backup').click();
    await page.waitForSelector('.backup-card');
    await page.waitForFunction(() => !document.querySelector('#create-memory-backup').disabled);
    const id = await page.locator('.backup-card').first().getAttribute('data-backup-id');
    // Alter only this harness's freshly created synthetic snapshot. Its owner
    // stays readable, while the persona JSON makes restoration invalid.
    const { DatabaseSync } = require('node:sqlite');
    const fixtureDB = new DatabaseSync(path.join(env.AI_NEKO_DATA_DIR, 'backups', id));
    try { fixtureDB.exec("UPDATE memory_scopes SET persona='invalid synthetic JSON'"); } finally { fixtureDB.close(); }
    await page.locator('#refresh-memory-backups').click();
    await page.waitForFunction(() => document.querySelector('.backup-card button[data-unrestorable="true"]')?.disabled);
    assert.ok((await page.locator('.backup-card').innerText()).includes('无法恢复'));
    assert.equal(await page.locator('.backup-card').getByRole('button', { name: '删除快照', exact: true }).isEnabled(), true);
    await page.locator('.backup-card').getByRole('button', { name: '删除快照', exact: true }).click();
    const evidencePath = screenshotPath.replace(/\.png$/, '-snapshots.png');
    await page.locator('#memory-backup-confirmation').scrollIntoViewIfNeeded();
    await page.screenshot({ path: evidencePath, omitBackground: true });
    report.snapshot_screenshot = path.basename(evidencePath);
    report.snapshot_screenshot_sha256 = crypto.createHash('sha256').update(fs.readFileSync(evidencePath)).digest('hex');
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/memory/backups'))).backups.length, 1);
    await page.locator('#confirm-memory-backup').click();
    await page.waitForSelector('.backup-card', { state: 'detached' });
    assert.equal((await page.evaluate(() => window.aiNekoChat.api('/api/memory/backups'))).backups.length, 0);
  });
  await check('configure_independent_model_and_audio_services', async () => {
    for (const [id, value] of [['asr-base-url', endpoint], ['asr-model', 'synthetic-asr'], ['tts-base-url', endpoint], ['tts-model', 'synthetic-tts'], ['tts-voice', 'synthetic-voice']]) await page.locator(`#${id}`).fill(value);
    await page.locator('#voice-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector('#voice-config-status').textContent.includes('已保存'));
    await page.locator('#model-base-url').fill(endpoint); await page.locator('#model-name').fill('synthetic-vision');
    await page.locator('#settings-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector('#settings-panel').hidden);
  });
  await check('default_on_demand_mode_and_new_session_keep_explicit_choice', async () => {
    assert.equal(await page.locator('#mode-guide').getAttribute('aria-pressed'), 'true');
    assert.ok((await page.locator('#mode-guide').innerText()).includes('按需联网'));
    assert.equal(await page.locator('#notice').isHidden(), true);
    await page.locator('#mode-chat').click(); await page.locator('#new-session').click();
    assert.equal(await page.locator('#mode-chat').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#mode-hint').innerText(), '不联网搜索');
    await page.locator('#mode-guide').click(); await page.locator('#new-session').click();
    assert.equal(await page.locator('#mode-guide').getAttribute('aria-pressed'), 'true');
  });
  await check('selected_synthetic_window_preview_and_per_turn_fresh_image', async () => {
    await application.evaluate(async ({ BrowserWindow, desktopCapturer, nativeImage }) => {
      const fixture = new BrowserWindow({ show: false, width: 680, height: 420, webPreferences: { sandbox: true, partition: 'ai-neko-synthetic-fixture' } });
      await fixture.loadURL('data:text/html,<body style="background:%23adc;color:%23234;font-size:36px">SYNTHETIC GAME<br>Round 8 / Coins 20<br><canvas width="320" height="180"></canvas></body>');
      globalThis.syntheticVisionCalls = 0;
      desktopCapturer.getSources = async (options) => { globalThis.syntheticVisionCalls++; return [{ id: 'window:98765:0', name: 'Synthetic game only', thumbnail: options.thumbnailSize.width ? await fixture.webContents.capturePage() : nativeImage.createEmpty() }]; };
    });
    await page.locator('#open-settings').click(); await page.locator('#refresh-vision').click();
    await page.locator('#vision-source').selectOption('window:98765:0'); await page.locator('#vision-enabled').check();
    await page.waitForSelector('#vision-preview:not([hidden])');
    await page.locator('#close-settings').click(); await page.locator('#speak-replies').check();
    await page.locator('#message-input').fill('合成画面提问'); await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector('body').dataset.speaking === 'true');
    const last = calls.models.at(-1).messages.findLast((item) => item.role === 'user').content;
    assert.ok(Array.isArray(last) && last.some((part) => part.type === 'image_url'));
    assert.equal(await application.evaluate(() => globalThis.syntheticVisionCalls), 3);
    assert.ok(calls.models.some((request) => request.tools?.some((tool) => tool.function?.name === 'search_web')));
    assert.ok(calls.models.every((request) => !request.messages.some((message) => message.role === 'tool')));
    assert.equal(await page.locator('#notice').isHidden(), true);
    report.on_demand_plain_question = { tool_schemas_available: true, search_calls: 0, missing_search_key_did_not_block: true };
  });
  await check('real_web_audio_playback_cancel_stops_queue', async () => {
    const before = calls.tts;
    const start = Date.now(); await page.locator('#stop-audio').click();
    await page.waitForFunction(() => document.querySelector('body').dataset.speaking === 'false');
    report.stop_observed_ms = Date.now() - start;
    await pause(650); assert.equal(calls.tts, before);
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    await waitForBackend(async () => { const id = localStorage.getItem('ai-neko.desktop.last-session'); return (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-1).audio_playback?.some((segment) => segment.state === 'stopped'); });
  });
  await check('fake_microphone_asr_image_chat_sentence_tts_loop', async () => {
    await page.locator('#record-voice').click();
    await page.waitForFunction(() => document.querySelector('#record-voice').getAttribute('aria-pressed') === 'true');
    await pause(700); await page.locator('#record-voice').click();
    await page.waitForFunction(() => document.querySelector('#media-status').textContent.includes('合成语音问题'));
    await page.waitForFunction(() => document.querySelector('body').dataset.speaking === 'true');
    assert.equal(calls.asr, 1); assert.ok(calls.tts >= 2);
    const last = calls.models.at(-1).messages.findLast((item) => item.role === 'user').content;
    assert.ok(Array.isArray(last) && last.some((part) => part.type === 'image_url'));
    await page.locator('#stop-audio').click();
  });
  await check('default_voice_image_can_request_search_and_reports_actual_missing_key', async () => {
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    assert.equal(await page.locator('#mode-guide').getAttribute('aria-pressed'), 'true');
    const before = calls.models.length;
    asrText = '合成语音查规则，需要查询最新规则。';
    await page.locator('#record-voice').click();
    await page.waitForFunction(() => document.querySelector('#record-voice').getAttribute('aria-pressed') === 'true');
    await pause(400); await page.locator('#record-voice').click();
    await page.waitForFunction(() => document.querySelector('#notice-text').textContent.includes('Tavily') && !document.querySelector('#notice').hidden);
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    const requests = calls.models.slice(before);
    const planner = requests.find((request) => request.tools?.some((tool) => tool.function?.name === 'search_web'));
    assert.ok(planner);
    assert.ok(planner.messages.findLast((message) => message.role === 'user').content.some((part) => part.type === 'image_url'));
    assert.ok(requests.some((request) => !request.tools && request.messages.some((message) => typeof message.content === 'string' && message.content.includes('search_key_missing'))));
    const events = await page.evaluate(async () => {
      const id = localStorage.getItem('ai-neko.desktop.last-session');
      const turn = (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-1);
      return (await window.aiNekoChat.api(`/api/sessions/${id}/turns/${turn.turn_id || turn.id}/events?after=0`)).events;
    });
    assert.ok(events.some((event) => event.type === 'tool' && event.name === 'search_web' && event.error === 'search_key_missing'));
    assert.equal(await page.locator('#settings-panel').isHidden(), true);
    report.voice_search = { default_mode: 'on_demand', image_and_tool_schemas: true, actual_tool_error: 'search_key_missing' };
    await page.locator('#stop-audio').click();
    asrText = '合成语音问题，请看当前棋盘。';
  });
  await check('opening_history_preserves_explicit_search_permission', async () => {
    await page.locator('#mode-chat').click();
    await page.locator('#open-history').click();
    await page.locator('#session-list .session-item').first().click();
    await page.waitForFunction(() => !document.querySelector('#chat-panel').hidden && document.body.dataset.sessionLoading === 'false');
    assert.equal(await page.locator('#mode-chat').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#mode-hint').innerText(), '不联网搜索');
    await page.locator('#mode-guide').click();
  });
  await check('failed_history_retry_uses_current_chat_only_permission', async () => {
    assert.equal(await page.locator('#mode-guide').getAttribute('aria-pressed'), 'true');
    failHistoricalQuestion = true;
    await page.locator('#message-input').fill('合成失败历史重试问题');
    await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => ![...document.querySelectorAll('.retry-turn')].at(-1).hidden && document.querySelector('#cancel-turn').hidden);
    await page.locator('#mode-chat').click();
    await page.locator('#open-history').click(); await page.locator('#session-list .session-item').first().click();
    await page.waitForFunction(() => !document.querySelector('#chat-panel').hidden && document.body.dataset.sessionLoading === 'false');
    const before = calls.models.length;
    const beforeReplies = await page.locator('.assistant-output').count();
    await page.locator('.retry-turn').last().click();
    await page.waitForFunction((count) => document.querySelector('#cancel-turn').hidden && [...document.querySelectorAll('.assistant-output')].length === count + 1 && [...document.querySelectorAll('.assistant-output')].at(-1).textContent.includes('再决定下一步。'), beforeReplies);
    assert.equal(await page.locator('#mode-chat').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#mode-hint').innerText(), '不联网搜索');
    const retried = calls.models.slice(before);
    assert.equal(retried.length, 1); assert.ok(retried.every((request) => !request.tools));
    const turns = await page.evaluate(async () => { const id = localStorage.getItem('ai-neko.desktop.last-session'); return (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns; });
    assert.equal(turns.at(-2).status, 'error'); assert.equal(turns.at(-2).error, 'provider_http_error'); assert.equal(turns.at(-2).guide, true);
    assert.equal(turns.at(-1).status, 'completed'); assert.equal(turns.at(-1).guide, false);
    report.history_retry = { original_guide: true, retry_guide: false, tool_schemas_sent: false };
    await page.locator('#stop-audio').click(); await page.locator('#mode-guide').click();
  });
  await check('folded_panel_voice_keeps_playing_without_false_display_ack', async () => {
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    asrText = '合成实际听到测试';
    await page.locator('#close-chat').click();
    const trigger = () => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((window) => window.webContents.getURL().includes('/renderer/')).webContents.send('ai-neko:action', 'voice-toggle'));
    await trigger();
    await page.waitForFunction(() => document.querySelector('#record-voice').getAttribute('aria-pressed') === 'true');
    await pause(400); await trigger();
    await page.waitForFunction(() => document.querySelector('body').dataset.speaking === 'true');
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    const turn = await page.evaluate(async () => {
      const id = localStorage.getItem('ai-neko.desktop.last-session');
      return (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-1);
    });
    assert.equal(turn.status, 'completed'); assert.equal(turn.ack_seq, 0);
    await waitForBackend(async () => { const id = localStorage.getItem('ai-neko.desktop.last-session'); const audio = (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-1).audio_playback; return audio?.some((segment) => segment.state === 'completed') && audio.some((segment) => segment.state === 'started'); });
    await page.evaluate(() => window.aiNekoCompanion.stopSpeech());
    // Submit while still hidden. The submit path must flush the stopped receipt
    // before the backend builds the next model's confirmed-history context.
    await page.evaluate(() => window.aiNekoChat.submitText('合成核对已听到的内容'));
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    const prior = await page.evaluate(async () => { const id = localStorage.getItem('ai-neko.desktop.last-session'); return (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-2); });
    report.partial_heard_observation = prior;
    assert.equal(prior.ack_seq, 0); assert.equal(prior.heard_text, '第一句🐾已经听完。');
    assert.ok(prior.audio_playback.some((segment) => segment.state === 'stopped'));
    const context = calls.models.at(-1).messages.filter((message) => message.role === 'assistant').map((message) => message.content).join('\n');
    assert.ok(context.includes('第一句🐾已经听完。')); assert.ok(!context.includes('第二句还没有听完。'));
    report.heard_context = { first_sentence: prior.heard_text, second_sentence_absent: true, display_ack: prior.ack_seq, actual_playback: prior.audio_playback };
    await page.evaluate(() => window.aiNekoCompanion.stopSpeech());
    asrText = '合成语音问题，请看当前棋盘。';
  });
  await check('hidden_complete_playback_confirms_both_sentences_without_display_ack', async () => {
    await page.evaluate(() => window.aiNekoChat.submitText('合成完整播放测试'));
    await waitForBackend(async () => { const id = localStorage.getItem('ai-neko.desktop.last-session'); const turn = (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-1); return turn.audio_playback?.filter((segment) => segment.state === 'completed').length === 2; });
    const turn = await page.evaluate(async () => { const id = localStorage.getItem('ai-neko.desktop.last-session'); return (await window.aiNekoChat.api(`/api/sessions/${id}`)).turns.at(-1); });
    assert.equal(turn.ack_seq, 0); assert.equal(turn.heard_text, '合成场景建议：先观察。再决定下一步。');
    report.full_heard = { heard_text: turn.heard_text, display_ack: turn.ack_seq, completed_segments: turn.audio_playback.length };
    await page.locator('#show-chat').click();
  });
  await check('vision_off_revokes_capture_and_does_not_fallback', async () => {
    await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden);
    await page.locator('#open-settings').click(); await page.locator('#vision-enabled').uncheck();
    await page.waitForSelector('#vision-preview', { state: 'hidden' });
    const count = await application.evaluate(() => globalThis.syntheticVisionCalls);
    await page.locator('#close-settings').click(); await page.locator('#speak-replies').uncheck();
    const beforeReplies = await page.locator('.assistant-output').count();
    await page.locator('#message-input').fill('关闭观察后的合成文字问题'); await page.locator('#message-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction((count) => document.querySelector('#cancel-turn').hidden && [...document.querySelectorAll('.assistant-output')].length === count + 1 && [...document.querySelectorAll('.assistant-output')].at(-1).textContent.includes('再决定下一步。'), beforeReplies);
    assert.equal(await application.evaluate(() => globalThis.syntheticVisionCalls), count);
    const last = calls.models.at(-1).messages.findLast((item) => item.role === 'user').content;
    assert.equal(typeof last, 'string');
    await page.screenshot({ path: screenshotPath, omitBackground: true });
  });
  assert.deepEqual(errors, []); report.status = 'PASS';
})().catch(async (error) => { report.status = 'FAIL'; report.error = error.stack; if (page && !page.isClosed()) { report.ui_state = await page.evaluate(() => Object.fromEntries(['memory-status', 'persona-status', 'voice-config-status', 'media-status', 'notice-text'].map((id) => [id, document.getElementById(id)?.textContent]))).catch(() => ({})); } process.exitCode = 1; console.error(error.stack); }).finally(async () => {
  if (application) await application.close().catch(() => {});
  server.closeAllConnections(); await new Promise((resolve) => server.close(resolve));
  report.screenshot = path.basename(screenshotPath);
  if (fs.existsSync(screenshotPath)) report.screenshot_sha256 = crypto.createHash('sha256').update(fs.readFileSync(screenshotPath)).digest('hex');
  report.synthetic_calls = { model: calls.models.length, asr: calls.asr, tts: calls.tts }; report.renderer_errors = errors; save();
  fs.rmSync(temporary, { recursive: true, force: true }); console.log(JSON.stringify(report));
});
