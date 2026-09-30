'use strict';
// Actual source Electron/IPC/UI acceptance. All data and model HTTP are synthetic.
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
const outputIndex = args.indexOf('--output');
const output = path.resolve(outputIndex < 0 ? path.join(root, 'artifacts/guides-evaluation/electron-local.json') : args[outputIndex + 1]);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-guide-electron-'));
const dataRoot = path.join(temporary, 'synthetic');
const hash = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
fs.mkdirSync(path.dirname(output), { recursive: true });
const tracked = ['desktop/tests/guide-local.smoke.cjs', 'desktop/renderer/app.js', 'desktop/renderer/index.html',
  'src/ai_neko/chat/graph.py', 'src/ai_neko/runtime/guides.py', 'src/ai_neko/memory/guide_retrieval.py',
  'src/ai_neko/runtime/service.py', 'uv.lock', 'desktop/package-lock.json'];
const sourceHashes = Object.fromEntries(tracked.map((name) => [name, hash(path.join(root, name))]));
const report = {
  status: 'RUNNING', platform: process.platform, node: process.version,
  source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  source_dirty: Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim()),
  source_hashes: sourceHashes, source_execution: true, packaged_windows: 'NOT_RUN', windows_11: 'NOT_RUN',
  G5_management_buttons: 'NOT_RUN', real_model_calls: 0, real_search_calls: 0, real_audio_service_calls: 0,
  actual_user_microphone_captures: 0, actual_desktop_captures: 0, model_quality: 'NOT_RUN',
  cases: [], questions: [], screenshots: [],
};
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
  env.USERPROFILE = profile;
  env.LOCALAPPDATA = path.join(profile, 'AppData', 'Local');
  env.APPDATA = path.join(profile, 'AppData', 'Roaming');
}
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const bootstrap = `
import json, sys
from datetime import datetime, timezone
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.service import MemoryService
paths = initialize_data_root(sys.argv[1])
memory = MemoryService(paths)
try:
    docs=[]
    for ordinal, text in enumerate((
        '合成说明 1.0：灯芯是潮灯守卫。灯芯负责照亮码头，不是商店装备，也不代表玩家已经执行任何操作。',
        '合成说明 2.0：航灯是雾岸领航员。航灯负责标记航道，不负责照亮码头，也不代表玩家已经执行任何操作。',
    ), 1):
        url=f'https://guides.example.test/electron/g{ordinal}'
        docs.append(memory.guides.ingest({
            'status':'read','completeness':'full','completeness_reasons':[],
            'text':text,'url':url,'original_url':url,'final_url':url,
            'title':f'合成术语说明 {ordinal}.0','retrieved_at':datetime.now(timezone.utc).isoformat(),
            'content_date':None,'headings':[],
        }, game='synthetic-guide',platform='pc',mode='notes',game_version=f'{ordinal}.0',
           version_basis=f'合成正文明确写明说明 {ordinal}.0'))
    print(json.dumps({'scope':memory.scope,'documents':docs,'control':memory.guides.control_snapshot()},ensure_ascii=False))
finally:
    memory.close()
`;
let fixture;
let application;
let page;
const errors = [];
const modelCalls = [];
const releaseStreams = new Set();
let unexpectedRequests = 0;
let emittedToolCalls = 0;
const server = http.createServer((request, response) => {
  const chunks = [];
  request.on('data', (chunk) => chunks.push(chunk));
  request.on('end', async () => {
    try {
      if (request.url !== '/v1/chat/completions' || request.method !== 'POST') {
        unexpectedRequests++;
        response.writeHead(404);
        response.end();
        return;
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const entry = { body, first_sent_at: Date.now(), release: null };
      modelCalls.push(entry);
      const evidenceMessage = body.messages.findLast((message) => message.role === 'user' && typeof message.content === 'string' && message.content.includes('"sources"'));
      const evidence = evidenceMessage ? JSON.parse(evidenceMessage.content.slice(evidenceMessage.content.indexOf('{'))) : null;
      entry.evidence = evidence;
      const second = evidence?.sources?.[0]?.guide_id === fixture.documents[1].guide_id;
      entry.first_fragment = second ? '航灯是雾岸领航员。' : '灯芯是潮灯守卫。';
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      response.write('data: ' + JSON.stringify({ choices: [{ delta: { content: entry.first_fragment } }] }) + '\n\n');
      await new Promise((resolve) => {
        entry.release = () => { releaseStreams.delete(entry.release); resolve(); };
        releaseStreams.add(entry.release);
        response.once('close', entry.release);
      });
      if (response.destroyed) return;
      response.write('data: ' + JSON.stringify({ choices: [{ delta: { content: '这是已采用本地说明中的术语 [S1]。' } }] }) + '\n\n');
      response.end('data: [DONE]\n\n');
    } catch (error) {
      errors.push('synthetic provider: ' + error.message);
      response.destroy();
    }
  });
});

async function check(name, operation) {
  const item = { name, status: 'RUNNING' };
  report.cases.push(item);
  save();
  try { await operation(); item.status = 'PASS'; }
  catch (error) { item.status = 'FAIL'; throw error; }
  finally { save(); }
}

async function latestTurn() {
  return page.evaluate(async () => {
    const id = localStorage.getItem('ai-neko.desktop.last-session');
    return (await window.aiNekoChat.api('/api/sessions/' + id)).turns.at(-1);
  });
}

async function adopt(document) {
  return page.evaluate(async (doc) => {
    const current = await window.aiNekoChat.api('/api/guides');
    return window.aiNekoChat.api('/api/guide-selection', {
      method: 'PUT', body: { request_id: crypto.randomUUID().replaceAll('-', ''), expected_revision: current.revision,
        game: doc.game, platform: doc.platform, mode: doc.mode, guide_id: doc.guide_id, revision_id: doc.revision_id },
    });
  }, document);
}

async function question(mode, text, document, screenshotName) {
  await page.locator(mode === 'chat' ? '#mode-chat' : '#mode-guide').click();
  const hint = await page.locator('#mode-hint').innerText();
  assert.ok(hint.includes('采用资料'), hint);
  const count = modelCalls.length;
  await page.locator('#message-input').fill(text);
  await page.locator('#message-form').evaluate((form) => form.requestSubmit());
  const firstText = document.guide_id === fixture.documents[0].guide_id ? '灯芯是潮灯守卫。' : '航灯是雾岸领航员。';
  await page.waitForFunction(({ fragment, question }) => {
    const turn = document.querySelector('.turn:last-child');
    return turn?.querySelector('.user-message')?.textContent === question && turn?.querySelector('.assistant-output')?.textContent.includes(fragment);
  }, { fragment: firstText, question: text }, { timeout: 15000 });
  const firstVisibleAt = Date.now();
  const running = await latestTurn();
  assert.equal(running.status, 'running', 'first fragment must render before provider stream ends');
  assert.equal(modelCalls.length, count + 1, 'local path must invoke the model only once');
  const call = modelCalls.at(-1);
  assert.equal(call.evidence?.local_retrieval?.status, 'sufficient');
  const sources = call.evidence.sources;
  assert.ok(sources.length > 0 && sources.length <= 6);
  assert.ok(sources.every((source) => source.local === true && source.guide_id === document.guide_id && source.revision_id === document.revision_id));
  assert.equal(sources[0].id, 'S1');
  const full = await page.evaluate((id) => window.aiNekoChat.api('/api/guides/' + id), document.guide_id);
  for (const source of sources) {
    assert.match(source.chunk_id, /^chunk-[a-f0-9]{32}$/);
    assert.equal(source.text, full.guide.text.slice(source.start, source.end));
  }
  assert.ok(sources.some((source) => source.text.includes(firstText)));
  assert.ok(!call.body.messages.some((message) => message.role === 'tool'));
  call.release();
  await page.waitForFunction(() => document.querySelector('#cancel-turn').hidden, null, { timeout: 15000 });
  const completed = await latestTurn();
  assert.equal(completed.status, 'completed');
  const events = await page.evaluate(async (turnId) => {
    const id = localStorage.getItem('ai-neko.desktop.last-session');
    return (await window.aiNekoChat.api(`/api/sessions/${id}/turns/${turnId}/events?after=0`)).events;
  }, completed.turn_id || completed.id);
  assert.equal(events.filter((event) => event.type === 'tool').length, 0, 'no search/page tool execution');
  assert.ok(events.some((event) => event.type === 'status' && event.status === 'local_retrieval' && event.retrieval.status === 'sufficient'));
  assert.ok(events.some((event) => event.type === 'source' && event.source.local === true));
  assert.equal(modelCalls.length, count + 1);
  const card = page.locator('.turn').last().locator('.source-card').first();
  await card.locator('summary').click();
  const visible = await card.innerText();
  for (const label of ['本地保存', '已采用', '原文版本 ' + document.game_version, '上次核查', '原文位置', '字符']) assert.ok(visible.includes(label), label + ': ' + visible);
  assert.ok(visible.includes(firstText));
  const shot = path.join(path.dirname(output), path.basename(output, '.json') + '-' + screenshotName + '.png');
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: shot, omitBackground: true });
  report.screenshots.push({ file: path.basename(shot), sha256: hash(shot) });
  report.questions.push({ mode, question: text, status: completed.status, local_status: call.evidence.local_retrieval.status,
    model_requests: modelCalls.length - count, search_tool_events: 0, read_page_tool_events: 0,
    first_fragment_visible_while_running: true, observed_first_fragment_ms: firstVisibleAt - call.first_sent_at,
    sources, source_card_text: visible, assistant_history: call.body.messages.filter((message) => message.role === 'assistant'),
    model_request_sha256: crypto.createHash('sha256').update(JSON.stringify(call.body)).digest('hex') });
  return call;
}

(async () => {
  fixture = JSON.parse(execFileSync(python, ['-c', bootstrap, dataRoot], { cwd: root, env, encoding: 'utf8', timeout: 30000 }));
  assert.equal(fixture.scope, JSON.stringify(['local', 'default']).replace(',', ', '));
  assert.deepEqual(fixture.control.selections, []);
  report.bootstrap = { scoped_to_default_memory_service: true, initial_selection_count: 0, documents: fixture.documents.map((item) => ({ guide_id: item.guide_id, revision_id: item.revision_id })) };
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  application = await _electron.launch({ executablePath: require('../node_modules/electron'), args: [path.join(root, 'desktop')], cwd: root, env, timeout: 45000 });
  report.runtime_versions = await application.evaluate(() => process.versions);
  page = await application.firstWindow();
  await page.waitForLoadState('domcontentloaded');
  if (page.url().includes('/consent/')) await page.locator('#accept').click();
  for (let index = 0; index < 200; index++) {
    const renderer = application.windows().find((window) => window.url().includes('/renderer/'));
    if (renderer) { page = renderer; break; }
    await wait(100);
  }
  page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForSelector('body[data-chat-ready="true"]', { timeout: 45000 });
  await page.waitForSelector('#pet-stage[data-loaded="true"]', { timeout: 45000 });
  await check('configure_synthetic_model_via_actual_settings_ui_with_search_unconfigured', async () => {
    await page.locator('#open-settings').click();
    await page.locator('#model-base-url').fill(`http://127.0.0.1:${server.address().port}/v1`);
    await page.locator('#model-name').fill('synthetic-guide-model');
    await page.locator('#model-api-key').fill('');
    await page.locator('#search-api-key').fill('');
    // Keep the app's default URL; an absent search credential leaves search
    // unconfigured without submitting an invalid empty endpoint preference.
    await page.locator('#search-base-url').fill('https://api.tavily.com');
    await page.locator('#settings-form').evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector('#settings-panel').hidden);
    const config = await page.evaluate(() => window.aiNekoChat.api('/api/config'));
    report.model_configuration = config.config || config;
    assert.equal(report.model_configuration.search_api_key_set || report.model_configuration.search_key_set || false, false);
    assert.equal(await page.locator('#speak-replies').isChecked(), false);
  });
  await check('adopt_persisted_public_guide_via_real_authenticated_ipc', async () => {
    const catalogue = await page.evaluate(() => window.aiNekoChat.api('/api/guides'));
    assert.equal(catalogue.guides.length, 2);
    assert.deepEqual(catalogue.selections, []);
    const adopted = await adopt(fixture.documents[0]);
    assert.equal(adopted.selection.guide_id, fixture.documents[0].guide_id);
    assert.equal(adopted.selection.revision_id, fixture.documents[0].revision_id);
    report.adoption = adopted;
  });
  await check('plain_chat_renders_streaming_local_source_and_precise_identity', async () => {
    await question('chat', '这份说明里的灯芯是什么意思？', fixture.documents[0], 'plain');
  });
  await check('on_demand_mode_uses_one_local_answer_without_search_configuration', async () => {
    await question('guide', '请说明灯芯是什么。', fixture.documents[0], 'on-demand');
  });
  await check('switch_via_ipc_remaps_S1_and_excludes_previous_guide_advice', async () => {
    const changed = await adopt(fixture.documents[1]);
    assert.equal(changed.previous_selection.guide_id, fixture.documents[0].guide_id);
    const call = await question('guide', '这份说明里的航灯是什么意思？', fixture.documents[1], 'switched');
    const assistantHistory = call.body.messages.filter((message) => message.role === 'assistant').map((message) => message.content).join('\n');
    assert.ok(!assistantHistory.includes('灯芯是潮灯守卫'));
    assert.ok(!JSON.stringify(call.evidence.sources).includes(fixture.documents[0].guide_id));
    assert.equal(call.evidence.sources[0].id, 'S1');
    report.switch = { actual_ipc: true, source_id_reused_with_new_document: true, old_advice_absent: true };
  });
  assert.equal(modelCalls.length, 3);
  assert.equal(unexpectedRequests, 0);
  assert.equal(emittedToolCalls, 0);
  assert.deepEqual(errors, []);
  report.source_files_unchanged = tracked.every((name) => hash(path.join(root, name)) === sourceHashes[name]);
  assert.ok(report.source_files_unchanged, 'source changed while smoke was running');
  report.status = 'PASS';
})().catch(async (error) => {
  report.status = 'FAIL';
  report.error = error.stack;
  process.exitCode = 1;
  console.error(error.stack);
  if (page && !page.isClosed()) {
    report.ui_failure = await page.evaluate(() => ({ status: document.querySelector('#connection-status')?.textContent,
      notice: document.querySelector('#notice')?.textContent, message: document.querySelector('.turn:last-child')?.textContent })).catch(() => ({}));
    const shot = output.replace(/\.json$/, '') + '-failure.png';
    await page.screenshot({ path: shot, omitBackground: true }).catch(() => {});
    if (fs.existsSync(shot)) report.screenshots.push({ file: path.basename(shot), sha256: hash(shot) });
  }
}).finally(async () => {
  for (const release of releaseStreams) release();
  if (application) await application.close().catch(() => {});
  server.closeAllConnections();
  if (server.listening) await new Promise((resolve) => server.close(resolve));
  report.synthetic_model_requests = modelCalls.length;
  report.synthetic_model_tool_calls = emittedToolCalls;
  report.unexpected_fixture_http_requests = unexpectedRequests;
  report.renderer_errors = errors;
  report.temporary_data_removed = true;
  fs.rmSync(temporary, { recursive: true, force: true });
  save();
  console.log(JSON.stringify({ status: report.status, output, cases: report.cases, synthetic_model_requests: modelCalls.length }));
});
