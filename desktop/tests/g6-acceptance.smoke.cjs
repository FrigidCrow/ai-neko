'use strict';
// Actual UI acceptance. Offline seeding creates only synthetic unadopted docs;
// all preferences, adoption, sessions and match changes use visible controls.
// --archive launches the extracted Windows executable, never source Electron.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { _electron } = require('../node_modules/playwright');

const root = path.resolve(__dirname, '../..');
const args = process.argv.slice(2);
function option(name) {
  const index = args.indexOf(name);
  if (index < 0) return null;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(name + ' requires a value');
  return args[index + 1];
}
const archive = option('--archive');
const output = path.resolve(option('--output') || path.join(root, 'artifacts/guides-evaluation/g6-electron.json'));
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-g6-electron-'));
const dataRoot = path.join(temporary, '合成 数据');
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
const hash = (file) => sha(fs.readFileSync(file));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const questions = ['灯芯是什么？', '航灯是什么？', '潮环是什么？', '泊印是什么？', '雾标是什么？'];
const preference = '讲解灯芯、航灯、潮环、泊印和雾标时，我偏好先给结论，再解释依据。';
const searchEndpoint = 'https://synthetic-search.invalid';
const tracked = ['desktop/tests/g6-acceptance.smoke.cjs', 'desktop/main.cjs', 'desktop/lib/backend.cjs',
  'desktop/renderer/app.js', 'desktop/renderer/guide-panel.js', 'desktop/renderer/companion.js', 'desktop/renderer/index.html',
  'src/ai_neko/runtime/service.py', 'src/ai_neko/runtime/guides.py', 'src/ai_neko/runtime/matches.py',
  'src/ai_neko/runtime/match_store.py', 'src/ai_neko/chat/graph.py', 'src/ai_neko/memory/guide_retrieval.py',
  'scripts/package_smoke.py', 'uv.lock', 'desktop/package-lock.json'];
const report = {
  status: 'RUNNING', started_at: new Date().toISOString(), platform: process.platform, node: process.version,
  execution: archive ? 'windows_extracted_executable' : 'source_electron',
  packaged_windows: 'NOT_RUN', windows_11_hardware: 'NOT_RUN', real_model_quality: 'NOT_RUN',
  real_provider_calls: 0, actual_user_microphone_captures: 0, actual_desktop_captures: 0,
  user_mutation_api_shortcuts: 0, media_races: 'Separate G5 harness; not rerun here',
  source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  source_dirty: Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim()),
  source_hashes: Object.fromEntries(tracked.map((name) => [name, hash(path.join(root, name))])),
  questions, preference, search_endpoint: searchEndpoint, temporary_fixture: temporary, fixture_retained: true,
  cases: [], launches: [], questions_observed: [], screenshots: [], model_requests: [], provider_requests: [], backend_requests: [],
};
fs.mkdirSync(path.dirname(output), { recursive: true });
const save = () => fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
const env = { ...process.env };
for (const key of Object.keys(env)) {
  if (/^(AI_NEKO_|ELECTRON_|LANGSMITH_)/.test(key) || ['PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS'].includes(key)) delete env[key];
}
env.AI_NEKO_DATA_DIR = dataRoot; env.PYTHONUTF8 = '1';
if (process.platform === 'win32') {
  const profile = path.join(temporary, 'synthetic-user');
  fs.mkdirSync(path.join(profile, 'AppData', 'Local'), { recursive: true });
  env.USERPROFILE = profile; env.LOCALAPPDATA = path.join(profile, 'AppData', 'Local');
  env.APPDATA = path.join(profile, 'AppData', 'Roaming');
}
const bootstrap = `
import json, sys
from datetime import datetime, timezone
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.service import MemoryService
memory=MemoryService(initialize_data_root(sys.argv[1]))
try:
    docs=[]
    texts=[('A','灯芯是潮灯守卫。\\n\\n航灯是雾岸领航员。\\n\\n潮环是港湾屏障。\\n\\n泊印是码头通行标记。\\n\\n雾标是海面方向标记。'),
           ('B','灯芯是岩港信使。\\n\\n航灯是远岸巡航员。')]
    for name,text in texts:
        url=f'https://guides.example.test/g6/{name.lower()}'
        doc=memory.guides.ingest({'status':'read','completeness':'full','completeness_reasons':[],
          'text':'合成说明 1.0\\n\\n'+text,'url':url,'original_url':url,'final_url':url,
          'title':f'G6 合成攻略 {name}','retrieved_at':datetime.now(timezone.utc).isoformat(),
          'content_date':None,'headings':[]}, game='synthetic-g6',platform='pc',mode='notes',
          game_version='1.0',version_basis='合成原文明确标注 1.0')
        docs.append(doc)
    print(json.dumps({'scope':memory.scope,'documents':docs,'control':memory.guides.control_snapshot()},ensure_ascii=False))
finally:
    memory.close()
`;
let application; let page; let fixture; let phase = 'prepare';
let executablePath = require('../node_modules/electron');
let launchArgs = [path.join(root, 'desktop')]; let launchCwd = root;
const errors = [], modelCalls = [];
function encodedJSON(body, key) {
  for (const message of body.messages || []) {
    if (typeof message.content !== 'string') continue;
    const offset = message.content.indexOf('{'); if (offset < 0) continue;
    try { const value = JSON.parse(message.content.slice(offset)); if (key in value) return value; } catch { /* plain user message */ }
  }
  return null;
}
const server = http.createServer((request, response) => {
  const chunks = []; request.on('data', (chunk) => chunks.push(chunk));
  request.on('end', async () => {
    const received_at = Date.now();
    report.provider_requests.push({ phase, method: request.method, path: request.url, received_at });
    try {
      if (request.url !== '/v1/chat/completions' || request.method !== 'POST') {
        errors.push('Unexpected synthetic provider request: ' + request.method + ' ' + request.url);
        response.writeHead(503, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ error: 'Unexpected search/read/audio request' })); return;
      }
      const body = JSON.parse(Buffer.concat(chunks));
      const evidence = encodedJSON(body, 'sources');
      const context = encodedJSON(body, 'match_context')?.match_context;
      const oldQuestion = [...(body.messages || [])].reverse().find((item) => item.role === 'user' && /^我现在有 \d+ 金币。灯芯是什么？$/.test(item.content));
      const oldRound = oldQuestion ? Number(oldQuestion.content.match(/\d+/)[0]) - 40 : null;
      const answer = phase === 'ten_old_match_turns'
        ? `旧局建议标记${oldRound}：仅供这一合成旧局复盘。`
        : evidence?.local_retrieval?.status === 'sufficient'
          ? `${evidence.sources[0].text} [S1]。`
          : '合成回答：这里只核对上下文与生命周期，不判断游戏建议质量。';
      const call = { phase, body, evidence, match_context: context, answer, received_at, sha256: sha(JSON.stringify(body)) };
      modelCalls.push(call);
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      response.write('data: ' + JSON.stringify({ choices: [{ delta: { content: answer } }] }) + '\n\n');
      await pause(60); response.end('data: [DONE]\n\n');
    } catch (error) { errors.push(error.message); response.destroy(); }
  });
});
async function until(predicate, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await predicate()) return; await pause(80); }
  throw new Error('Timed out: ' + label);
}
async function get(route) {
  const deadline = Date.now() + 20000;
  for (;;) {
    const response = await page.evaluate(async (url) => {
      try { return { value: await window.aiNekoChat.api(url) }; }
      catch (error) { return { error: { status: error.status, code: error.code, message: error.message } }; }
    }, route);
    if (!response.error) return response.value;
    if (response.error.status !== 409 || Date.now() > deadline) throw new Error(route + ': ' + JSON.stringify(response.error));
    await pause(80);
  }
}
async function sessionId() { return page.evaluate(() => localStorage.getItem('ai-neko.desktop.last-session')); }
async function session() { const id = await sessionId(); return id ? get('/api/sessions/' + id) : { turns: [] }; }
async function matches() { return get('/api/sessions/' + await sessionId() + '/matches'); }
async function captureRequests() {
  if (application) report.backend_requests.push(...await application.evaluate(() => globalThis.g6Requests?.splice(0) || []).catch(() => []));
}
async function screenshot(label) {
  const file = path.join(path.dirname(output), path.basename(output, '.json') + '-' + label + '.png');
  await page.screenshot({ path: file, omitBackground: true }); report.screenshots.push({ file: path.basename(file), sha256: hash(file) });
}
async function check(name, operation) {
  phase = name; if (application) await application.evaluate((_, value) => { globalThis.g6Phase = value; }, name);
  const item = { name, status: 'RUNNING', started_at: new Date().toISOString() }; report.cases.push(item); save();
  try { item.evidence = await operation() || {}; item.status = 'PASS'; }
  catch (error) { item.status = 'FAIL'; item.error = error.stack; throw error; }
  finally { await captureRequests(); save(); }
}
async function launch() {
  application = await _electron.launch({ executablePath, args: launchArgs, cwd: launchCwd, env, timeout: 60000 });
  const runtime = await application.evaluate(({ app }) => ({ pid: process.pid, executable: process.execPath, packaged: app.isPackaged, app_path: app.getAppPath(), versions: process.versions }));
  report.launches.push(runtime);
  assert.equal(runtime.packaged, Boolean(archive));
  if (archive) assert.equal(path.resolve(runtime.executable).toLowerCase(), path.resolve(executablePath).toLowerCase());
  page = await application.firstWindow(); await page.waitForLoadState('domcontentloaded');
  if (page.url().includes('/consent/')) await page.locator('#accept').click();
  await until(() => { const found = application.windows().find((item) => item.url().includes('/renderer/')); if (!found) return false; page = found; return true; }, 'renderer window');
  page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForSelector('body[data-chat-ready="true"]', { timeout: 60000 });
  await page.waitForSelector('#pet-stage[data-loaded="true"]', { timeout: 45000 });
  await application.evaluate(({ app }) => {
    const load = process.getBuiltinModule('module').createRequire(process.getBuiltinModule('path').join(app.getAppPath(), 'main.cjs'));
    const { OwnedBackend } = load('./lib/backend.cjs'); const original = OwnedBackend.prototype.request;
    globalThis.g6Requests = []; globalThis.g6Phase = 'launch';
    OwnedBackend.prototype.request = async function (request) {
      const body = request.encoded ? JSON.parse(request.encoded) : undefined;
      const entry = { phase: globalThis.g6Phase, method: request.method, path: request.path, body, started_at: Date.now() };
      globalThis.g6Requests.push(entry);
      try {
        const result = await original.call(this, request); entry.status = result.status;
        if (request.method !== 'GET' || /\/events\?/.test(request.path) || result.status >= 400) entry.response = result.body;
        entry.finished_at = Date.now(); return result;
      } catch (error) { entry.error = error.message; throw error; }
    };
  });
}
async function openGuides() {
  if (await page.locator('#guide-panel').isHidden()) await page.locator('#open-guides').click();
  await page.waitForSelector('#guide-library-list .guide-card');
  await page.waitForFunction(() => document.querySelector('#guide-panel').getAttribute('aria-busy') === 'false');
}
async function closeGuides() { if (await page.locator('#guide-panel').isVisible()) await page.locator('#close-guides').click(); }
async function adopt(doc) {
  await openGuides(); await page.locator(`.guide-card[data-guide-id="${doc.guide_id}"] .guide-select`).click();
  await page.waitForFunction(() => document.querySelector('#guide-status').textContent.includes('请确认游戏条件'));
  await page.locator('#guide-adopt').click();
  await page.waitForFunction(() => document.querySelector('#guide-panel').getAttribute('aria-busy') === 'false');
  const current = await get('/api/guides');
  assert.equal(current.selections[0].guide_id, doc.guide_id); assert.equal(current.selections[0].revision_id, doc.revision_id);
  await closeGuides(); return current.selections[0];
}
async function fillMatch(goal) {
  for (const [key, value] of Object.entries({ game: 'synthetic-g6', platform: 'pc', mode: 'notes', version: '1.0', goal })) await page.locator('#match-' + key).fill(value);
}
async function settled() {
  await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden, null, { timeout: 20000 });
  const turn = (await session()).turns.at(-1); assert.equal(turn.status, 'completed'); return turn;
}
async function ask(question, { doc, requireLocal = false } = {}) {
  await closeGuides(); const before = modelCalls.length, networkBefore = report.provider_requests.length;
  await page.locator('#message-input').fill(question); await page.locator('#send-message').click();
  await until(() => modelCalls.length > before, 'actual provider request');
  await until(async () => (await session()).turns.at(-1)?.status === 'completed', 'turn completion');
  const turn = await settled(); const request = modelCalls.at(-1);
  const requests = modelCalls.length - before;
  if (requireLocal) assert.equal(requests, 1, 'one answer request per covered synthetic question');
  else assert.ok(requests >= 1 && requests <= 2, 'gap may plan before the synthetic answer');
  const sid = await sessionId(), tid = turn.turn_id || turn.id;
  const events = (await get(`/api/sessions/${sid}/turns/${tid}/events?after=0`)).events;
  const tools = events.filter((item) => item.type === 'tool');
  assert.equal(tools.filter((item) => ['search_web', 'read_web_page'].includes(item.name)).length, 0);
  assert.equal(report.provider_requests.slice(networkBefore).filter((item) => item.path !== '/v1/chat/completions').length, 0);
  assert.ok(!request.body.messages.some((item) => item.role === 'tool'));
  if (requireLocal) {
    assert.equal(request.evidence?.local_retrieval?.status, 'sufficient', question);
    const sources = request.evidence.sources;
    assert.ok(sources.length >= 1 && sources.length <= 6); assert.equal(sources[0].id, 'S1');
    assert.ok(sources.every((item) => item.local === true && item.guide_id === doc.guide_id && item.revision_id === doc.revision_id));
    assert.ok(sources.reduce((sum, item) => sum + [...item.text].length, 0) <= 8000);
    const full = (await get(`/api/guides/${doc.guide_id}?revision_id=${doc.revision_id}`)).guide;
    for (const source of sources) {
      assert.match(source.chunk_id, /^chunk-[a-f0-9]{32}$/);
      assert.equal(source.text, [...full.text].slice(source.start, source.end).join(''));
    }
    assert.equal(request.evidence.tool_rounds, 0, 'sufficient local retrieval has no research round');
    assert.equal(tools.length, 0, 'available supplement schemas do not imply tool execution');
    assert.ok(!events.some((item) => item.type === 'status' && item.status === 'planning'));
  }
  report.questions_observed.push({ phase, session_id: sid, turn_id: tid, question, status: turn.status,
    model_requests: requests, search_requests: 0, page_requests: 0, tool_events: tools,
    local_status: request.evidence?.local_retrieval?.status, sources: request.evidence?.sources || [], model_request_sha256: request.sha256 });
  return request;
}

(async () => {
  fixture = JSON.parse(execFileSync(python, ['-c', bootstrap, dataRoot], { cwd: root, env, encoding: 'utf8', timeout: 30000 }));
  assert.deepEqual(fixture.control.selections, []); report.seed = fixture;
  if (archive) {
    assert.equal(process.platform, 'win32', '--archive requires Windows and never falls back to source execution');
    assert.equal(process.arch, 'x64');
    const extract = 'import sys; from pathlib import Path; from scripts.package_smoke import safe_extract; print(safe_extract(Path(sys.argv[1]), Path(sys.argv[2])))';
    const extracted = execFileSync(python, ['-c', extract, path.resolve(archive), path.join(temporary, '解压 应用')], { cwd: root, encoding: 'utf8', env }).trim();
    executablePath = path.join(extracted, 'ai-neko.exe'); launchArgs = []; launchCwd = extracted;
    report.archive_sha256 = hash(path.resolve(archive)); report.build = JSON.parse(fs.readFileSync(path.join(extracted, 'build-info.json')));
    assert.equal(report.build.source.commit, report.source_commit);
    env.PATH = [path.join(env.SYSTEMROOT || 'C:\\Windows', 'System32'), env.SYSTEMROOT || 'C:\\Windows'].join(';');
  }
  report.executable_sha256 = hash(executablePath);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  await launch(); const [docA, docB] = fixture.documents;
  await check('configure_synthetic_model_search_and_personal_preference_via_UI', async () => {
    await page.locator('#open-settings').click();
    await page.locator('#memory-content').fill(preference); await page.locator('#memory-kind').selectOption('preference');
    await page.locator('#memory-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#memory-status').textContent.includes('已保存'));
    await page.locator('#model-base-url').fill(`http://127.0.0.1:${server.address().port}/v1`);
    await page.locator('#model-name').fill('synthetic-g6'); await page.locator('#model-api-key').fill('');
    await page.locator('#search-base-url').fill(searchEndpoint);
    await page.locator('#search-api-key').fill('synthetic-g6-search-key');
    await page.locator('#save-settings').click(); await page.waitForSelector('#settings-panel', { state: 'hidden' });
    assert.equal((await get('/api/config')).search_key_set, true); await page.locator('#mode-guide').click();
    return { search_key_set: true, preference_saved: (await get('/api/memories')).memories.some((item) => item.content === preference) };
  });
  await check('A01_UI_adopt_then_actual_new_process_new_chat_contains_same_saved_body', async () => {
    const selected = await adopt(docA); await ask(questions[0], { doc: docA, requireLocal: true });
    const priorSession = await sessionId(), firstPid = report.launches.at(-1).pid;
    await screenshot('A01-before-exit'); await captureRequests(); await application.close(); application = null;
    const count = modelCalls.length; await launch();
    assert.notEqual(report.launches.at(-1).pid, firstPid); assert.equal(modelCalls.length, count, 'restart must not replay inference');
    assert.ok((await page.locator('#current-guide-label').innerText()).includes('G6 合成攻略 A'));
    assert.equal((await get('/api/guides')).selections[0].revision_id, selected.revision_id);
    await page.locator('#new-session').click(); await page.locator('#mode-guide').click();
    const request = await ask(questions[0], { doc: docA, requireLocal: true });
    assert.notEqual(await sessionId(), priorSession);
    const card = page.locator('.turn').last().locator('.source-card').first(); await card.locator('summary').click();
    assert.ok((await card.innerText()).includes('本地保存')); await screenshot('A01-new-process-new-chat');
    return { first_pid: firstPid, second_pid: report.launches.at(-1).pid, previous_session_id: priorSession,
      new_session_id: await sessionId(), guide_id: selected.guide_id, revision_id: selected.revision_id, actual_source: request.evidence.sources[0] };
  });
  const reuseSession = await sessionId();
  await check('A02_five_fixed_questions_with_search_configured_have_zero_network_tools', async () => {
    // Non-Windows credentials intentionally expire with the service process.
    // Restore only this synthetic key through the same visible settings UI.
    const reenteredAfterRestart = !(await get('/api/config')).search_key_set;
    if (reenteredAfterRestart) {
      await page.locator('#open-settings').click();
      await page.locator('#search-api-key').fill('synthetic-g6-search-key');
      await page.locator('#save-settings').click(); await page.waitForSelector('#settings-panel', { state: 'hidden' });
    }
    assert.equal((await get('/api/config')).search_key_set, true);
    for (const question of questions) await ask(question, { doc: docA, requireLocal: true });
    assert.equal(await sessionId(), reuseSession); await screenshot('A02-search-configured');
    return { session_id: reuseSession, question_count: 5, search_key_set: true, synthetic_key_reentered_after_restart: reenteredAfterRestart, search_requests: 0, page_requests: 0 };
  });
  await check('A02_same_five_questions_after_UI_search_disable_have_zero_network_tools', async () => {
    await page.locator('#open-settings').click(); await page.locator('#clear-search-key').check();
    await page.locator('#save-settings').click(); await page.waitForSelector('#settings-panel', { state: 'hidden' });
    assert.equal((await get('/api/config')).search_key_set, false);
    for (const question of questions) await ask(question, { doc: docA, requireLocal: true });
    assert.equal(await sessionId(), reuseSession); await screenshot('A02-search-disabled');
    return { session_id: reuseSession, question_count: 5, search_key_set: false, search_requests: 0, page_requests: 0 };
  });
  let oldMatch;
  await check('ten_old_match_turns', async () => {
    await openGuides(); await fillMatch('旧局目标：守住雾港'); await page.locator('#match-start').click();
    await page.waitForFunction(() => document.querySelector('#guide-panel').getAttribute('aria-busy') === 'false');
    oldMatch = (await matches()).current; assert.ok(oldMatch);
    for (let index = 0; index < 10; index++) {
      const request = await ask(`我现在有 ${41 + index} 金币。灯芯是什么？`);
      assert.equal(request.match_context.match_id, oldMatch.match_id);
      assert.ok(request.match_context.observations.some((item) => item.text.includes(`${41 + index} 金币`)));
      assert.ok((await session()).turns.at(-1).confirmed_text.includes(`旧局建议标记${index + 1}`));
    }
    const turns = (await session()).turns.filter((item) => item.context?.match?.match_id === oldMatch.match_id);
    assert.equal(turns.length, 10); await screenshot('A07-ten-old-turns');
    return { old_match_id: oldMatch.match_id, confirmed_turn_ids: turns.map((item) => item.turn_id || item.id), count: turns.length };
  });
  await check('A07_UI_new_match_excludes_ten_old_dynamic_turns_and_keeps_guide_and_preference', async () => {
    await openGuides(); await fillMatch('新局目标：观察潮汐'); await page.locator('#match-new').click();
    await page.waitForFunction(() => document.querySelector('#guide-panel').getAttribute('aria-busy') === 'false');
    const current = (await matches()).current; assert.notEqual(current.match_id, oldMatch.match_id);
    const request = await ask(questions[0], { doc: docA, requireLocal: true });
    assert.equal(request.match_context.match_id, current.match_id); assert.deepEqual(request.match_context.observations, []);
    assert.equal(request.match_context.last_delivered_advice, null);
    const messages = JSON.stringify(request.body.messages);
    assert.ok(!messages.includes('旧局建议标记')); assert.ok(!messages.includes('旧局目标：守住雾港'));
    for (let count = 41; count <= 50; count++) assert.ok(!messages.includes(`${count} 金币`));
    assert.ok(messages.includes(preference), 'applicable personal preference must survive new match');
    assert.equal(request.match_context.selection.guide_id, docA.guide_id); await screenshot('A07-new-match');
    return { old_match_id: oldMatch.match_id, current_match_id: current.match_id,
      actual_context: request.match_context, applicable_preference_in_model: true, old_dynamic_fields_absent: true };
  });
  await check('A07_explicit_UI_review_recovers_old_match_as_historical_only', async () => {
    const current = (await matches()).current; await openGuides(); await page.locator('#match-history > summary').click();
    const card = page.locator(`.match-card[data-match-id="${oldMatch.match_id}"]`);
    await card.locator('.match-review-question').fill('请复盘旧局十轮已经讨论过的决策，不要当成当前局势。');
    const before = modelCalls.length; await card.locator('.match-review').click();
    await until(() => modelCalls.length > before, 'historical review model'); await until(async () => (await session()).turns.at(-1)?.status === 'completed', 'review completion'); await settled();
    const request = modelCalls.at(-1); assert.equal(request.match_context.match_id, oldMatch.match_id);
    assert.equal(request.match_context.status, 'historical'); assert.equal(request.match_context.history_only, true);
    assert.deepEqual(request.match_context.observations, []); assert.ok(JSON.stringify(request.body.messages).includes('旧局建议标记'));
    assert.equal((await matches()).current.match_id, current.match_id); await screenshot('A07-explicit-review');
    const next = await ask(questions[1], { doc: docA, requireLocal: true });
    assert.equal(next.match_context.match_id, current.match_id); assert.ok(!JSON.stringify(next.body.messages).includes('旧局建议标记'));
    return { reviewed_match_id: oldMatch.match_id, current_match_id: current.match_id, actual_historical_context: request.match_context,
      next_current_request_excludes_review: true };
  });
  await check('A09_UI_switch_reuses_S1_with_exact_new_document_and_revision', async () => {
    const oldSource = report.questions_observed.find((item) => item.sources.length)?.sources[0];
    await adopt(docB); const request = await ask(questions[0], { doc: docB, requireLocal: true });
    assert.equal(oldSource.id, 'S1'); assert.equal(request.evidence.sources[0].id, 'S1');
    assert.notEqual(oldSource.guide_id, request.evidence.sources[0].guide_id);
    assert.ok(!request.body.messages.filter((item) => item.role === 'assistant').some((item) => item.content.includes('潮灯守卫')));
    await screenshot('A09-remapped-S1'); return { before: oldSource, after: request.evidence.sources[0], index_rebuild: 'NOT_RUN in this desktop harness' };
  });
  assert.deepEqual(errors, []); assert.equal(report.provider_requests.filter((item) => item.path !== '/v1/chat/completions').length, 0);
  report.source_files_unchanged = Object.entries(report.source_hashes).every(([name, digest]) => hash(path.join(root, name)) === digest);
  assert.ok(report.source_files_unchanged, 'tracked production/harness source changed during acceptance');
  if (archive) { assert.equal(hash(path.resolve(archive)), report.archive_sha256); report.packaged_windows = 'PASS'; }
  report.status = 'PASS';
})().catch(async (error) => {
  report.status = 'FAIL'; report.error = error.stack; process.exitCode = 1; console.error(error.stack);
  if (page && !page.isClosed()) {
    report.failure_ui = await page.evaluate(() => Object.fromEntries(['guide-status', 'match-summary', 'notice-text', 'settings-message', 'memory-status'].map((id) => [id, document.getElementById(id)?.textContent]))).catch(() => ({}));
    await screenshot('failure').catch(() => {});
  }
}).finally(async () => {
  await captureRequests(); if (application) await application.close().catch(() => {});
  server.closeAllConnections(); if (server.listening) await new Promise((resolve) => server.close(resolve));
  report.model_requests = modelCalls; report.synthetic_calls = { model: modelCalls.length, search: report.provider_requests.filter((item) => item.path === '/search').length };
  report.renderer_and_provider_errors = errors; report.finished_at = new Date().toISOString(); save();
  console.log(JSON.stringify({ status: report.status, execution: report.execution, cases: report.cases.map(({ name, status }) => ({ name, status })), output, temporary_fixture: temporary }));
});
