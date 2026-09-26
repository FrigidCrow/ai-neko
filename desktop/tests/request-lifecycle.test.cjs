'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { validateRequest } = require('../lib/security.cjs');

const source = fs.readFileSync(path.join(__dirname, '../renderer/app.js'), 'utf8');
const SID = 'a'.repeat(32), TID = 'b'.repeat(32);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let index = 0; index < 12; index++) await tick(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

class Element {
  constructor() {
    this.value = ''; this.hidden = false; this.disabled = false; this.dataset = {};
    this.style = {}; this.textContent = ''; this.children = []; this.listeners = new Map();
    this.scrollHeight = 50; this.scrollTop = 0; this.clientHeight = 100;
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  setAttribute() {}
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  focus() {}
  requestSubmit() { return this.listeners.get('submit')?.({ preventDefault() {} }); }
}

async function harness({ request, capture, flush, stopRecording, plain = false } = {}) {
  const elements = new Map(), requests = [], storage = new Map(), calls = { captures: 0, begin: [], text: [], stops: 0, recordingsStopped: 0 };
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const document = {
    body: new Element(), visibilityState: 'visible', getElementById: byId,
    createElement: () => new Element(), createTextNode: (text) => ({ textContent: text }),
    querySelectorAll: () => [], addEventListener() {},
  };
  const window = {
    addEventListener() {}, dispatchEvent() {},
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
        else if (/\/turns$/.test(payload.path)) body = { id: TID, status: 'accepted', sent_seq: 0 };
        else if (payload.path.includes('/events')) body = { events: [], status: 'completed', last_seq: 0 };
        return { status: 200, body };
      },
      onStatus() {}, onAction() {}, getPreferences: async () => ({ scale: 1, alwaysOnTop: true }),
      status: async () => ({ state: 'ready' }),
    },
    aiNekoCompanion: {
      flushPlayback: async () => flush?.(),
      captureForTurn: async () => {
        calls.captures++;
        return capture ? capture() : plain ? null : { epoch: 0, frame: { frame_id: `frame-${calls.captures}`, source_id: 'window:synthetic:0', captured_at: Date.now(), data_url: 'data:image/png;base64,SYNTHETIC' } };
      },
      frameStillAllowed: () => true,
      stopSpeech: () => { calls.stops++; },
      stopRecording: () => { calls.recordingsStopped++; stopRecording?.(); },
      beginTurn: (...args) => calls.begin.push(args),
      text: (...args) => calls.text.push(args), done() {}, speakingTurn: () => false,
    },
  };
  const context = vm.createContext({
    window, document, URL, DOMException, AbortController, Event, performance, crypto: { randomUUID },
    localStorage: { setItem: (key, value) => storage.set(key, value), getItem: (key) => storage.get(key), removeItem: (key) => storage.delete(key) },
    setTimeout: (callback, milliseconds) => { const timer = setTimeout(callback, milliseconds); if (milliseconds > 1000) timer.unref(); return timer; },
    clearTimeout, requestAnimationFrame: (callback) => setImmediate(callback),
  });
  vm.runInContext(source, context, { filename: 'desktop/renderer/app.js' });
  await drain();
  assert.equal(document.body.dataset.chatReady, 'true');
  const chat = window.aiNekoChat;
  return { chat, requests, calls, storage, byId, async dispose() { chat.invalidatePending(); await drain(); } };
}

const turnPosts = (app) => app.requests.filter((item) => item.method === 'POST' && /\/turns$/.test(item.path));
const requestCancels = (app) => app.requests.filter((item) => /\/requests\/[^/]+\/cancel$/.test(item.path));

test('lost visual acceptance retries the identical frame and request; a new question captures anew', async () => {
  let attempts = 0;
  const app = await harness({ request: async (payload) => {
    if (/\/turns$/.test(payload.path) && ++attempts === 1) throw new Error('synthetic response lost after acceptance');
  } });
  try {
    await app.chat.submitText('同一个问题');
    assert.equal(app.byId('message-input').value, '同一个问题');
    await app.chat.submitText('同一个问题'); await drain();
    assert.equal(app.calls.captures, 1);
    const [first, retry] = turnPosts(app);
    assert.deepEqual(retry.body, first.body);
    assert.equal(app.calls.begin.length, 1);
    await app.chat.submitText('新的问题'); await drain();
    const next = turnPosts(app)[2];
    assert.equal(app.calls.captures, 2);
    assert.notEqual(next.body.request_id, first.body.request_id);
    assert.notEqual(next.body.image.frame_id, first.body.image.frame_id);
    assert.deepEqual([...app.storage.keys()], ['ai-neko.desktop.last-session']);
    assert.equal([...app.storage.values()].some((value) => value.includes('SYNTHETIC')), false);
  } finally { await app.dispose(); }
});

test('revoking during capture prevents session or turn POST after the frame arrives', async () => {
  const frame = deferred();
  const app = await harness({ capture: () => frame.promise });
  try {
    const sending = app.chat.submitText('旧画面'); await drain();
    assert.equal(app.calls.captures, 1);
    await app.chat.revokeVision();
    frame.resolve({ epoch: 0, frame: { frame_id: 'revoked-frame' } });
    await sending; await drain();
    assert.equal(app.requests.filter((item) => item.method === 'POST').length, 0);
    assert.equal(app.calls.begin.length, 0);
  } finally { await app.dispose(); }
});

test('revoking while session creation is pending never posts its captured image', async () => {
  const session = deferred();
  const app = await harness({ request: (payload) => payload.method === 'POST' && payload.path === '/api/sessions' ? session.promise : undefined });
  try {
    const sending = app.chat.submitText('会话尚未建立'); await drain();
    await app.chat.revokeVision();
    session.resolve({ status: 201, body: { id: SID } });
    await sending;
    assert.equal(turnPosts(app).length, 0);
    assert.equal(app.calls.begin.length, 0);
  } finally { await app.dispose(); }
});

test('revoking an unconfirmed POST tombstones the request and a late accepted response never starts playback or polling', async () => {
  const accepted = deferred();
  const app = await harness({ request: (payload) => /\/turns$/.test(payload.path) ? accepted.promise : undefined });
  try {
    const sending = app.chat.submitText('响应尚未到达'); await drain();
    const pending = turnPosts(app)[0];
    const revoked = app.chat.revokeVision();
    app.byId('message-input').value = '新的输入不能被覆盖';
    await revoked;
    assert.equal(requestCancels(app)[0].path, `/api/sessions/${SID}/requests/${pending.body.request_id}/cancel`);
    assert.deepEqual(requestCancels(app)[0].body, {});
    accepted.resolve({ status: 202, body: { id: TID } });
    await sending; await drain();
    assert.ok(app.requests.some((item) => item.path === `/api/sessions/${SID}/turns/${TID}/cancel`));
    assert.equal(app.calls.begin.length, 0);
    assert.equal(app.requests.some((item) => item.path.includes('/events')), false);
    assert.equal(app.byId('message-input').value, '新的输入不能被覆盖');
  } finally { await app.dispose(); }
});

test('revoking after response loss still cancels by request identity and the next attempt uses a fresh image', async () => {
  let attempts = 0;
  const app = await harness({ request: (payload) => {
    if (/\/turns$/.test(payload.path) && ++attempts === 1) throw new Error('synthetic response loss');
  } });
  try {
    await app.chat.submitText('第一次视觉提问');
    const first = turnPosts(app)[0];
    await app.chat.revokeVision();
    assert.equal(requestCancels(app).length, 1);
    assert.ok(requestCancels(app)[0].path.includes(first.body.request_id));
    await app.chat.submitText('第一次视觉提问'); await drain();
    assert.equal(app.calls.captures, 2);
    assert.notEqual(turnPosts(app)[1].body.request_id, first.body.request_id);
    assert.notEqual(turnPosts(app)[1].body.image.frame_id, first.body.image.frame_id);
  } finally { await app.dispose(); }
});

test('revoking an accepted visual turn stops polling and ignores a late text batch', async () => {
  const events = deferred();
  const app = await harness({ request: (payload) => payload.path.includes('/events') ? events.promise : undefined });
  try {
    await app.chat.submitText('已经接受的视觉问题'); await drain();
    assert.equal(app.calls.begin.length, 1);
    const stops = app.calls.stops;
    await app.chat.revokeVision();
    assert.ok(app.calls.stops > stops);
    events.resolve({ status: 200, body: { events: [{ seq: 1, type: 'text', text: '不可播放的旧图回复' }], status: 'running', last_seq: 1 } });
    await drain();
    assert.equal(app.calls.text.length, 0);
    assert.equal(requestCancels(app)[0].path, `/api/sessions/${SID}/requests/${turnPosts(app)[0].body.request_id}/cancel`);
    assert.equal(app.byId('cancel-turn').hidden, true);
  } finally { await app.dispose(); }
});

test('a late revoked response cannot clear or replace a newer submission', async () => {
  const oldResponse = deferred(), newResponse = deferred(); let attempts = 0;
  const newTurn = 'c'.repeat(32);
  const app = await harness({ request: (payload) => {
    if (/\/turns$/.test(payload.path)) return ++attempts === 1 ? oldResponse.promise : newResponse.promise;
  } });
  try {
    const old = app.chat.submitText('旧来源问题'); await drain();
    await app.chat.revokeVision();
    const current = app.chat.submitText('新来源问题'); await drain();
    oldResponse.resolve({ status: 202, body: { id: TID } });
    await old; await drain();
    assert.equal(app.byId('message-input').value, '新来源问题');
    assert.equal(app.byId('message-input').disabled, true);
    assert.equal(app.calls.begin.length, 0);
    newResponse.resolve({ status: 202, body: { id: newTurn } });
    await current; await drain();
    assert.deepEqual(app.calls.begin, [[newTurn, SID]]);
    assert.equal(app.byId('message-input').value, '');
    assert.equal(app.calls.captures, 2);
  } finally { await app.dispose(); }
});

test('failed request revocation remains retryable without retaining a captured image', async () => {
  let cancels = 0;
  const app = await harness({ request: (payload) => {
    if (/\/turns$/.test(payload.path)) throw new Error('synthetic response loss');
    if (payload.path.includes('/requests/') && ++cancels === 1) throw new Error('synthetic cancel response loss');
  } });
  try {
    await app.chat.submitText('撤销重试');
    const original = turnPosts(app)[0];
    await assert.rejects(app.chat.revokeVision(), /synthetic cancel response loss/);
    await app.chat.revokeVision();
    assert.equal(requestCancels(app).length, 2);
    assert.equal(requestCancels(app)[0].path, requestCancels(app)[1].path);
    assert.ok(requestCancels(app)[1].path.includes(original.body.request_id));
    assert.equal(app.calls.begin.length, 0);
    assert.deepEqual([...app.storage.keys()], ['ai-neko.desktop.last-session']);
  } finally { await app.dispose(); }
});

test('failed cancellation of an accepted visual turn retries its request identity without another image upload', async () => {
  const events = deferred(); let cancellations = 0;
  const app = await harness({ request: (payload) => {
    if (payload.path.includes('/events')) return events.promise;
    if (payload.path.endsWith('/cancel') && ++cancellations === 1) throw new Error('synthetic accepted-turn cancellation failure');
  } });
  try {
    await app.chat.submitText('已接受回合的取消重试'); await drain();
    const posted = turnPosts(app)[0];
    assert.equal(app.calls.begin.length, 1);
    await assert.rejects(app.chat.revokeVision(), /synthetic accepted-turn cancellation failure/);
    assert.equal(app.byId('cancel-turn').hidden, true);
    events.resolve({ status: 200, body: { events: [{ seq: 1, type: 'text', text: '不能播放' }], status: 'completed', last_seq: 1 } });
    await drain();
    assert.equal(app.calls.text.length, 0);
    await app.chat.revokeVision();
    const retried = requestCancels(app);
    assert.equal(retried.length, 2);
    assert.equal(retried[0].path, `/api/sessions/${SID}/requests/${posted.body.request_id}/cancel`);
    assert.deepEqual(retried[1], retried[0]);
    assert.deepEqual(retried[1].body, {});
    assert.equal(turnPosts(app).length, 1);
    assert.equal(app.calls.captures, 1);
    assert.equal(app.calls.begin.length, 1);
  } finally { await app.dispose(); }
});

for (const cancellation of ['signal', 'isCurrent']) {
  test(`obsolete ASR waiting for an active turn cannot overwrite input or stop a newer recording (${cancellation})`, async () => {
    const events = deferred();
    const app = await harness({ plain: true, request: (payload) => payload.path.includes('/events') ? events.promise : undefined });
    try {
      await app.chat.submitText('上一轮'); await drain();
      const controller = new AbortController(); let current = true;
      const pending = app.chat.submitText('旧识别文字', { signal: controller.signal, isCurrent: () => current });
      const cancelled = assert.rejects(pending, { name: 'AbortError' });
      app.byId('message-input').value = '新输入';
      const stops = app.calls.recordingsStopped;
      if (cancellation === 'signal') controller.abort(); else current = false;
      events.resolve({ status: 200, body: { events: [], status: 'completed', last_seq: 0 } });
      await cancelled; await drain();
      assert.equal(app.byId('message-input').value, '新输入');
      assert.equal(app.calls.recordingsStopped, stops);
      assert.equal(turnPosts(app).length, 1);
    } finally { await app.dispose(); }
  });
}

test('already cancelled ASR leaves the draft and recording untouched', async () => {
  const app = await harness();
  try {
    const controller = new AbortController(); controller.abort();
    app.byId('message-input').value = '用户正在编辑';
    const stops = app.calls.recordingsStopped;
    await assert.rejects(app.chat.submitText('过期结果', { signal: controller.signal }), { name: 'AbortError' });
    assert.equal(app.byId('message-input').value, '用户正在编辑');
    assert.equal(app.calls.recordingsStopped, stops);
    assert.equal(turnPosts(app).length, 0);
  } finally { await app.dispose(); }
});

test('submission owns accepted ASR text before its own stopRecording aborts the recorder signal', async () => {
  const controller = new AbortController();
  const app = await harness({ stopRecording: () => controller.abort() });
  try {
    await app.chat.submitText('有效的新语音', { signal: controller.signal, isCurrent: () => !controller.signal.aborted });
    await drain();
    assert.equal(controller.signal.aborted, true);
    assert.equal(turnPosts(app).length, 1);
    assert.equal(turnPosts(app)[0].body.text, '有效的新语音');
    assert.equal(app.calls.begin.length, 1);
  } finally { await app.dispose(); }
});

test('request cancellation bridge accepts only scoped opaque request IDs', () => {
  assert.doesNotThrow(() => validateRequest({ method: 'POST', path: `/api/sessions/${SID}/requests/${'c'.repeat(32)}/cancel`, body: {} }));
  for (const requestId of ['arbitrary', 'c'.repeat(31), 'C'.repeat(32), '../turns', '%2e%2e']) {
    assert.throws(() => validateRequest({ method: 'POST', path: `/api/sessions/${SID}/requests/${requestId}/cancel`, body: {} }));
  }
  assert.throws(() => validateRequest({ method: 'GET', path: `/api/sessions/${SID}/requests/${'c'.repeat(32)}/cancel` }));
});
