'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { validateRequest } = require('../lib/security.cjs');

const SID = 'a'.repeat(32), TID = 'b'.repeat(32), RESPONSE = 'c'.repeat(32), JOB = 'd'.repeat(32);
const TARGET_A = { guide_id: 'guide-' + 'e'.repeat(32), revision_id: 'revision-' + 'f'.repeat(32), game: '雾棋', platform: 'PC', mode: '排位' };
const TARGET_B = { ...TARGET_A, guide_id: 'guide-' + '1'.repeat(32), revision_id: 'revision-' + '2'.repeat(32) };
const source = (name) => fs.readFileSync(path.join(__dirname, '../renderer', name), 'utf8');
const drain = async () => { for (let index = 0; index < 40; index++) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const frame = (id) => ({ source_id: id, source_name: id, frame_id: 'synthetic-frame', captured_at: Date.now(), data_url: 'data:image/png;base64,c3ludGhldGlj' });

class Element {
  constructor() {
    this.value = ''; this.hidden = false; this.disabled = false; this.checked = false;
    this.dataset = {}; this.style = {}; this.textContent = ''; this.children = []; this.listeners = new Map();
    this.scrollHeight = 50; this.scrollTop = 0; this.clientHeight = 100;
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  focus() {} add() {} scrollIntoView() {} reportValidity() { return true; }
}

// Production renderer scripts run unchanged. The bridge, device and provider
// surfaces are synthetic; no operating-system media permission is requested.
async function harness({ request, selectedSource = () => null, realMedia = false, realGuidePanel = false, now, vision = {} } = {}) {
  const elements = new Map(), requests = [], storage = new Map(), listeners = new Map();
  const calls = { begin: [], text: [], audioStarts: 0, audioStops: 0, vision: [] };
  let selectedVision = null;
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const document = {
    body: new Element(), visibilityState: 'visible', getElementById: byId,
    createElement: () => new Element(), createTextNode: (text) => ({ textContent: text }),
    querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
  };
  const window = {
    addEventListener(name, callback) { listeners.set(name, callback); }, dispatchEvent() {},
    aiNekoGuidePanel: { create: () => ({ render() {}, status() {}, sourceAction: () => null, selectedSource }) },
    aiNeko: {
      request: async (payload) => {
        validateRequest(payload);
        requests.push(JSON.parse(JSON.stringify(payload)));
        const custom = await request?.(payload);
        if (custom !== undefined) return custom;
        let body = {};
        if (payload.path === '/api/config') body = { model_base_url: 'http://127.0.0.1:9000', model: 'synthetic' };
        else if (payload.path === '/api/sessions' && payload.method === 'GET') body = { sessions: [] };
        else if (payload.path === '/api/sessions' && payload.method === 'POST') body = { id: SID };
        else if (/\/turns$/.test(payload.path)) body = { id: TID, status: 'accepted', sent_seq: 0, context: { match: { match_id: null, expected_revision: 0 } } };
        else if (payload.path === '/api/guides') body = { revision: 0, guides: [], selections: [] };
        else if (/\/matches$/.test(payload.path)) body = { revision: 0, current: null, matches: [] };
        else if (payload.path.includes('/events')) body = { events: [], status: 'completed', last_seq: 0 };
        else if (payload.path === '/api/voice/synthesize') body = { audio_base64: 'c3ludGhldGlj', mime_type: 'audio/wav' };
        return { status: 200, body };
      },
      onStatus() {}, onAction() {}, getPreferences: async () => ({ scale: 1, alwaysOnTop: true }),
      status: async () => ({ state: 'ready' }), microphone: async () => {},
      stopVision: async () => { calls.vision.push('stop'); selectedVision = null; await vision.stop?.(); },
      selectVision: async (id) => { calls.vision.push(`select:${id}`); selectedVision = id; return vision.select ? vision.select(id) : frame(id); },
      captureVision: async () => { calls.vision.push('capture'); return vision.capture ? vision.capture() : frame(selectedVision); },
    },
    aiNekoCompanion: {
      flushPlayback: async () => {}, captureForTurn: async () => null, frameStillAllowed: () => true,
      stopSpeech() {}, stopRecording() {}, clearKeys() {},
      beginTurn: (...args) => calls.begin.push(args), text: (...args) => calls.text.push(args), done() {}, speakingTurn: () => false,
    },
  };
  class AudioContext {
    async resume() {} async decodeAudioData() { return {}; }
    createBufferSource() { return { connect() {}, disconnect() {}, start() { calls.audioStarts++; }, stop() { calls.audioStops++; } }; }
    createAnalyser() { return { fftSize: 256, connect() {}, disconnect() {}, getByteTimeDomainData(array) { array.fill(128); } }; }
  }
  const context = vm.createContext({
    window, document, URL, DOMException, AbortController, Event, performance, AudioContext, atob,
    MutationObserver: class { observe() {} disconnect() {} },
    crypto: { randomUUID }, navigator: { mediaDevices: {} },
    localStorage: { setItem: (key, value) => storage.set(key, value), getItem: (key) => storage.get(key), removeItem: (key) => storage.delete(key) },
    setTimeout: (callback, milliseconds) => { const timer = setTimeout(callback, milliseconds); if (milliseconds > 1000) timer.unref(); return timer; },
    clearTimeout, requestAnimationFrame: (callback) => callback.name === 'meter' ? 1 : setImmediate(callback), cancelAnimationFrame: clearImmediate,
    setInterval: () => 1, clearInterval() {},
    Date: class extends Date { static now() { return now ? now() : Date.now(); } },
  });
  if (realGuidePanel) vm.runInContext(source('guide-panel.js'), context, { filename: 'desktop/renderer/guide-panel.js' });
  vm.runInContext(source('app.js'), context, { filename: 'desktop/renderer/app.js' });
  if (realMedia) {
    byId('speak-replies').checked = true;
    vm.runInContext(source('media.js'), context, { filename: 'desktop/renderer/media.js' });
    window.aiNekoMedia = context.aiNekoMedia;
    vm.runInContext(source('companion.js'), context, { filename: 'desktop/renderer/companion.js' });
    const companion = window.aiNekoCompanion;
    window.aiNekoCompanion = Object.freeze({ ...companion, beginTurn: (...args) => { calls.begin.push(args); return companion.beginTurn(...args); } });
  }
  await drain();
  assert.equal(document.body.dataset.chatReady, 'true');
  return { chat: window.aiNekoChat, companion: window.aiNekoCompanion, requests, calls, byId,
    enableVision(id) { byId('vision-source').value = id; byId('vision-enabled').checked = true; return byId('vision-enabled').onchange({ target: byId('vision-enabled') }); },
    disableVision() { byId('vision-enabled').checked = false; return byId('vision-enabled').onchange({ target: byId('vision-enabled') }); },
    changeVision(id) { byId('vision-source').value = id; return byId('vision-source').onchange(); },
    async dispose() { await window.aiNekoChat.cancelTurn(); await window.aiNekoChat.invalidatePending(); await drain(); } };
}

const turnPosts = (app) => app.requests.filter((item) => item.method === 'POST' && /\/turns$/.test(item.path));
const cancels = (app) => app.requests.filter((item) => /\/requests\/[^/]+\/cancel$/.test(item.path));

test('control response event retaining its job ID is followed and spoken exactly once', async () => {
  let jobReads = 0;
  const app = await harness({ realMedia: true, request: (payload) => {
    if (payload.path === `/api/sessions/${SID}/turns/${TID}/events?after=0`) return {
      status: 200, body: { status: 'completed', last_seq: 1, control_job_id: JOB, events: [{ seq: 1, type: 'done', status: 'completed' }] },
    };
    if (payload.path === `/api/sessions/${SID}/control-jobs/${JOB}`) {
      if (++jobReads > 5) return { status: 409, body: { detail: { message: 'synthetic recursion guard' } } };
      return { status: 200, body: { status: 'completed', response_turn: {
        id: RESPONSE, input: '', status: 'completed', delivered_text: '', context: { match: { match_id: null, expected_revision: 0 } },
      } } };
    }
    if (payload.path === `/api/sessions/${SID}/turns/${RESPONSE}/events?after=0`) return {
      status: 200, body: { status: 'completed', last_seq: 2, control_job_id: JOB, match_binding: { match_id: null, expected_revision: 0 },
        events: [{ seq: 1, type: 'text', text: '攻略已经切换。' }, { seq: 2, type: 'done', status: 'completed' }] },
    };
  } });
  try {
    await app.chat.submitText('按这份攻略'); await drain();
    assert.equal(jobReads, 1);
    assert.equal(app.calls.begin.filter(([id]) => id === RESPONSE).length, 1);
    assert.equal(app.calls.audioStarts, 1);
    assert.equal(app.calls.audioStops, 0);
    assert.equal(app.requests.filter((item) => item.path === `/api/sessions/${SID}/turns/${RESPONSE}/cancel`).length, 0);
    const tts = app.requests.filter((item) => item.path === '/api/voice/synthesize');
    assert.equal(tts.length, 1);
    assert.equal(tts[0].body.turn_id, RESPONSE);
    assert.deepEqual(tts[0].body.match, { match_id: null, expected_revision: 0 });
  } finally { await app.dispose(); }
});

test('a recovered replayed control job never resumes its historical confirmation speech', async () => {
  let jobReads = 0;
  const app = await harness({ realMedia: true, request: (payload) => {
    if (payload.path === `/api/sessions/${SID}/turns/${TID}/events?after=0`) return {
      status: 200, body: { status: 'completed', last_seq: 1, control_job_id: JOB, events: [{ seq: 1, type: 'done', status: 'completed' }] },
    };
    if (payload.path === `/api/sessions/${SID}/control-jobs/${JOB}`) {
      jobReads++;
      return { status: 200, body: { status: 'completed', replayed: true, committed: true, response_turn: {
        id: RESPONSE, input: '', status: 'completed', delivered_text: '历史确认不应重播。', context: { match: { match_id: null, expected_revision: 0 } },
      } } };
    }
  } });
  try {
    await app.chat.submitText('重启前已经提交的控制'); await drain();
    assert.equal(jobReads, 1);
    assert.equal(app.calls.begin.filter(([id]) => id === RESPONSE).length, 0);
    assert.equal(app.requests.some((item) => item.path.includes(`/turns/${RESPONSE}/events`)), false);
    assert.equal(app.requests.some((item) => item.path === '/api/voice/synthesize'), false);
    assert.equal(app.calls.audioStarts, 0);
  } finally { await app.dispose(); }
});

test('guide fetch cancellation rejects a late completed result before any save/adopt continuation', { timeout: 5000 }, async () => {
  for (const trigger of ['button', 'cancelTurn']) {
    const pollStarted = deferred(), lateCompletion = deferred(), cancelled = deferred();
    let requestId, continued = false;
    const app = await harness({ request: async (payload) => {
      if (payload.path === '/api/guides' && payload.method === 'POST') {
        requestId = payload.body.request_id;
        return { status: 202, body: { request_id: requestId, status: 'running' } };
      }
      if (payload.path === `/api/guide-operations/${requestId}`) {
        pollStarted.resolve();
        await lateCompletion.promise;
        return { status: 200, body: { status: 'completed', result: { ...TARGET_A } } };
      }
      if (payload.path === `/api/guide-operations/${requestId}/cancel`) {
        cancelled.resolve();
        return { status: 200, body: { request_id: requestId, status: 'cancelled' } };
      }
    } });
    let outcome;
    try {
      outcome = app.chat.runControl({ kind: 'guide-fetch', expectedRevision: 0,
        fields: { url: 'https://example.invalid/synthetic-guide', game: '雾棋', platform: 'PC', mode: '排位' },
      }).then((value) => { continued = true; return { value }; }, (error) => ({ error }));
      await pollStarted.promise;
      assert.equal(app.byId('cancel-guide-operation').hidden, false);
      if (trigger === 'button') app.byId('cancel-guide-operation').listeners.get('click')();
      else await app.chat.cancelTurn();
      await cancelled.promise;
      const cancellation = app.requests.filter((item) => item.path === `/api/guide-operations/${requestId}/cancel`);
      assert.equal(cancellation.length, 1, trigger);
      assert.equal(cancellation[0].method, 'POST');
      assert.deepEqual(cancellation[0].body, {});
      // The simulated server completion arrives after cancellation, matching a
      // fetch that committed just before the cancellation request reached it.
      lateCompletion.resolve();
      const result = await outcome;
      assert.equal(result.error?.code, 'cancelled', trigger);
      assert.equal(result.error?.status, undefined, trigger);
      assert.equal(result.error?.controlSettled, true, trigger);
      assert.match(result.error?.publicMessage, /已停止.*不再继续采用/, trigger);
      assert.equal(continued, false, 'a rejected operation must never continue into adoption');
      assert.equal(app.requests.some((item) => item.path === '/api/guide-selection'), false);
      assert.equal(app.byId('cancel-guide-operation').hidden, true);
    } finally {
      lateCompletion.resolve();
      await outcome;
      await app.dispose();
    }
  }
});

test('a lost guide fetch response retains exact request ID, CAS and fields while blocking a different control', async () => {
  let posts = 0, catalogRevision = 7;
  const app = await harness({ request: (payload) => {
    if (payload.path === '/api/guides' && payload.method === 'GET') {
      return { status: 200, body: { revision: catalogRevision, guides: [], selections: [] } };
    }
    if (payload.path === '/api/guides' && payload.method === 'POST') {
      if (++posts === 1) {
        catalogRevision = 8;
        throw new Error('synthetic response lost after server acceptance');
      }
      return { status: 200, body: { request_id: payload.body.request_id, status: 'completed', result: { ...TARGET_A } } };
    }
  } });
  const command = { kind: 'guide-fetch', expectedRevision: 7, fields: {
    url: 'https://example.invalid/synthetic-guide', game: '雾棋', platform: 'PC', mode: '排位',
    game_version: '合成版本 1', version_basis: '用户明确提供',
  } };
  try {
    await assert.rejects(app.chat.runControl(command), (error) => error.code === 'network');
    const first = app.requests.find((item) => item.path === '/api/guides' && item.method === 'POST');
    assert.match(first.body.request_id, /^[a-f0-9]{32}$/);
    assert.equal(first.body.expected_revision, 7);
    assert.equal(app.byId('notice-action').textContent, '核对上次操作');
    const requestCount = app.requests.length;
    await assert.rejects(app.chat.runControl({ kind: 'guide-backup' }), (error) => error.publicMessage.includes('先重试同一操作'));
    await assert.rejects(app.chat.runControl({ ...command, fields: { ...command.fields, url: 'https://example.invalid/different-guide' } }), (error) => error.publicMessage.includes('先重试同一操作'));
    assert.equal(app.requests.length, requestCount, 'a different operation must not reach the bridge while acceptance is uncertain');
    const result = await app.chat.runControl({ ...command, expectedRevision: catalogRevision, fields: { ...command.fields } });
    const requests = app.requests.filter((item) => item.path === '/api/guides' && item.method === 'POST');
    assert.equal(requests.length, 2);
    assert.deepEqual(requests[1], first, 'retry preserves the original CAS even after the catalog has changed');
    assert.equal(result.guide_id, TARGET_A.guide_id);
    assert.equal(result.revision_id, TARGET_A.revision_id);
    assert.equal(app.requests.some((item) => /\/guide-operations\/[^/]+\/cancel$/.test(item.path)), false);
    await app.chat.runControl({ kind: 'guide-backup' });
    assert.equal(app.requests.filter((item) => item.path === '/api/guide-backups' && item.method === 'POST').length, 1);
  } finally { await app.dispose(); }
});

for (const [terminal, message] of [
  [{ status: 'error', error: 'guide_fetch_failed' }, /读取或保存未能完成/],
  [{ status: 'error', error: 'guide_fetch_timeout' }, /网页读取超时/],
  [{ status: 'error', error: 'guide_unsupported_encoding' }, /不支持的压缩响应/],
  [{ status: 'error', error: 'guide_unsupported_content_type' }, /不是受支持的网页或纯文本/],
  [{ status: 'error', error: 'guide_response_too_large' }, /超过读取大小限制/],
  [{ status: 'error', error: 'guide_access_blocked' }, /不符合公开网页访问要求/],
  [{ status: 'error', error: 'guide_page_unavailable' }, /拒绝访问、需要登录或暂不提供正文/],
  [{ status: 'cancelled', error: null }, /操作已取消/],
  [{ status: 'cancelled', error: 'guide_removed' }, /攻略已被移除/],
  [{ status: 'error', error: 'guide_request_conflict' }, /资料或对局已更新/],
]) test(`real guide form renders ${terminal.error || terminal.status} and permits a fresh retry`, async () => {
  let requestId, polls = 0;
  const app = await harness({ realGuidePanel: true, request: (payload) => {
    if (payload.path === '/api/guides' && payload.method === 'POST') {
      requestId = payload.body.request_id;
      return { status: 202, body: { request_id: requestId, status: 'running' } };
    }
    if (payload.path === `/api/guide-operations/${requestId}`) {
      polls++;
      return { status: 200, body: { request_id: requestId, result: null, ...terminal } };
    }
  } });
  try {
    app.byId('guide-url').value = 'https://example.invalid/synthetic-guide';
    for (let attempt = 1; attempt <= 2; attempt++) {
      app.byId('guide-save-form').listeners.get('submit')({ preventDefault() {} });
      const deadline = Date.now() + 3000;
      while (polls < attempt || app.byId('guide-panel')['aria-busy'] !== 'false') {
        assert.ok(Date.now() < deadline, 'guide form must settle');
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      assert.match(app.byId('guide-status').textContent, message);
      assert.doesNotMatch(app.byId('guide-status').textContent, /已保存到本机|已采用/);
      if (terminal.error !== 'guide_request_conflict') assert.doesNotMatch(app.byId('guide-status').textContent, /资料或对局已更新/);
    }
    const posts = app.requests.filter((item) => item.path === '/api/guides' && item.method === 'POST');
    assert.equal(posts.length, 2);
    assert.notEqual(posts[0].body.request_id, posts[1].body.request_id, 'known terminal result frees the request slot');
    assert.equal(app.requests.some((item) => item.path === '/api/guide-selection'), false);
  } finally { await app.dispose(); }
});

test('real guide form keeps genuine HTTP revision conflict distinct from read failure', async () => {
  const app = await harness({ realGuidePanel: true, request: (payload) => {
    if (payload.path === '/api/guides' && payload.method === 'POST') return { status: 409, body: { detail: { code: 'guide_request_conflict' } } };
  } });
  try {
    app.byId('guide-url').value = 'https://example.invalid/synthetic-guide';
    app.byId('guide-save-form').listeners.get('submit')({ preventDefault() {} }); await drain();
    assert.match(app.byId('guide-status').textContent, /资料或对局已更新/);
    assert.equal(app.requests.some((item) => item.path.startsWith('/api/guide-operations/')), false);
  } finally { await app.dispose(); }
});

for (const failure of ['POST-500', 'POST-500-then-409', 'poll-503', 'poll-408', 'poll-401', 'poll-404', 'poll-deadline', 'unknown-status']) {
  test(`guide fetch ${failure} retains its exact operation until the committed outcome is recovered`, async () => {
    let posts = 0, requestId, catalogRevision = 7, clockReads = 0;
    const app = await harness({
      now: failure === 'poll-deadline' ? () => ++clockReads * 130001 : undefined,
      request: (payload) => {
        if (payload.path === '/api/guides' && payload.method === 'GET') return { status: 200, body: { revision: catalogRevision, guides: [], selections: [] } };
        if (payload.path === '/api/guides' && payload.method === 'POST') {
          requestId = payload.body.request_id;
          posts++;
          if (failure === 'POST-500-then-409' && posts === 2) return { status: 409, body: { detail: { code: 'guide_request_conflict' } } };
          if (posts > 1) return { status: 202, body: { request_id: requestId, status: 'completed', result: { ...TARGET_A } } };
          catalogRevision = 8;
          return failure.startsWith('POST-500') ? { status: 500, body: {} }
            : { status: 202, body: { request_id: requestId, status: failure === 'unknown-status' ? 'unexpected' : 'running' } };
        }
        if (payload.path === `/api/guide-operations/${requestId}`) return { status: Number(failure.slice(5)), body: {} };
      },
    });
    const command = { kind: 'guide-fetch', expectedRevision: 7, fields: { url: 'https://example.invalid/synthetic-guide' } };
    try {
      await assert.rejects(app.chat.runControl(command), (error) => {
        assert.notEqual(error.controlSettled, true);
        if (failure === 'poll-deadline') assert.match(error.publicMessage, /查询超时.*可能仍在运行/);
        if (failure === 'unknown-status') assert.match(error.publicMessage, /结果尚未确认/);
        return true;
      });
      await assert.rejects(app.chat.runControl({ kind: 'guide-backup' }), (error) => /先重试同一操作/.test(error.publicMessage));
      if (failure === 'POST-500-then-409') {
        await assert.rejects(app.chat.runControl(command), (error) => error.status === 409);
        await assert.rejects(app.chat.runControl({ kind: 'guide-backup' }), (error) => /先重试同一操作/.test(error.publicMessage));
      }
      const result = await app.chat.runControl({ ...command, expectedRevision: 8 });
      assert.equal(result.guide_id, TARGET_A.guide_id);
      const sent = app.requests.filter((item) => item.path === '/api/guides' && item.method === 'POST');
      for (const retry of sent.slice(1)) assert.deepEqual(retry, sent[0], 'retry must preserve accepted ID, CAS and complete payload');
      await app.chat.runControl({ kind: 'guide-backup' });
    } finally { await app.dispose(); }
  });
}

test('an explicitly newly selected source changes the intent of an otherwise identical retry', async () => {
  let selected = TARGET_A, posts = 0;
  const app = await harness({ selectedSource: () => selected, request: (payload) => {
    if (/\/turns$/.test(payload.path) && ++posts === 1) throw new Error('synthetic lost acceptance');
  } });
  try {
    await app.chat.submitText('按这份攻略');
    const first = turnPosts(app)[0];
    selected = TARGET_B;
    await app.chat.submitText('按这份攻略'); await drain();
    const second = turnPosts(app)[1];
    assert.notEqual(second.body.request_id, first.body.request_id);
    assert.equal(second.body.guide_target.guide_id, TARGET_B.guide_id);
    assert.ok(cancels(app).some((item) => item.path.includes(first.body.request_id)));
    assert.ok(app.requests.findIndex((item) => item.path.includes(first.body.request_id) && item.path.endsWith('/cancel')) < app.requests.findIndex((item) => item.method === 'POST' && item.body?.request_id === second.body.request_id));
  } finally { await app.dispose(); }
});

for (const reset of ['cleared', 'same-source-new-catalog']) test(`lost acceptance retains its original target after ${reset}`, async () => {
  let selected = TARGET_A, posts = 0, revision = 0;
  const app = await harness({ selectedSource: () => selected, request: (payload) => {
    if (payload.path === '/api/guides') return { status: 200, body: { revision, guides: [], selections: [] } };
    if (/\/turns$/.test(payload.path) && ++posts === 1) throw new Error('synthetic lost acceptance');
  } });
  try {
    await app.chat.submitText('按这份攻略');
    const first = turnPosts(app)[0];
    revision = 1;
    selected = reset === 'cleared' ? null : { ...TARGET_A };
    await app.chat.submitText('按这份攻略'); await drain();
    assert.deepEqual(turnPosts(app)[1].body, first.body);
    assert.equal(cancels(app).length, 0);
  } finally { await app.dispose(); }
});

test('a new plain question durably retires the previous lost acceptance before posting', async () => {
  let posts = 0;
  const app = await harness({ request: (payload) => {
    if (/\/turns$/.test(payload.path) && ++posts === 1) throw new Error('synthetic lost plain acceptance');
  } });
  try {
    await app.chat.submitText('旧问题');
    const first = turnPosts(app)[0];
    await app.chat.submitText('改问一个新问题'); await drain();
    const second = turnPosts(app)[1];
    assert.notEqual(second.body.request_id, first.body.request_id);
    const revoke = app.requests.findIndex((item) => item.path.includes(first.body.request_id) && item.path.endsWith('/cancel'));
    assert.ok(revoke >= 0, 'lost plain acceptance must remain revocable');
    assert.ok(revoke < app.requests.findIndex((item) => item.method === 'POST' && item.body?.request_id === second.body.request_id));
  } finally { await app.dispose(); }
});

test('a failed retirement blocks replacement questions until its tombstone is confirmed', async () => {
  let posts = 0, cancellationAvailable = false;
  const app = await harness({ request: (payload) => {
    if (/\/turns$/.test(payload.path) && ++posts === 1) throw new Error('synthetic lost plain acceptance');
    if (/\/requests\/[^/]+\/cancel$/.test(payload.path) && !cancellationAvailable) throw new Error('synthetic cancellation unavailable');
  } });
  try {
    await app.chat.submitText('旧问题');
    const first = turnPosts(app)[0];
    await app.chat.submitText('新的问题'); await drain();
    assert.equal(turnPosts(app).length, 1, 'a failed revocation must not allow a replacement POST');
    assert.ok(cancels(app).some((item) => item.path.includes(first.body.request_id)));
    await app.chat.submitText('又一个新问题'); await drain();
    assert.equal(turnPosts(app).length, 1, 'later Enter/voice submission must not bypass the unresolved revocation');
    cancellationAvailable = true;
    await app.chat.cancelTurn();
    await app.chat.submitText('撤销确认后再问'); await drain();
    assert.equal(turnPosts(app).length, 2);
  } finally { cancellationAvailable = true; await app.dispose(); }
});

function matchServer({ closeGate } = {}) {
  const match = { match_id: 'match-' + '3'.repeat(32), game: '雾棋', platform: 'PC', mode: '排位', status: 'active', state_revision: 1, selection: null, observations: [] };
  const server = { revision: 1, closes: 0, match,
    request: async (payload) => {
      const base = `/api/sessions/${SID}/matches`;
      if (payload.path === base && payload.method === 'GET') return { status: 200, body: { revision: server.revision, current: { ...match }, matches: [{ ...match }] } };
      if (payload.path === `${base}/${match.match_id}` && payload.method === 'GET') return { status: 200, body: { ...match, observations: [...match.observations] } };
      if (payload.path === `${base}/${match.match_id}/close-observation`) {
        server.closes++;
        await closeGate?.promise;
        assert.equal(payload.body.expected_revision, server.revision);
        server.revision++;
        match.state_revision = server.revision;
        match.observations = [];
        return { status: 200, body: { match_id: match.match_id, revision: server.revision } };
      }
    },
  };
  return server;
}

test('enabling match observation completes its backend close without cancelling its own selection', async () => {
  const server = matchServer();
  const app = await harness({ realMedia: true, request: server.request });
  try {
    await app.chat.captureBinding();
    await app.enableVision('window:synthetic-A');
    assert.equal(server.closes, 1);
    assert.equal(app.byId('vision-enabled').checked, true);
    assert.equal(app.byId('vision-preview').hidden, false);
    assert.deepEqual(app.calls.vision, ['stop', 'select:window:synthetic-A']);
    const capture = await app.companion.captureForTurn();
    assert.equal(capture.frame.source_id, 'window:synthetic-A');
    assert.equal(app.companion.frameStillAllowed(capture), true);

    // Only the actual close-observation endpoint clears this simulated server
    // evidence. A client checkbox change alone cannot satisfy the assertion.
    server.match.observations = [{ source_kind: 'vision', text: '合成画面：31金币' }];
    const stopping = app.disableVision();
    assert.equal(app.companion.frameStillAllowed(capture), false);
    await stopping;
    assert.equal(server.closes, 2);
    assert.deepEqual(server.match.observations, []);
    assert.equal(app.byId('vision-enabled').checked, false);
    assert.equal(app.byId('vision-preview').hidden, true);
    assert.equal(await app.companion.captureForTurn(), null);
    assert.equal((await app.chat.captureBinding()).match.expected_revision, server.revision);
  } finally { await app.dispose(); }
});

test('a direct match-panel close still disables local vision before invalidating server evidence', async () => {
  const server = matchServer();
  const app = await harness({ realMedia: true, request: server.request });
  try {
    await app.chat.captureBinding();
    await app.enableVision('window:synthetic-A');
    const capture = await app.companion.captureForTurn();
    server.match.observations = [{ source_kind: 'vision', text: '合成画面：31金币' }];
    await app.chat.runControl({ kind: 'match-close-observation', targetId: server.match.match_id, expectedRevision: server.revision });
    assert.equal(server.closes, 2);
    assert.deepEqual(server.match.observations, []);
    assert.equal(app.companion.frameStillAllowed(capture), false);
    assert.equal(app.byId('vision-enabled').checked, false);
    assert.equal(app.byId('vision-preview').hidden, true);
    assert.equal(await app.companion.captureForTurn(), null);
  } finally { await app.dispose(); }
});

for (const action of ['uncheck', 'change-source']) test(`pending backend observation close cannot reopen after ${action}`, async () => {
  const closeGate = deferred();
  const server = matchServer({ closeGate });
  const app = await harness({ realMedia: true, request: server.request });
  try {
    await app.chat.captureBinding();
    const enabling = app.enableVision('window:old-A');
    await drain();
    assert.equal(server.closes, 1);
    assert.equal(app.byId('vision-enabled').checked, true);
    assert.equal(await app.companion.captureForTurn(), null);
    const cancelling = action === 'uncheck' ? app.disableVision() : app.changeVision('window:new-B');
    assert.equal(app.byId('vision-enabled').checked, false);
    closeGate.resolve();
    await Promise.all([enabling, cancelling]); await drain();
    assert.equal(app.calls.vision.some((item) => item.startsWith('select:')), false);
    assert.equal(app.byId('vision-enabled').checked, false);
    assert.equal(app.byId('vision-preview').hidden, true);
    assert.equal(await app.companion.captureForTurn(), null);
  } finally { closeGate.resolve(); await app.dispose(); }
});
