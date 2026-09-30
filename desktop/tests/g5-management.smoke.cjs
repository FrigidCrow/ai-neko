'use strict';
// Source Electron acceptance: every user mutation enters through rendered UI.
// HTTP providers and microphone are synthetic; IPC and WebAudio are real.
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
const outputArg = args.indexOf('--output');
if (outputArg >= 0 && !args[outputArg + 1]) throw new Error('--output requires a filename');
const output = path.resolve(outputArg < 0 ? path.join(root, 'artifacts/guides-evaluation/g5-management.json') : args[outputArg + 1]);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-g5-electron-'));
const dataRoot = path.join(temporary, 'synthetic');
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
const hash = (name) => sha(fs.readFileSync(name));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tracked = ['desktop/tests/g5-management.smoke.cjs', 'desktop/renderer/app.js',
  'desktop/renderer/companion.js', 'desktop/renderer/guide-panel.js', 'desktop/renderer/index.html',
  'desktop/renderer/style.css', 'desktop/main.cjs', 'desktop/lib/backend.cjs',
  'src/ai_neko/chat/graph.py', 'src/ai_neko/runtime/service.py', 'src/ai_neko/runtime/matches.py',
  'src/ai_neko/runtime/match_store.py', 'src/ai_neko/runtime/guides.py',
  'src/ai_neko/runtime/controls.py', 'src/ai_neko/chat/control_intent.py',
  'src/ai_neko/memory/guide_retrieval.py', 'uv.lock', 'desktop/package-lock.json'];
const report = {
  status: 'RUNNING', started_at: new Date().toISOString(), platform: process.platform, node: process.version,
  source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  source_dirty: Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim()),
  source_hashes: Object.fromEntries(tracked.filter((name) => fs.existsSync(path.join(root, name))).map((name) => [name, hash(path.join(root, name))])),
  source_execution: true, packaged_windows: 'NOT_RUN', windows_11: 'NOT_RUN', model_quality: 'NOT_RUN',
  validation_stage: args.includes('--integration') ? 'development_integration' : 'frozen_source_acceptance',
  real_provider_calls: 0, actual_user_microphone_captures: 0, actual_desktop_captures: 0,
  synthetic_microphone: true, actual_web_audio: true, mutation_api_shortcuts: 0,
  temporary_fixture: temporary, fixture_retained: true, cases: [], screenshots: [], launches: [],
  backend_requests: [], model_requests: [], asr_requests: [], tts_requests: [], audio_events: [],
};
fs.mkdirSync(path.dirname(output), { recursive: true });
const save = () => fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
const env = { ...process.env };
for (const key of Object.keys(env)) {
  if (/^(AI_NEKO_|ELECTRON_|LANGSMITH_)/.test(key) || ['PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS'].includes(key)) delete env[key];
}
env.AI_NEKO_DATA_DIR = dataRoot;
env.PYTHONUTF8 = '1';
if (process.platform === 'win32') {
  const profile = path.join(temporary, 'synthetic-user');
  fs.mkdirSync(path.join(profile, 'AppData', 'Local'), { recursive: true });
  env.USERPROFILE = profile; env.LOCALAPPDATA = path.join(profile, 'AppData', 'Local');
  env.APPDATA = path.join(profile, 'AppData', 'Roaming');
}
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const bootstrap = `
import json, sys
from datetime import datetime, timezone
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.service import MemoryService
memory=MemoryService(initialize_data_root(sys.argv[1]))
try:
    docs=[]
    for letter, text in [('A','灯芯是潮灯守卫。灯芯负责照亮码头。'),('B','航灯是雾岸领航员。航灯负责标记航道。')]:
        url=f'https://guides.example.test/g5/{letter.lower()}'
        docs.append(memory.guides.ingest({'status':'read','completeness':'full','completeness_reasons':[],
          'text':'合成说明 1.0：'+text+'术语不是当前玩家状态，也不表示玩家已经操作。',
          'url':url,'original_url':url,'final_url':url,'title':f'合成攻略 {letter}',
          'retrieved_at':datetime.now(timezone.utc).isoformat(),'content_date':None,'headings':[]},
          game='synthetic-g5',platform='pc',mode='notes',game_version='1.0',version_basis='原文明确标注 1.0'))
    print(json.dumps({'scope':memory.scope,'documents':docs,'control':memory.guides.control_snapshot()},ensure_ascii=False))
finally:
    memory.close()
`;
let fixture; let application; let page; let phase = 'bootstrap';
let asrText = ''; let holdNextASR = false; let holdNextTTS = false; let holdNextModel = false;
const errors = []; const releases = new Set(); const modelCalls = []; const asrCalls = []; const ttsCalls = [];
const mp3 = fs.readFileSync(path.join(__dirname, 'fixtures/synthetic-tone.mp3'));
function jsonMessage(body, key) {
  for (const message of body.messages || []) {
    if (typeof message.content !== 'string') continue;
    const start = message.content.indexOf('{');
    if (start < 0) continue;
    try { const parsed = JSON.parse(message.content.slice(start)); if (key in parsed) return parsed; } catch { /* ordinary user text */ }
  }
  return null;
}
async function gate(entry, response) {
  await new Promise((resolve) => {
    entry.release = () => { releases.delete(entry.release); entry.released_at = Date.now(); resolve(); };
    releases.add(entry.release); response.once('close', entry.release);
  });
}
const server = http.createServer((request, response) => {
  const chunks = [];
  request.on('data', (chunk) => chunks.push(chunk));
  request.on('end', async () => {
    try {
      const bytes = Buffer.concat(chunks);
      if (request.method !== 'POST') throw new Error('unexpected provider method: ' + request.method);
      if (request.url === '/v1/audio/transcriptions') {
        const entry = { phase, text: asrText, received_at: Date.now(), bytes: bytes.length, sha256: sha(bytes), held: holdNextASR };
        holdNextASR = false; asrCalls.push(entry);
        if (entry.held) await gate(entry, response);
        if (!response.destroyed) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ text: entry.text })); }
        return;
      }
      if (request.url === '/v1/audio/speech') {
        const entry = { phase, body: JSON.parse(bytes), received_at: Date.now(), held: holdNextTTS };
        holdNextTTS = false; ttsCalls.push(entry);
        if (entry.held) await gate(entry, response);
        if (!response.destroyed) { response.writeHead(200, { 'Content-Type': 'audio/mpeg' }); response.end(mp3); }
        return;
      }
      if (request.url !== '/v1/chat/completions') throw new Error('unexpected provider URL: ' + request.url);
      const body = JSON.parse(bytes);
      const evidence = jsonMessage(body, 'sources');
      const match = jsonMessage(body, 'match_context')?.match_context;
      const observation = body.tools?.some((tool) => tool.function?.name === 'report_match_observation');
      const entry = { phase, kind: observation ? 'observation' : 'answer', body, evidence, match_context: match, received_at: Date.now(), held: holdNextModel };
      holdNextModel = false; modelCalls.push(entry);
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      if (observation) {
        response.end('data: ' + JSON.stringify({ choices: [{ delta: { tool_calls: [{ index: 0, id: 'g5-synthetic-frame', type: 'function',
          function: { name: 'report_match_observation', arguments: JSON.stringify({ fields: [{ name: '金币', value: '31' }, { name: '回合', value: '8' }] }) } }] } }] }) + '\n\ndata: [DONE]\n\n');
        return;
      }
      const sourced = evidence?.local_retrieval?.status === 'sufficient';
      const first = !sourced ? '合成回应：当前资料未覆盖整个问题。' : evidence?.sources?.[0]?.guide_id === fixture.documents[1].guide_id ? '航灯是雾岸领航员。' : '灯芯是潮灯守卫。';
      entry.first_fragment = first;
      response.write('data: ' + JSON.stringify({ choices: [{ delta: { content: first } }] }) + '\n\n');
      if (entry.held) await gate(entry, response); else await pause(100);
      if (!response.destroyed) {
        entry.tail = entry.held ? '旧请求迟到尾句不应出现。' : sourced ? '合成资料已读完 [S1]。' : '此处只验证消息与生命周期。';
        response.write('data: ' + JSON.stringify({ choices: [{ delta: { content: entry.tail } }] }) + '\n\n');
        response.end('data: [DONE]\n\n');
      }
    } catch (error) { errors.push('provider: ' + error.message); response.destroy(); }
  });
});

async function until(predicate, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await predicate()) return; await pause(80); }
  throw new Error('Timed out: ' + label);
}
async function get(route) {
  assert.ok(route.startsWith('/api/'));
  // Controls deliberately close reads while a cross-database intent settles.
  // Retry only read-only 409 responses; never repeat a user mutation here.
  const deadline = Date.now() + 20000;
  for (;;) {
    const result = await page.evaluate(async (url) => {
      try { return { value: await window.aiNekoChat.api(url) }; }
      catch (error) { return { error: { status: error.status, code: error.code, message: error.message } }; }
    }, route);
    if (!result.error) return result.value;
    if (result.error.status !== 409 || Date.now() >= deadline) throw new Error(route + ': ' + JSON.stringify(result.error));
    await pause(80);
  }
}
async function sessionId() { return page.evaluate(() => localStorage.getItem('ai-neko.desktop.last-session')); }
async function session() { const id = await sessionId(); return id ? get('/api/sessions/' + id) : { turns: [] }; }
async function matches() { return get('/api/sessions/' + await sessionId() + '/matches'); }
async function latestTurn() { return (await session()).turns.at(-1); }
async function settle() {
  await until(async () => { const turn = await latestTurn(); return !turn || ['completed', 'cancelled', 'error'].includes(turn.status); }, 'backend turn settled');
  await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden, null, { timeout: 20000 });
}
async function captureLogs() {
  if (!application) return;
  report.backend_requests.push(...await application.evaluate(() => globalThis.g5Requests?.splice(0) || []).catch(() => []));
  if (page && !page.isClosed()) report.audio_events.push(...await page.evaluate(() => window.g5Audio?.splice(0) || []).catch(() => []));
}
async function check(name, operation) {
  phase = name;
  await application.evaluate((_, value) => { globalThis.g5Phase = value; }, name);
  const item = { name, status: 'RUNNING', started_at: new Date().toISOString(), models_before: modelCalls.length };
  report.cases.push(item); save();
  try { item.evidence = await operation() || {}; item.status = 'PASS'; }
  catch (error) { item.status = 'FAIL'; item.error = error.stack; throw error; }
  finally { item.model_requests = modelCalls.length - item.models_before; await captureLogs(); save(); }
}
async function screenshot(name) {
  const filename = path.join(path.dirname(output), path.basename(output, '.json') + '-' + name + '.png');
  await page.screenshot({ path: filename, omitBackground: true });
  report.screenshots.push({ file: path.basename(filename), sha256: hash(filename) });
}
async function launch() {
  application = await _electron.launch({ executablePath: require('../node_modules/electron'),
    args: [path.join(root, 'desktop'), '--use-fake-device-for-media-stream'], cwd: root, env, timeout: 45000 });
  report.launches.push(await application.evaluate(() => ({ pid: process.pid, versions: process.versions, at: Date.now() })));
  page = await application.firstWindow(); await page.waitForLoadState('domcontentloaded');
  if (page.url().includes('/consent/')) await page.locator('#accept').click();
  await until(() => {
    const renderer = application.windows().find((item) => item.url().includes('/renderer/'));
    if (!renderer) return false; page = renderer; return true;
  }, 'renderer window');
  page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForSelector('body[data-chat-ready="true"]', { timeout: 45000 });
  await page.waitForSelector('#pet-stage[data-loaded="true"]', { timeout: 45000 });
  await application.evaluate(({ app }) => {
    const load = process.getBuiltinModule('module').createRequire(process.getBuiltinModule('path').join(app.getAppPath(), 'main.cjs'));
    const { OwnedBackend } = load('./lib/backend.cjs');
    const original = OwnedBackend.prototype.request;
    globalThis.g5Requests = []; globalThis.g5Phase = 'launch';
    OwnedBackend.prototype.request = async function (request) {
      let body; try { body = request.encoded ? JSON.parse(request.encoded) : undefined; } catch { body = '<non-json>'; }
      // Keep content required to audit actual mutations; never retain audio blobs.
      if (body && typeof body === 'object') for (const key of ['audio', 'audio_base64']) if (body[key]) body[key] = '<synthetic audio omitted>';
      const entry = { phase: globalThis.g5Phase, method: request.method, path: request.path, body, started_at: Date.now() };
      globalThis.g5Requests.push(entry);
      try {
        const result = await original.call(this, request); entry.status = result.status;
        if ((request.method !== 'GET' && !request.path.startsWith('/api/voice/')) || /\/control-jobs\/|\/events\?/.test(request.path) || result.status >= 400) entry.response = result.body;
        entry.finished_at = Date.now(); return result;
      } catch (error) { entry.error = error.message; throw error; }
    };
  });
  await page.evaluate(() => {
    window.g5Audio = [];
    for (const method of ['start', 'stop']) {
      const original = AudioBufferSourceNode.prototype[method];
      AudioBufferSourceNode.prototype[method] = function (...args) {
        const result = original.apply(this, args);
        window.g5Audio.push({ method, at: Date.now(), performance_ms: performance.now() }); return result;
      };
    }
  });
}
async function openGuides() {
  if (await page.locator('#guide-panel').isHidden()) await page.locator('#open-guides').click();
  await page.waitForSelector('#guide-library-list .guide-card');
}
async function controlsReady() {
  await page.waitForFunction(() => document.querySelector('#guide-panel').getAttribute('aria-busy') === 'false');
}
async function closeGuides() { if (await page.locator('#guide-panel').isVisible()) await page.locator('#close-guides').click(); }
function guideCard(doc) { return page.locator(`.guide-card[data-guide-id="${doc.guide_id}"]`); }
async function confirmIfVisible() {
  if (await page.locator('#guide-confirmation').isVisible()) await page.locator('#confirm-guide-action').click();
}
async function chooseGuide(doc) {
  await openGuides(); await controlsReady();
  await guideCard(doc).locator('.guide-select, .guide-switch').click();
  await page.waitForFunction(() => document.querySelector('#guide-status').textContent.includes('请确认游戏条件'));
  await page.locator('#guide-adopt').click();
  await confirmIfVisible();
  await until(async () => (await get('/api/guides')).selections.some((item) => item.guide_id === doc.guide_id), 'guide selection committed');
  await controlsReady();
}
async function fillMatch(goal) {
  for (const [key, value] of Object.entries({ game: 'synthetic-g5', platform: 'pc', mode: 'notes', version: '1.0', goal })) await page.locator('#match-' + key).fill(value);
}
async function newMatch(goal) {
  const old = (await matches()).current;
  await openGuides(); await fillMatch(goal); await page.locator('#match-new').click(); await confirmIfVisible();
  await until(async () => { const value = (await matches()).current; return value && value.match_id !== old.match_id; }, 'new match committed');
  await controlsReady(); return (await matches()).current;
}
async function ask(text, { held = false } = {}) {
  await closeGuides(); await settle();
  const before = modelCalls.length; holdNextModel = held;
  await page.locator('#message-input').fill(text); await page.locator('#send-message').click();
  await until(() => modelCalls.slice(before).some((item) => item.kind === 'answer'), 'actual answer model request');
  const entry = modelCalls.slice(before).find((item) => item.kind === 'answer');
  await page.waitForFunction((fragment) => document.querySelector('.turn:last-child .assistant-output')?.textContent.includes(fragment), entry.first_fragment, { timeout: 15000 });
  if (held) assert.equal((await latestTurn()).status, 'running'); else await settle();
  return entry;
}
async function stopAudio() {
  await closeGuides(); await page.locator('#stop-audio').click();
  await page.waitForFunction(() => document.body.dataset.speaking !== 'true');
}
async function speak(text, { held = false } = {}) {
  await closeGuides(); await settle(); asrText = text; holdNextASR = held;
  const before = asrCalls.length;
  await page.locator('#record-voice').click();
  await page.waitForFunction(() => document.querySelector('#record-voice').getAttribute('aria-pressed') === 'true');
  await pause(350); await page.locator('#record-voice').click();
  await until(() => asrCalls.length > before, 'actual ASR request');
  return asrCalls.at(-1);
}
async function selectVoiceTarget(doc) {
  await openGuides(); await controlsReady();
  const details = guideCard(doc).locator('.guide-document-detail');
  if (await details.getAttribute('open') !== null) await details.locator('summary').click();
  await details.locator('summary').click();
  await details.locator('.guide-body').waitFor();
  await closeGuides();
}
async function voiceControl(text, { target = null, newGame = false } = {}) {
  if (target) await selectVoiceTarget(target);
  const old = (await matches()).current; const before = { model: modelCalls.length, tts: ttsCalls.length, turns: (await session()).turns.length };
  await speak(text);
  await until(async () => (await session()).turns.length >= before.turns + 2, 'independent control response turn');
  await settle();
  const created = (await session()).turns.slice(before.turns);
  const source = created.find((item) => item.input === text);
  assert.ok(source, 'ASR command must create its real source turn');
  const sid = await sessionId(); const sourceId = source.turn_id || source.id;
  const events = await get(`/api/sessions/${sid}/turns/${sourceId}/events?after=0`);
  assert.ok(events.control_job_id);
  const job = await get(`/api/sessions/${sid}/control-jobs/${events.control_job_id}`);
  assert.equal(job.status, 'completed'); assert.equal(job.committed, true);
  assert.notEqual(job.response_turn_id, sourceId);
  const response = created.find((item) => (item.turn_id || item.id) === job.response_turn_id);
  assert.ok(response); assert.equal(response.status, 'completed');
  const current = (await matches()).current;
  if (target) assert.equal(current.selection.guide_id, target.guide_id);
  if (newGame) assert.notEqual(current.match_id, old.match_id);
  assert.equal(job.response_turn.context.match.match_id, current.match_id);
  assert.equal(job.response_turn.context.match.expected_revision, (await matches()).revision);
  assert.equal(modelCalls.length, before.model, 'control confirmation must not call model');
  await until(() => ttsCalls.length > before.tts, 'independent control confirmation TTS');
  await page.waitForFunction(() => document.body.dataset.speaking === 'true');
  await captureLogs();
  const submission = report.backend_requests.findLast((item) => item.method === 'POST' && /\/turns$/.test(item.path) && item.body?.text === text);
  assert.equal(submission?.body.input_origin, 'voice');
  assert.equal(submission.body.match.match_id, old.match_id);
  if (target) assert.equal(submission.body.guide_target.guide_id, target.guide_id);
  const voiceRequest = report.backend_requests.findLast((item) => item.path === '/api/voice/synthesize');
  assert.equal(voiceRequest.body.turn_id, job.response_turn_id);
  assert.equal(voiceRequest.body.match.expected_revision, (await matches()).revision);
  await screenshot(newGame ? 'voice-new-match' : 'voice-' + target.title.slice(-1));
  await stopAudio();
  return { command_turn_id: sourceId, response_turn_id: job.response_turn_id, job_id: events.control_job_id,
    submitted_match: submission.body.match, response_match: job.response_turn.context.match,
    input_origin: submission.body.input_origin, guide_target: submission.body.guide_target, confirmation_TTS: ttsCalls.slice(before.tts).map((item) => item.body.input), model_calls: 0 };
}
async function assertSource(call, doc) {
  assert.equal(call.evidence?.local_retrieval?.status, 'sufficient');
  const sources = call.evidence.sources;
  assert.ok(sources.length >= 1 && sources.length <= 6);
  assert.equal(sources[0].id, 'S1');
  assert.ok(sources.every((item) => item.local === true && item.guide_id === doc.guide_id && item.revision_id === doc.revision_id));
  assert.ok(sources.reduce((sum, item) => sum + [...item.text].length, 0) <= 8000);
  const detail = await get(`/api/guides/${doc.guide_id}?revision_id=${doc.revision_id}`);
  for (const item of sources) {
    assert.match(item.chunk_id, /^chunk-[a-f0-9]{32}$/);
    assert.equal(item.text, [...detail.guide.text].slice(item.start, item.end).join(''));
  }
}

(async () => {
  fixture = JSON.parse(execFileSync(python, ['-c', bootstrap, dataRoot], { cwd: root, env, encoding: 'utf8', timeout: 30000 }));
  assert.deepEqual(fixture.control.selections, []);
  report.bootstrap = { scope: fixture.scope, unadopted_documents: fixture.documents.map(({ guide_id, revision_id, game_version }) => ({ guide_id, revision_id, game_version })) };
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  await launch();
  const [docA, docB] = fixture.documents;
  await check('configure_synthetic_services_through_settings_ui', async () => {
    const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    await page.locator('#open-settings').click();
    for (const [id, value] of [['asr-base-url', endpoint], ['asr-model', 'synthetic-asr'], ['tts-base-url', endpoint], ['tts-model', 'synthetic-tts'], ['tts-voice', 'synthetic-tone']]) await page.locator('#' + id).fill(value);
    await page.locator('#voice-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#voice-config-status').textContent.includes('已保存'));
    await page.locator('#model-base-url').fill(endpoint); await page.locator('#model-name').fill('synthetic-g5');
    await page.locator('#model-api-key').fill(''); await page.locator('#search-api-key').fill('');
    await page.locator('#search-base-url').fill('https://api.tavily.com');
    await page.locator('#save-settings').click(); await page.waitForSelector('#settings-panel', { state: 'hidden' });
    await page.locator('#mode-chat').click();
    return { search_configured: false, endpoint, actual_settings_forms: true };
  });
  await check('library_adopt_A_and_actual_model_source_card', async () => {
    await chooseGuide(docA); await closeGuides();
    assert.ok((await page.locator('#current-guide-label').innerText()).includes('合成攻略 A'));
    const call = await ask('灯芯是什么意思？'); await assertSource(call, docA);
    const card = page.locator('.turn').last().locator('.source-card').first(); await card.locator('summary').click();
    const text = await card.innerText();
    for (const label of ['本地保存', '已采用', '原文版本 1.0', '上次核查', '字符']) assert.ok(text.includes(label), text);
    await screenshot('adopted-source'); return { guide_id: docA.guide_id, source: call.evidence.sources[0], source_card_text: text };
  });
  await check('source_card_adoption_after_explicit_unadopt', async () => {
    await openGuides(); await page.locator('.guide-selection .guide-unadopt').click(); await confirmIfVisible();
    await until(async () => (await get('/api/guides')).selections.length === 0, 'unadopt committed');
    await controlsReady();
    await closeGuides();
    await page.locator('.turn').last().locator('.source-adopt').first().click();
    await page.waitForFunction(() => document.querySelector('#guide-status').textContent.includes('请确认游戏条件'));
    await page.locator('#guide-adopt').click(); await confirmIfVisible();
    await until(async () => (await get('/api/guides')).selections.some((item) => item.guide_id === docA.guide_id), 'source adoption committed');
    await controlsReady();
    return { source_card_button: true, guide_id: docA.guide_id };
  });
  await check('start_match_and_fresh_typed_state_reach_actual_model', async () => {
    await openGuides(); await fillMatch('合成目标：守住码头'); await page.locator('#match-start').click();
    await until(async () => Boolean((await matches()).current), 'match started');
    await controlsReady();
    const current = (await matches()).current;
    const call = await ask('我现在有 20 金币。灯芯是什么意思？');
    assert.equal(call.match_context?.match_id || call.match_context?.id, current.match_id);
    assert.equal(call.match_context.goal, '合成目标：守住码头');
    assert.ok(call.match_context.observations.some((item) => item.text.includes('20 金币')));
    const next = await ask('灯芯是什么意思？');
    await assertSource(next, docA);
    assert.ok(next.match_context.observations.some((item) => item.text.includes('20 金币')));
    await screenshot('current-match'); return { match: (await matches()).current, actual_context: next.match_context };
  });
  await check('UI_switch_B_remaps_S1_and_removes_A_advice', async () => {
    await chooseGuide(docB); const call = await ask('航灯是什么意思？'); await assertSource(call, docB);
    const history = call.body.messages.filter((item) => item.role === 'assistant').map((item) => item.content).join('\n');
    assert.ok(!history.includes('灯芯是潮灯守卫')); assert.equal(call.match_context.selection.guide_id, docB.guide_id);
    return { selection: call.match_context.selection, old_guide_advice_absent: true };
  });
  await check('held_model_stream_then_UI_switch_drops_late_tail', async () => {
    const call = await ask('航灯是什么意思？', { held: true }); const old = await latestTurn();
    await chooseGuide(docA); call.release(); await settle(); await closeGuides(); await pause(350);
    const prior = (await session()).turns.find((item) => (item.turn_id || item.id) === (old.turn_id || old.id));
    assert.equal(prior.status, 'cancelled'); assert.ok(!JSON.stringify(prior).includes('旧请求迟到尾句'));
    assert.ok(!(await page.locator('#messages').innerText()).includes('旧请求迟到尾句'));
    return { turn_id: old.turn_id || old.id, cancelled: true, late_tail_absent: true };
  });
  await check('held_TTS_then_UI_switch_never_starts_old_audio', async () => {
    await closeGuides(); await page.locator('#speak-replies').check(); holdNextTTS = true;
    const before = ttsCalls.length; await ask('灯芯是什么意思？'); await until(() => ttsCalls.length > before, 'held TTS HTTP');
    const held = ttsCalls[before]; assert.equal(held.held, true);
    const starts = await page.evaluate(() => window.g5Audio.filter((item) => item.method === 'start').length);
    await chooseGuide(docB); held.release(); await pause(500);
    assert.equal(await page.evaluate(() => window.g5Audio.filter((item) => item.method === 'start').length), starts);
    await closeGuides(); assert.notEqual(await page.locator('body').getAttribute('data-speaking'), 'true');
    return { late_tts_audio_started: false, tts_input: held.body.input };
  });
  await check('playing_audio_then_new_match_stops_actual_WebAudio', async () => {
    await ask('航灯是什么意思？'); await page.waitForFunction(() => document.body.dataset.speaking === 'true');
    const stops = await page.evaluate(() => window.g5Audio.filter((item) => item.method === 'stop').length);
    const current = await newMatch('合成新局目标');
    assert.ok(await page.evaluate((count) => window.g5Audio.filter((item) => item.method === 'stop').length > count, stops));
    await closeGuides(); await page.locator('#speak-replies').uncheck();
    const call = await ask('航灯是什么意思？');
    assert.equal(call.match_context.match_id || call.match_context.id, current.match_id);
    assert.deepEqual(call.match_context.observations, []);
    assert.ok(!JSON.stringify(call.match_context).includes('20 金币'));
    return { match_id: current.match_id, actual_node_stop: true, old_observation_absent: true };
  });
  await check('held_ASR_then_UI_new_match_cannot_submit_late_command', async () => {
    const held = await speak('新一局', { held: true }); const before = (await session()).turns.length;
    const current = await newMatch('ASR 迟到后的新局'); const revision = (await matches()).revision;
    held.release(); await pause(600);
    assert.equal((await session()).turns.length, before); assert.equal((await matches()).revision, revision);
    assert.equal((await matches()).current.match_id, current.match_id);
    return { delayed_ASR_did_not_create_turn: true, current_match_id: current.match_id, revision };
  });
  await check('ambiguous_voice_guide_target_clarifies_without_mutation_or_model', async () => {
    // Opening then cancelling an explicit choice is the public way to remove
    // the pointing target; the already adopted guide remains selected.
    await openGuides(); await guideCard(docA).locator('.guide-select, .guide-switch').click();
    await page.waitForFunction(() => document.querySelector('#guide-status').textContent.includes('请确认游戏条件'));
    await page.locator('#cancel-guide-adoption').click(); await closeGuides();
    const before = { guides: await get('/api/guides'), matches: await matches(), models: modelCalls.length, turns: (await session()).turns.length };
    await speak('按这份攻略');
    await until(async () => (await session()).turns.length > before.turns, 'clarification turn accepted'); await settle();
    const turn = await latestTurn();
    assert.equal((await get('/api/guides')).revision, before.guides.revision);
    assert.equal((await matches()).revision, before.matches.revision); assert.equal(modelCalls.length, before.models);
    const text = await page.locator('.turn').last().locator('.assistant-output').innerText();
    assert.ok(/哪|选择|指定|明确/.test(text), text);
    await stopAudio(); return { turn_id: turn.turn_id || turn.id, clarification: text, mutations: 0, models: 0 };
  });
  await check('voice_adopt_A_uses_selected_source_and_independent_committed_receipt', () => voiceControl('按这份攻略', { target: docA }));
  await check('voice_switch_B_uses_new_binding_and_independent_TTS', () => voiceControl('换成这份攻略', { target: docB }));
  await check('voice_new_match_stops_old_context_and_confirms_new_match', () => voiceControl('新一局', { newGame: true }));
  await check('UI_goal_update_and_explicit_historical_review', async () => {
    await openGuides(); await page.locator('#match-goal').fill('更新后的合成目标'); await page.locator('#match-update').click();
    await until(async () => (await matches()).current.goal === '更新后的合成目标', 'goal update'); await controlsReady();
    const catalog = await matches(); const current = catalog.current; const old = catalog.matches.find((item) => item.status === 'ended');
    assert.ok(old);
    await page.locator('#match-history > summary').click();
    const row = page.locator(`.match-card[data-match-id="${old.match_id}"]`);
    await row.locator('.match-review-question').fill('请复盘这局的目标，不能当成当前局势。');
    const before = modelCalls.length; await row.locator('.match-review').click();
    await until(() => modelCalls.length > before, 'historical review actual model'); await settle();
    const call = modelCalls.at(-1);
    assert.ok(JSON.stringify(call.match_context).includes(old.match_id));
    assert.equal((await matches()).current.match_id, current.match_id);
    assert.equal((await matches()).current.goal, '更新后的合成目标');
    await screenshot('historical-review');
    return { reviewed_match_id: old.match_id, current_match_id: current.match_id, actual_context: call.match_context };
  });
  await check('active_match_fresh_vision_propagates_effective_revision_to_real_TTS', async () => {
    await closeGuides();
    // Intercept only source enumeration, returning a newly created synthetic
    // BrowserWindow. No real desktop/window list or user screenshot is read.
    await application.evaluate(async ({ BrowserWindow, desktopCapturer, nativeImage }) => {
      const fixtureWindow = new BrowserWindow({ show: false, width: 680, height: 420,
        webPreferences: { sandbox: true, partition: 'ai-neko-g5-synthetic-frame' } });
      await fixtureWindow.loadURL('data:text/html,<body style="background:%23cdf;color:%23234;font-size:40px">SYNTHETIC G5<br>Round 8<br>Coins 31</body>');
      globalThis.g5VisionCalls = 0;
      desktopCapturer.getSources = async (options) => {
        globalThis.g5VisionCalls++;
        return [{ id: 'window:98766:0', name: 'Synthetic G5 only',
          thumbnail: options.thumbnailSize.width ? await fixtureWindow.webContents.capturePage() : nativeImage.createEmpty() }];
      };
    });
    await page.locator('#open-settings').click(); await page.locator('#refresh-vision').click();
    await page.locator('#vision-source').selectOption('window:98766:0'); await page.locator('#vision-enabled').check();
    await page.waitForSelector('#vision-preview:not([hidden])'); await page.locator('#close-settings').click();
    await page.locator('#speak-replies').check();
    const before = { models: modelCalls.length, tts: ttsCalls.length, revision: (await matches()).revision };
    const call = await ask('航灯是什么意思？'); await assertSource(call, docB);
    await page.waitForFunction(() => document.body.dataset.speaking === 'true');
    const calls = modelCalls.slice(before.models); assert.equal(calls.length, 2);
    assert.equal(calls[0].kind, 'observation'); assert.equal(calls[1].kind, 'answer');
    assert.ok(calls[0].body.messages.some((item) => Array.isArray(item.content) && item.content.some((part) => part.type === 'image_url')));
    const observation = call.match_context.observations.find((item) => item.source_kind === 'vision');
    assert.ok(observation.frame_id); assert.equal(observation.fields['金币'], '31'); assert.equal(observation.fields['回合'], '8');
    assert.ok(observation.expires_at - observation.observed_at <= 120);
    const current = await matches(); assert.ok(current.revision > before.revision);
    await captureLogs();
    const accepted = report.backend_requests.findLast((item) => item.phase === phase && item.method === 'POST' && /\/turns$/.test(item.path));
    assert.equal(accepted.body.match.expected_revision, before.revision);
    assert.equal(accepted.body.image.frame_id, observation.frame_id);
    const playback = report.backend_requests.findLast((item) => item.phase === phase && item.path === '/api/voice/synthesize');
    assert.equal(playback.status, 200); assert.equal(playback.body.match.expected_revision, current.revision);
    assert.equal(playback.body.turn_id, (await latestTurn()).turn_id || (await latestTurn()).id);
    assert.equal(report.backend_requests.filter((item) => item.phase === phase && item.status === 409).length, 0);
    await stopAudio(); await openGuides();
    const visibleEvidence = await page.locator('#match-evidence').innerText();
    assert.ok(visibleEvidence.includes('31'), visibleEvidence);
    await page.locator('#match-evidence').scrollIntoViewIfNeeded(); await screenshot('vision-effective-binding');
    await page.locator('#match-close-observation').click(); await controlsReady();
    const context = await get(`/api/sessions/${await sessionId()}/matches/${current.current.match_id}`);
    assert.deepEqual(context.match?.observations || context.observations, []);
    await closeGuides(); await page.locator('#speak-replies').uncheck();
    await page.locator('#open-settings').click(); assert.equal(await page.locator('#vision-enabled').isChecked(), false); await page.locator('#close-settings').click();
    return { accepted_revision: before.revision, effective_revision: current.revision, frame_id: observation.frame_id,
      observation, TTS_match: playback.body.match, actual_audio_started: true,
      visible_evidence: visibleEvidence, synthetic_capture_calls: await application.evaluate(() => globalThis.g5VisionCalls), close_observation_invalidated: true };
  });
  await check('UI_backup_delete_guide_restore_preserves_tombstone_and_delete_backup', async () => {
    await openGuides(); await page.locator('#guide-backups > summary').click();
    await page.locator('#create-guide-backup').click();
    await until(async () => (await get('/api/guide-backups')).backups.length === 1, 'guide snapshot created'); await controlsReady();
    const snapshot = (await get('/api/guide-backups')).backups[0];
    await chooseGuide(docA);
    await page.locator('.guide-backup-restore').click(); await page.locator('#confirm-guide-action').click();
    await page.waitForFunction(() => document.querySelector('#guide-status').textContent.includes('快照已恢复')); await controlsReady();
    assert.equal((await get('/api/guides')).selections[0].guide_id, docB.guide_id);
    await guideCard(docA).locator('.guide-delete').click();
    assert.equal((await get('/api/guides')).guides.some((item) => item.guide_id === docA.guide_id), true, 'confirmation must precede deletion');
    await page.locator('#cancel-guide-action').click();
    assert.equal((await get('/api/guides')).guides.some((item) => item.guide_id === docA.guide_id), true);
    await guideCard(docA).locator('.guide-delete').click(); await page.locator('#confirm-guide-action').click();
    await until(async () => !(await get('/api/guides')).guides.some((item) => item.guide_id === docA.guide_id), 'guide deleted'); await controlsReady();
    // G2 physically purges snapshots containing deleted text. The UI must
    // discard their restore buttons too, instead of offering a dead target.
    assert.equal((await get('/api/guide-backups')).backups.length, 0);
    await page.locator('#guide-backup-list .backup-card').waitFor({ state: 'detached' });
    await page.locator('#create-guide-backup').click();
    await until(async () => (await get('/api/guide-backups')).backups.length === 1, 'post-deletion snapshot created'); await controlsReady();
    const afterDeletion = (await get('/api/guide-backups')).backups[0];
    await page.locator('.guide-backup-restore').click(); await page.locator('#confirm-guide-action').click();
    await page.waitForFunction(() => document.querySelector('#guide-status').textContent.includes('快照已恢复')); await controlsReady();
    const restored = await get('/api/guides');
    assert.equal(restored.guides.some((item) => item.guide_id === docA.guide_id), false);
    assert.equal(restored.selections[0].guide_id, docB.guide_id);
    await screenshot('backup-restore');
    await page.locator('.guide-backup-delete').click(); await page.locator('#confirm-guide-action').click();
    await until(async () => (await get('/api/guide-backups')).backups.length === 0, 'snapshot deleted'); await controlsReady();
    return { original_backup_id: snapshot.backup_id, restored_selection_B: true, deleted_content_backup_purged: true,
      post_deletion_backup_id: afterDeletion.backup_id, confirmation_cancel_preserved_guide: true, deleted_guide_id: docA.guide_id, restore_did_not_resurrect: true };
  });
  await check('end_match_keeps_selected_guide_and_restart_needs_update', async () => {
    await openGuides(); const ended = (await matches()).current; await page.locator('#match-end').click(); await confirmIfVisible();
    await until(async () => !(await matches()).current, 'match ended');
    await controlsReady();
    assert.equal((await get('/api/guides')).selections[0].guide_id, docB.guide_id);
    await fillMatch('重启验证目标'); await page.locator('#match-start').click();
    await until(async () => Boolean((await matches()).current), 'restart match started');
    await controlsReady();
    const active = (await matches()).current; const sid = await sessionId();
    await closeGuides(); await captureLogs(); await application.close(); application = null;
    const before = { model: modelCalls.length, tts: ttsCalls.length };
    await launch(); assert.equal(await sessionId(), sid);
    const after = (await matches()).current;
    assert.equal(after.match_id, active.match_id); assert.equal(after.status, 'needs_update');
    assert.equal((await get('/api/guides')).selections[0].guide_id, docB.guide_id);
    assert.notEqual(report.launches[0].pid, report.launches.at(-1).pid);
    await pause(400); assert.equal(modelCalls.length, before.model); assert.equal(ttsCalls.length, before.tts);
    assert.ok((await page.locator('#current-match-label').innerText()).includes('待更新'));
    await screenshot('restart'); return { ended_match_id: ended.match_id, restarted_match: after, spontaneous_model_or_audio: false };
  });
  assert.deepEqual(errors, []);
  assert.ok(modelCalls.every((item) => !item.body.messages.some((message) => message.role === 'tool')));
  await captureLogs();
  const actualTools = report.backend_requests.flatMap((item) => item.response?.events || []).filter((item) => item.type === 'tool');
  assert.equal(actualTools.filter((item) => ['search_web', 'read_web_page'].includes(item.name)).length, 0);
  report.external_search_or_read_events = 0;
  report.source_files_unchanged = Object.entries(report.source_hashes).every(([name, digest]) => hash(path.join(root, name)) === digest);
  if (!args.includes('--integration')) assert.ok(report.source_files_unchanged, 'source changed during smoke run');
  report.status = 'PASS';
})().catch(async (error) => {
  report.status = 'FAIL'; report.error = error.stack; process.exitCode = 1; console.error(error.stack);
  if (page && !page.isClosed()) {
    report.failure_ui = await page.evaluate(() => Object.fromEntries(['guide-status', 'match-summary', 'media-status', 'notice-text', 'turn-status'].map((id) => [id, document.getElementById(id)?.textContent]))).catch(() => ({}));
    await screenshot('failure').catch(() => {});
  }
}).finally(async () => {
  for (const release of releases) release();
  await captureLogs();
  if (application) await application.close().catch(() => {});
  server.closeAllConnections(); if (server.listening) await new Promise((resolve) => server.close(resolve));
  report.model_requests = modelCalls.map(({ release, ...entry }) => ({ ...entry, sha256: sha(JSON.stringify(entry.body)) }));
  report.asr_requests = asrCalls.map(({ release, ...entry }) => entry);
  report.tts_requests = ttsCalls.map(({ release, ...entry }) => entry);
  report.synthetic_calls = { model: modelCalls.length, asr: asrCalls.length, tts: ttsCalls.length };
  report.actual_mutation_requests = report.backend_requests.filter((item) => item.method !== 'GET').map(({ phase, method, path: route, status, body }) => ({
    phase, method, path: route, status, request_id: body?.request_id, match: body?.match, expected_revision: body?.expected_revision,
  }));
  report.renderer_and_provider_errors = errors; report.finished_at = new Date().toISOString(); save();
  console.log(JSON.stringify({ status: report.status, cases: report.cases.map(({ name, status }) => ({ name, status })), output, temporary_fixture: temporary }));
});
