'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { SpeechQueue, encodeVoiceWav } = require('../renderer/media.js');
const source = fs.readFileSync(path.join(__dirname, '../renderer/companion.js'), 'utf8');
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function nonvoiceFixture(api) {
  const server = {
    persona: { name: 'Saved name', user_name: 'Saved user', traits: ['calm'], speaking_style: '', catchphrase: '', game_style: '', version: 1 },
    memories: [], memoryConfig: { auto_extract: false }, backups: { revision: 1, backups: [] },
    extractionOverview: { pending: 0, running: 0, failed: 0, cancelled: 0, context_limit_exceeded: false, latest: null },
  };
  const f = fixture({ api: (route, options) => {
    const custom = api?.(route, options, server);
    if (custom !== undefined) return custom;
    if (route === '/api/persona') {
      if (options?.method === 'PUT') server.persona = { ...server.persona, ...options.body, version: server.persona.version + 1 };
      return { ...server.persona };
    }
    if (route === '/api/memory/config') {
      if (options?.method === 'PUT') server.memoryConfig = { ...options.body };
      return { ...server.memoryConfig };
    }
    if (route === '/api/memory/backups') return { ...server.backups };
    if (route === '/api/memories') return { memories: server.memories.map((item) => ({ ...item })), extraction_overview: structuredClone(server.extractionOverview) };
    if (route.startsWith('/api/memories/') && options?.method === 'PUT') {
      const item = server.memories.find((item) => route.endsWith('/' + item.id));
      Object.assign(item, options.body); return { ...item };
    }
    return {};
  } });
  return { ...f, server };
}

const unavailable = (label = 'read') => Object.assign(new Error('Synthetic ' + label + ' 503'), { status: 503 });

// Run the production renderer unchanged. Every device, frame and service below
// is synthetic; these tests never request an operating-system media permission.
function fixture(overrides = {}) {
  const nodes = new Map(), streams = [], recorders = [], timers = new Map(), listeners = new Map();
  let activeElement = null;
  const calls = { cancel: 0, asr: 0, decoded: 0, sink: 0, resumed: 0, submitted: [], microphone: [], vision: [], api: [] };
  const makeNode = (tag = '') => ({ tagName: tag.toUpperCase(), value: '', checked: false, hidden: false, textContent: '', attributes: {}, dataset: {}, children: [],
    setAttribute(key, value) { this.attributes[key] = value; }, removeAttribute(key) { delete this[key]; },
    contains(target) { return this === target || this.children.some((child) => child.contains?.(target)); },
    replaceChildren(...children) { if (activeElement && this.contains(activeElement)) activeElement = null; this.children = [...children]; this.textContent = ''; },
    setSelectionRange(start, end, direction) { this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction; },
    append(...children) { this.children.push(...children); }, focus() { activeElement = this; }, add(child) { this.children.push(child); } });
  const el = (id) => {
    if (!nodes.has(id)) nodes.set(id, makeNode());
    return nodes.get(id);
  };
  const makeStream = (device = 'default') => {
    const track = { stopped: false, events: {}, stop() { this.stopped = true; }, addEventListener(name, callback) { this.events[name] = callback; } };
    const stream = { device, track, getTracks: () => [track], getAudioTracks: () => [track] }; streams.push(stream); return stream;
  };
  class Recorder {
    static isTypeSupported() { return true; }
    constructor(stream) { this.stream = stream; this.state = 'inactive'; this.mimeType = 'audio/webm'; recorders.push(this); }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['synthetic']) }); void this.onstop?.(); }); }
  }
  class Reader { async readAsDataURL(blob) { this.result = `data:${blob.type};base64,${Buffer.from(await blob.arrayBuffer()).toString('base64')}`; this.onload(); } }
  const decoded = () => ({ sampleRate: 48000, length: 480, numberOfChannels: 1, getChannelData: () => new Float32Array(480).fill(0.2) });
  class Context {
    async setSinkId() { calls.sink++; if (overrides.sink) await overrides.sink(); }
    async resume() { calls.resumed++; if (overrides.resume) await overrides.resume(); }
    async decodeAudioData(value) { calls.decoded++; return overrides.decode ? overrides.decode(value, decoded) : decoded(); }
  }
  let observer;
  class Observer { constructor(callback) { observer = callback; } observe() {} disconnect() {} }
  const chat = {
    canRecord: () => true,
    captureBinding: async () => overrides.binding ? overrides.binding() : ({ sessionId: 'a'.repeat(32), contextEpoch: 0, match: { match_id: null, expected_revision: 0 } }),
    bindingCurrent: (binding) => overrides.bindingCurrent ? overrides.bindingCurrent(binding) : true,
    cancelControlConfirmation: async () => {},
    closeObservation: async () => overrides.closeObservation?.(),
    cancelTurn: async () => { calls.cancel++; if (overrides.cancel) await overrides.cancel(calls.cancel); },
    api: async (route, options) => { calls.api.push({ route, options }); if (route === '/api/voice/transcribe') { calls.asr++; return overrides.transcribe ? overrides.transcribe(options) : { text: '合成问题' }; } return overrides.api ? overrides.api(route, options) : {}; },
    invalidatePending: async () => overrides.invalidate?.(),
    refreshAfterForget: async () => {},
    submitText: async (text, options) => { calls.submitted.push({ text, options }); if (overrides.submit) await overrides.submit(text, options); },
    revokeVision: async () => { calls.vision.push('revoke'); if (overrides.revoke) await overrides.revoke(); },
    friendlyError: (error) => error.message,
  };
  const frame = (id) => ({ source_id: id, source_name: id, frame_id: 'synthetic', captured_at: Date.now(), data_url: 'data:image/jpeg;base64,c3ludGhldGlj' });
  const bridge = {
    microphone: async (enabled) => { calls.microphone.push(enabled); if (overrides.permission) await overrides.permission(enabled); },
    stopVision: async () => { calls.vision.push('stop'); },
    selectVision: async (id) => { calls.vision.push(`select:${id}`); return overrides.select ? overrides.select(id) : frame(id); },
    captureVision: async () => overrides.capture ? overrides.capture() : frame(el('vision-source').value),
    onAction(callback) { this.action = callback; },
  };
  let timerId = 0;
  const scope = {
    window: { aiNeko: bridge, aiNekoChat: chat, aiNekoMedia: { SpeechQueue, encodeVoiceWav }, dispatchEvent(event) { listeners.get(event.type)?.(event); }, addEventListener(name, callback) { listeners.set(name, callback); } },
    document: { body: { dataset: {} }, get activeElement() { return activeElement; }, getElementById: el, createElement: makeNode, querySelector: () => null, querySelectorAll: () => [] },
    navigator: { mediaDevices: { getUserMedia: async (constraints) => overrides.media ? overrides.media(constraints, makeStream) : makeStream(constraints.audio.deviceId?.exact || 'default'), enumerateDevices: async () => overrides.devices ? overrides.devices() : [] } },
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    MediaRecorder: Recorder, FileReader: Reader, AudioContext: Context, MutationObserver: Observer, CustomEvent: class {}, Blob, AbortController, DOMException, crypto: globalThis.crypto, performance,
    setTimeout(callback) { const id = ++timerId; timers.set(id, () => { timers.delete(id); return callback(); }); return id; }, clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(source, scope, { filename: 'companion.js' });
  return { el, calls, streams, recorders, timers, bridge, chat, frame, companion: scope.window.aiNekoCompanion,
    recording: () => scope.document.body.dataset.recording === 'true', close: () => listeners.get('pagehide')(),
    start: () => el('record-voice').onclick(), stop: () => el('stop-audio').onclick(),
    openSettings: () => { el('settings-panel').hidden = false; listeners.get('ai-neko-settings')(); },
    reconnect: () => listeners.get('ai-neko-connected')(), focused: () => activeElement,
    closeSettings: () => { el('settings-panel').hidden = true; observer(); },
    enable: (id) => { el('vision-source').value = id; el('vision-enabled').checked = true; return el('vision-enabled').onchange({ target: el('vision-enabled') }); },
    disable: () => { el('vision-enabled').checked = false; return el('vision-enabled').onchange({ target: el('vision-enabled') }); },
  };
}

for (const area of ['memories', 'backups', 'memoryConfig', 'persona']) {
  test('non-voice ' + area + ' clears only its recovered load error', async () => {
    const route = { memories: '/api/memories', backups: '/api/memory/backups', memoryConfig: '/api/memory/config', persona: '/api/persona' }[area];
    const statusId = { memories: 'memory-status', backups: 'memory-backup-status', memoryConfig: 'memory-config-status', persona: 'persona-status' }[area];
    let fail = true;
    const f = nonvoiceFixture((path) => path === route && fail ? Promise.reject(unavailable(area)) : undefined);
    f.el('settings-message').textContent = 'Other section save failure';
    f.openSettings(); await tick(); assert.match(f.el(statusId).textContent, /503/);
    fail = false; f.reconnect(); await tick();
    assert.doesNotMatch(f.el(statusId).textContent, /503/);
    assert.equal(f.el('settings-message').textContent, 'Other section save failure');
  });
}

test('non-voice memory list and extraction configuration fail and recover independently', async () => {
  let listFails = true, configFails = false, backupsFail = true;
  const f = nonvoiceFixture((route) => {
    if (route === '/api/memories' && listFails) return Promise.reject(unavailable('list'));
    if (route === '/api/memory/config' && configFails) return Promise.reject(unavailable('config'));
    if (route === '/api/memory/backups' && backupsFail) return Promise.reject(unavailable('backups'));
  });
  f.server.memoryConfig.auto_extract = true;
  f.openSettings(); await tick();
  assert.equal(f.el('memory-auto-extract').checked, true);
  assert.match(f.el('memory-status').textContent, /list 503/);
  assert.equal(f.el('memory-config-status').textContent, '');
  listFails = false; configFails = true; f.reconnect(); await tick();
  assert.equal(f.el('memory-status').textContent, '');
  assert.match(f.el('memory-config-status').textContent, /config 503/);
  assert.match(f.el('memory-backup-status').textContent, /backups 503/);
  configFails = false; await f.el('refresh-memories').onclick();
  assert.equal(f.el('memory-config-status').textContent, '');
  assert.match(f.el('memory-backup-status').textContent, /backups 503/);
  backupsFail = false; await f.el('refresh-memory-backups').onclick();
  assert.equal(f.el('memory-backup-status').textContent, '');
});

test('memory extraction overview keeps no-job state separate from stored facts', async () => {
  const f = nonvoiceFixture();
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'explicitly saved fact' }];
  f.openSettings(); await tick();
  assert.match(f.el('memory-extraction-status').textContent, /还没有自动抽取任务记录/);
  assert.equal(f.el('memory-list').children[0].children[0].value, 'explicitly saved fact');
  assert.equal(f.el('memory-status').textContent, '');
});

for (const latest of ['pending', 'running']) for (const attempts of [1, 3]) {
  test('memory extraction ' + latest + ' attempt ' + attempts + ' does not claim content was saved or will be retried', async () => {
    const f = nonvoiceFixture();
    Object.assign(f.server.extractionOverview, { pending: 2, running: 1, latest: { status: latest, attempts } });
    f.openSettings(); await tick();
    const message = f.el('memory-extraction-status').textContent;
    assert.match(message, /待处理 2，运行状态 1/);
    assert.match(message, /未完成状态不保证会继续处理/);
    assert.match(message, /尚未确认是否保存/);
    assert.doesNotMatch(message, /已保存|一定|自动重试/);
    assert.equal(f.el('memory-list').children.length, 0);
  });
}

test('memory extraction historical failures do not override a successful manual save or drafts', async () => {
  const f = nonvoiceFixture();
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'first' }, { id: 'two', kind: 'fact', content: 'second' }];
  Object.assign(f.server.extractionOverview, { failed: 3, cancelled: 2, latest: { status: 'completed', attempts: 1 } });
  f.openSettings(); await tick();
  const first = f.el('memory-list').children[0], second = f.el('memory-list').children[1];
  first.children[0].value = 'unsaved draft'; second.children[0].value = 'edited second';
  await second.children[2].onclick();
  const message = f.el('memory-extraction-status').textContent;
  assert.match(message, /历史任务累计：失败 3，取消 2/);
  assert.match(message, /不代表本次手动保存的结果/);
  assert.match(message, /抽取完成；是否保存内容请以下方记忆列表为准/);
  assert.match(message, /新消息中明确重述/);
  assert.doesNotMatch(message, /最近提交的任务：抽取失败|已保存|自动重试/);
  assert.match(f.el('memory-status').textContent, /记忆已经更新/);
  assert.equal(f.el('memory-list').children[0], first);
  assert.equal(first.children[0].value, 'unsaved draft');
});

for (const [latest, expected] of [['failed', /最近提交的任务：抽取失败/], ['cancelled', /已取消，不会自动重试/]]) {
  test('memory extraction ' + latest + ' explains how to restate unsaved content', async () => {
    const f = nonvoiceFixture();
    Object.assign(f.server.extractionOverview, { [latest]: 1, latest: { status: latest, attempts: 3 } });
    f.openSettings(); await tick();
    const message = f.el('memory-extraction-status').textContent;
    assert.match(message, expected);
    assert.match(message, /未保存的内容需要在新消息中明确重述/);
    assert.doesNotMatch(message, /已保存|容量/);
  });
}

test('memory extraction current capacity warning clears independently of historical failures', async () => {
  const f = nonvoiceFixture();
  Object.assign(f.server.extractionOverview, { failed: 4, context_limit_exceeded: true, latest: { status: 'failed', attempts: 1 } });
  f.openSettings(); await tick();
  assert.match(f.el('memory-extraction-status').textContent, /当前记忆上下文超出自动整理容量/);
  assert.match(f.el('memory-extraction-status').textContent, /明确保存仍可使用/);
  f.server.extractionOverview.context_limit_exceeded = false;
  await f.el('refresh-memories').onclick();
  assert.doesNotMatch(f.el('memory-extraction-status').textContent, /容量/);
  assert.match(f.el('memory-extraction-status').textContent, /历史任务累计：失败 4/);
});

test('memory extraction unavailable metadata does not block older response fact lists', async () => {
  const f = nonvoiceFixture();
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'stored fact' }];
  const valid = { ...f.server.extractionOverview };
  const invalid = [undefined, null, {}, { ...valid, pending: -1 }, { ...valid, failed: '1' },
    { ...valid, context_limit_exceeded: undefined }, { ...valid, latest: undefined },
    { ...valid, latest: { status: 'unknown', attempts: 1 } }, { ...valid, latest: { status: 'completed' } }];
  f.openSettings(); await tick();
  for (const overview of invalid) {
    f.server.extractionOverview = overview;
    await f.el('refresh-memories').onclick();
    assert.match(f.el('memory-extraction-status').textContent, /后台抽取状态暂不可用/);
    assert.equal(f.el('memory-list').children[0].children[0].value, 'stored fact');
    assert.equal(f.el('memory-status').textContent, '');
  }
});

test('memory extraction reopening and read failure mark old success unknown without clearing edit errors', async () => {
  let pendingRead = null, failRead = false;
  const f = nonvoiceFixture((route, options) => {
    if (route === '/api/memories/one' && options?.method === 'PUT') return Promise.reject(unavailable('save'));
    if (route === '/api/memories' && !options?.method) {
      if (pendingRead) return pendingRead.promise;
      if (failRead) return Promise.reject(unavailable('list'));
    }
  });
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'stored fact' }];
  f.server.extractionOverview.latest = { status: 'completed', attempts: 1 };
  f.openSettings(); await tick();
  const card = f.el('memory-list').children[0]; card.children[0].value = 'unsaved edit';
  await card.children[2].onclick();
  assert.match(f.el('memory-status').textContent, /save 503/);
  pendingRead = deferred(); f.closeSettings(); f.openSettings();
  assert.match(f.el('memory-extraction-status').textContent, /暂不可用/);
  assert.doesNotMatch(f.el('memory-extraction-status').textContent, /抽取完成/);
  pendingRead.reject(unavailable('list')); await tick(); pendingRead = null; failRead = true;
  assert.match(f.el('memory-status').textContent, /save 503/);
  assert.match(f.el('memory-status').textContent, /list 503/);
  assert.match(f.el('memory-extraction-status').textContent, /暂不可用/);
  failRead = false; await f.el('refresh-memories').onclick();
  assert.match(f.el('memory-extraction-status').textContent, /抽取完成/);
  assert.match(f.el('memory-status').textContent, /save 503/);
  assert.doesNotMatch(f.el('memory-status').textContent, /list 503/);
  assert.equal(f.el('memory-list').children[0], card);
  assert.equal(card.children[0].value, 'unsaved edit');
});

for (const result of ['success', 'failure']) {
  test('memory extraction stale ' + result + ' cannot replace the newest overview', async () => {
    const pending = deferred(); let reads = 0;
    const f = nonvoiceFixture((route) => route === '/api/memories' && ++reads === 1 ? pending.promise : undefined);
    f.server.extractionOverview.latest = { status: 'running', attempts: 2 };
    f.server.extractionOverview.running = 1;
    f.openSettings(); await tick(); f.reconnect(); await tick();
    const current = f.el('memory-extraction-status').textContent;
    assert.match(current, /最近提交的任务：尚未完成/);
    if (result === 'failure') pending.reject(unavailable('old list'));
    else pending.resolve({ memories: [], extraction_overview: { pending: 0, running: 0, failed: 99, cancelled: 0,
      context_limit_exceeded: true, latest: { status: 'failed', attempts: 3 } } });
    await tick();
    assert.equal(f.el('memory-extraction-status').textContent, current);
    assert.doesNotMatch(f.el('memory-status').textContent, /old list/);
  });
}

for (const area of ['memories', 'backups', 'persona']) for (const outcome of ['success', 'failure']) {
  test('non-voice stale ' + area + ' ' + outcome + ' cannot replace a newer recovered read', async () => {
    const pending = deferred(); let reads = 0;
    const route = { memories: '/api/memories', backups: '/api/memory/backups', persona: '/api/persona' }[area];
    const f = nonvoiceFixture((path) => path === route && ++reads === 1 ? pending.promise : undefined);
    f.server.memories = [{ id: 'one', kind: 'fact', content: 'new memory' }];
    f.server.persona.name = 'new persona'; f.server.backups.revision = 22;
    f.openSettings(); await tick(); f.reconnect(); await tick();
    if (outcome === 'failure') pending.reject(unavailable('obsolete'));
    else pending.resolve(area === 'memories' ? { memories: [] } : area === 'backups' ? { revision: 1, backups: [] } : { name: 'obsolete', version: 0 });
    await tick();
    assert.equal(f.el('memory-list').children[0].children[0].value, 'new memory');
    assert.equal(f.el('persona-name').value, 'new persona');
    assert.equal(f.el('memory-backup-list').dataset.revision, '22');
    for (const id of ['memory-status', 'memory-backup-status', 'persona-status']) assert.doesNotMatch(f.el(id).textContent, /obsolete|503/);
  });
}

test('non-voice persona recovery preserves dirty fields and refreshes clean fields', async () => {
  const pending = deferred(); let first = true;
  const f = nonvoiceFixture((route) => {
    if (route === '/api/persona' && first) { first = false; return pending.promise; }
  });
  f.openSettings(); await tick(); f.el('persona-name').value = 'draft before first response';
  pending.resolve({ ...f.server.persona }); await tick();
  assert.equal(f.el('persona-name').value, 'draft before first response');
  assert.equal(f.el('persona-user-name').value, 'Saved user');
  f.server.persona.user_name = 'new saved user'; f.server.persona.name = 'new saved name';
  f.reconnect(); await tick(); f.closeSettings(); f.openSettings(); await tick();
  assert.equal(f.el('persona-name').value, 'draft before first response');
  assert.equal(f.el('persona-user-name').value, 'new saved user');
});

test('non-voice persona save does not overwrite typing while the save is pending', async () => {
  const pending = deferred();
  const f = nonvoiceFixture((route, options, server) => {
    if (route === '/api/persona' && options?.method === 'PUT') {
      server.persona = { ...server.persona, ...options.body, version: 2 }; return pending.promise;
    }
  });
  f.openSettings(); await tick(); f.el('persona-name').value = 'submitted';
  const saving = f.el('persona-form').onsubmit({ preventDefault() {} });
  f.el('persona-name').value = 'new unsaved name'; f.el('persona-catchphrase').value = 'new unsaved phrase';
  pending.resolve({}); await saving;
  assert.equal(f.el('persona-name').value, 'new unsaved name');
  assert.equal(f.el('persona-catchphrase').value, 'new unsaved phrase');
  assert.match(f.el('persona-status').textContent, /已保存/);
});

test('non-voice memory recovery and saving another row preserve editable draft nodes', async () => {
  const f = nonvoiceFixture();
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'first' }, { id: 'two', kind: 'fact', content: 'second' }];
  f.openSettings(); await tick();
  const first = f.el('memory-list').children[0], second = f.el('memory-list').children[1];
  first.children[0].value = 'unsaved first'; second.children[0].value = 'saved second';
  await second.children[2].onclick(); f.reconnect(); await tick();
  assert.equal(f.el('memory-list').children[0], first);
  assert.equal(first.children[0].value, 'unsaved first');
  assert.equal(second.children[0].value, 'saved second');
  f.server.memories = [f.server.memories[1]]; f.reconnect(); await tick();
  assert.ok(f.el('memory-list').children.includes(first), 'a removed server row must not silently discard its unsaved draft');
});

test('non-voice an add-memory reply clears only the submitted draft', async () => {
  const pending = deferred(); let posts = 0;
  const f = nonvoiceFixture((route, options) => route === '/api/memories' && options?.method === 'POST' && ++posts === 1 ? pending.promise : undefined);
  f.openSettings(); await tick(); f.el('memory-content').value = 'submitted'; f.el('memory-kind').value = 'fact';
  const saving = f.el('memory-form').onsubmit({ preventDefault() {} });
  f.el('memory-content').value = 'next draft'; f.el('memory-kind').value = 'event';
  pending.resolve({}); await saving;
  assert.equal(f.el('memory-content').value, 'next draft'); assert.equal(f.el('memory-kind').value, 'event');
  await f.el('memory-form').onsubmit({ preventDefault() {} });
  assert.equal(f.el('memory-content').value, '');
});

test('non-voice an obsolete extraction PUT failure cannot reverse the latest saved choice', async () => {
  const pending = deferred(); let writes = 0;
  const f = nonvoiceFixture((route, options) => route === '/api/memory/config' && options?.method === 'PUT' && ++writes === 1 ? pending.promise : undefined);
  f.openSettings(); await tick(); const input = f.el('memory-auto-extract');
  input.checked = true; const old = input.onchange({ target: input });
  await tick(); input.checked = false; const latest = input.onchange({ target: input });
  pending.reject(unavailable('obsolete PUT')); await Promise.all([old, latest]);
  assert.equal(input.checked, false); assert.equal(f.server.memoryConfig.auto_extract, false);
  assert.doesNotMatch(f.el('memory-config-status').textContent, /503|obsolete/);
});

test('non-voice a delayed extraction GET cannot overwrite a user save', async () => {
  const pending = deferred(); let reads = 0;
  const f = nonvoiceFixture((route, options) => route === '/api/memory/config' && !options?.method && ++reads === 1 ? pending.promise : undefined);
  f.openSettings(); await tick(); const input = f.el('memory-auto-extract');
  input.checked = true; await input.onchange({ target: input });
  pending.resolve({ auto_extract: false }); await tick();
  assert.equal(input.checked, true);
});

for (const area of ['persona', 'memories', 'memoryConfig', 'backups']) {
  test('non-voice successful ' + area + ' GET cannot erase a failed mutation notice', async () => {
    const routes = { persona: '/api/persona', memories: '/api/memories', memoryConfig: '/api/memory/config', backups: '/api/memory/backups' };
    const statuses = { persona: 'persona-status', memories: 'memory-status', memoryConfig: 'memory-config-status', backups: 'memory-backup-status' };
    const f = nonvoiceFixture((route, options) => route === routes[area] && options?.method ? Promise.reject(unavailable('save')) : undefined);
    f.openSettings(); await tick();
    if (area === 'persona') await f.el('persona-form').onsubmit({ preventDefault() {} });
    if (area === 'memories') { f.el('memory-content').value = 'draft'; await f.el('memory-form').onsubmit({ preventDefault() {} }); }
    if (area === 'memoryConfig') { const input = f.el('memory-auto-extract'); input.checked = true; await input.onchange({ target: input }); }
    if (area === 'backups') await f.el('create-memory-backup').onclick();
    assert.match(f.el(statuses[area]).textContent, /save 503/);
    f.reconnect(); await tick();
    assert.match(f.el(statuses[area]).textContent, /save 503/);
  });
}

for (const area of ['persona', 'memoryRow', 'memoryNew']) {
  test('non-voice an obsolete ' + area + ' save success cannot hide a newer save failure', async () => {
    const pending = deferred(); let writes = 0;
    const route = area === 'persona' ? '/api/persona' : area === 'memoryRow' ? '/api/memories/one' : '/api/memories';
    const f = nonvoiceFixture((path, options) => {
      if (path === route && options?.method) return ++writes === 1 ? pending.promise : Promise.reject(unavailable('latest save'));
    });
    f.server.memories = [{ id: 'one', kind: 'fact', content: 'original' }];
    f.openSettings(); await tick();
    const input = area === 'persona' ? f.el('persona-name') : area === 'memoryRow' ? f.el('memory-list').children[0].children[0] : f.el('memory-content');
    const save = area === 'persona' ? () => f.el('persona-form').onsubmit({ preventDefault() {} }) : area === 'memoryRow'
      ? () => f.el('memory-list').children[0].children[2].onclick() : () => f.el('memory-form').onsubmit({ preventDefault() {} });
    input.value = 'submitted first'; const old = save(); await tick();
    input.value = 'new failed draft'; const latest = save();
    pending.resolve({}); await Promise.all([old, latest]);
    assert.equal(input.value, 'new failed draft');
    assert.match(f.el(area === 'persona' ? 'persona-status' : 'memory-status').textContent, /latest save 503/);
  });
}

for (const area of ['persona', 'memoryRow']) {
  test('non-voice ' + area + ' save preserves an edit back to the previous saved value', async () => {
    const pending = deferred();
    const route = area === 'persona' ? '/api/persona' : '/api/memories/one';
    const f = nonvoiceFixture((path, options, server) => {
      if (path !== route || options?.method !== 'PUT') return;
      if (area === 'persona') server.persona.name = options.body.name;
      else server.memories[0].content = options.body.content;
      return pending.promise;
    });
    f.server.memories = [{ id: 'one', kind: 'fact', content: 'original' }];
    f.openSettings(); await tick();
    const input = area === 'persona' ? f.el('persona-name') : f.el('memory-list').children[0].children[0];
    const original = input.value; input.value = 'submitted replacement';
    const saving = area === 'persona' ? f.el('persona-form').onsubmit({ preventDefault() {} }) : f.el('memory-list').children[0].children[2].onclick();
    await tick(); input.value = original;
    f.reconnect(); await tick(); assert.equal(input.value, original, 'a read during the write must preserve a reverted draft');
    pending.resolve({}); await saving;
    assert.equal(input.value, original, 'the accepted save owns only its submitted snapshot');
    f.reconnect(); await tick(); assert.equal(input.value, original);
  });
}

test('non-voice extraction reads during overlapping writes cannot replace the latest intent', async () => {
  const first = deferred(), second = deferred(); let writes = 0;
  const f = nonvoiceFixture((route, options, server) => {
    if (route !== '/api/memory/config' || options?.method !== 'PUT') return;
    if (++writes === 1) { server.memoryConfig.auto_extract = true; return first.promise; }
    return second.promise;
  });
  f.openSettings(); await tick(); const input = f.el('memory-auto-extract');
  input.checked = true; const old = input.onchange({ target: input });
  await tick();
  input.checked = false; const latest = input.onchange({ target: input });
  f.reconnect(); await tick(); assert.equal(input.checked, false);
  assert.equal(writes, 1, 'the later mutation cannot pass the first write');
  first.resolve({}); await old; await tick();
  f.server.memoryConfig.auto_extract = false; second.resolve({}); await latest;
  assert.equal(input.checked, false); assert.equal(f.server.memoryConfig.auto_extract, false);
});

test('non-voice extraction writes serialize real persistence and coalesce superseded queued choices', async () => {
  const first = deferred(); const writes = [];
  const f = nonvoiceFixture((route, options, server) => {
    if (route !== '/api/memory/config' || options?.method !== 'PUT') return;
    writes.push(options.body.auto_extract);
    const commit = () => { server.memoryConfig = { ...options.body }; return { ...server.memoryConfig }; };
    return writes.length === 1 ? first.promise.then(commit) : commit();
  });
  f.openSettings(); await tick(); const input = f.el('memory-auto-extract');
  input.checked = true; const old = input.onchange({ target: input }); await tick();
  input.checked = false; const superseded = input.onchange({ target: input });
  input.checked = true; const supersededAgain = input.onchange({ target: input });
  input.checked = false; const latest = input.onchange({ target: input }); await tick();
  assert.deepEqual(writes, [true]);
  first.resolve(); await Promise.all([old, superseded, supersededAgain, latest]);
  assert.deepEqual(writes, [true, false]);
  assert.equal(input.checked, false); assert.equal(f.server.memoryConfig.auto_extract, false);
});

test('non-voice latest extraction failure restores the last committed value instead of an obsolete baseline', async () => {
  const first = deferred(); let writes = 0;
  const f = nonvoiceFixture((route, options, server) => {
    if (route !== '/api/memory/config' || options?.method !== 'PUT') return;
    if (++writes === 1) return first.promise.then(() => { server.memoryConfig = { ...options.body }; return server.memoryConfig; });
    return Promise.reject(unavailable('latest extraction'));
  });
  f.openSettings(); await tick(); const input = f.el('memory-auto-extract');
  input.checked = true; const old = input.onchange({ target: input }); await tick();
  input.checked = false; const latest = input.onchange({ target: input });
  first.resolve(); await Promise.all([old, latest]);
  assert.equal(input.checked, true); assert.equal(f.server.memoryConfig.auto_extract, true);
  assert.match(f.el('memory-config-status').textContent, /latest extraction/);
});

for (const firstFails of [false, true]) {
  test('non-voice persona writes retain the latest persisted intent after an older ' + (firstFails ? 'failure' : 'success'), async () => {
    const first = deferred(); const writes = [];
    const f = nonvoiceFixture((route, options, server) => {
      if (route !== '/api/persona' || options?.method !== 'PUT') return;
      writes.push(options.body.name);
      const commit = () => { server.persona = { ...server.persona, ...options.body, version: server.persona.version + 1 }; return { ...server.persona }; };
      return writes.length === 1 ? first.promise.then(commit) : commit();
    });
    f.openSettings(); await tick(); f.el('persona-name').value = 'first submitted';
    const old = f.el('persona-form').onsubmit({ preventDefault() {} }); await tick();
    f.el('persona-name').value = 'latest submitted'; const latest = f.el('persona-form').onsubmit({ preventDefault() {} }); await tick();
    assert.deepEqual(writes, ['first submitted']);
    if (firstFails) first.reject(unavailable('old persona')); else first.resolve();
    await Promise.all([old, latest]);
    assert.deepEqual(writes, ['first submitted', 'latest submitted']);
    assert.equal(f.server.persona.name, 'latest submitted'); assert.equal(f.el('persona-name').value, 'latest submitted');
    assert.doesNotMatch(f.el('persona-status').textContent, /old persona/);
  });
}

test('non-voice a queued same-row save cannot submit an obsolete snapshot after cancellation waits', async () => {
  const cancel = deferred(); let cancellations = 0; const writes = [];
  const f = nonvoiceFixture((route, options) => { if (route === '/api/memories/one' && options?.method === 'PUT') writes.push(options.body.content); });
  f.chat.cancelTurn = () => ++cancellations === 1 ? cancel.promise : Promise.resolve();
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'original' }]; f.openSettings(); await tick();
  const row = f.el('memory-list').children[0]; row.children[0].value = 'first submitted';
  const old = row.children[2].onclick(); await tick();
  row.children[0].value = 'latest submitted'; const latest = row.children[2].onclick(); await tick();
  assert.deepEqual(writes, []); cancel.resolve(); await Promise.all([old, latest]);
  assert.deepEqual(writes, ['latest submitted']); assert.equal(f.server.memories[0].content, 'latest submitted');
  assert.equal(f.el('memory-list').children[0].children[0].value, 'latest submitted');
});

test('non-voice successful forgetting survives another row save queued during pending cancellation', async () => {
  const cancel = deferred(); const writes = [];
  const f = nonvoiceFixture((route, options, server) => {
    if (options?.method) writes.push(route + ':' + options.method);
    if (route === '/api/memories/one' && options?.method === 'DELETE') { server.memories = server.memories.filter((item) => item.id !== 'one'); return {}; }
  });
  f.chat.invalidatePending = () => cancel.promise;
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'forget me' }, { id: 'two', kind: 'fact', content: 'keep me' }];
  f.openSettings(); await tick(); const rows = f.el('memory-list').children;
  const removing = rows[0].children[3].onclick(); await tick();
  rows[1].children[0].value = 'updated survivor'; const saving = rows[1].children[2].onclick(); await tick();
  assert.deepEqual(writes, []); cancel.resolve(); await Promise.all([removing, saving]);
  assert.deepEqual(writes, ['/api/memories/one:DELETE', '/api/memories/two:PUT']);
  assert.deepEqual(f.server.memories.map((item) => item.id), ['two']);
  assert.deepEqual(f.el('memory-list').children.map((row) => row.children[0].value), ['updated survivor']);
});

test('non-voice one row failure remains visible when another queued row saves successfully', async () => {
  const first = deferred();
  const f = nonvoiceFixture((route, options) => route === '/api/memories/one' && options?.method === 'PUT' ? first.promise : undefined);
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'original one' }, { id: 'two', kind: 'fact', content: 'original two' }];
  f.openSettings(); await tick(); const rows = f.el('memory-list').children;
  rows[0].children[0].value = 'unsaved one'; const failing = rows[0].children[2].onclick(); await tick();
  rows[1].children[0].value = 'saved two'; const saving = rows[1].children[2].onclick();
  first.reject(unavailable('independent row')); await Promise.all([failing, saving]);
  assert.match(f.el('memory-status').textContent, /independent row/);
  assert.deepEqual(f.server.memories.map((item) => item.content), ['original one', 'saved two']);
  assert.deepEqual(f.el('memory-list').children.map((row) => row.children[0].value), ['unsaved one', 'saved two']);
});

test('non-voice save followed by forget for the same row cannot resurrect the forgotten editor', async () => {
  const saving = deferred(); const writes = [];
  const f = nonvoiceFixture((route, options, server) => {
    if (route !== '/api/memories/one' || !options?.method) return;
    writes.push(options.method);
    if (options.method === 'PUT') return saving.promise.then(() => { server.memories[0].content = options.body.content; return {}; });
    if (options.method === 'DELETE') { server.memories = []; return {}; }
  });
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'original' }]; f.openSettings(); await tick();
  const row = f.el('memory-list').children[0]; row.children[0].value = 'submitted'; const old = row.children[2].onclick(); await tick();
  const removing = row.children[3].onclick(); await tick(); assert.deepEqual(writes, ['PUT']);
  saving.resolve(); await Promise.all([old, removing]);
  assert.deepEqual(writes, ['PUT', 'DELETE']); assert.equal(f.server.memories.length, 0); assert.equal(f.el('memory-list').children.length, 0);
});

test('non-voice a pre-deletion read cannot restore erased text while history refresh waits or fails', async () => {
  const deletion = deferred(), history = deferred(), stale = deferred(); let reads = 0;
  const f = nonvoiceFixture((route, options, server) => {
    if (route === '/api/memories' && !options?.method && ++reads === 2) return stale.promise;
    if (route === '/api/memories/one' && options?.method === 'DELETE') return deletion.promise.then(() => { server.memories = []; return {}; });
  });
  f.chat.refreshAfterForget = () => history.promise;
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'erased text' }]; f.openSettings(); await tick();
  const removing = f.el('memory-list').children[0].children[3].onclick(); await tick();
  const refreshing = f.el('refresh-memories').onclick(); await tick();
  deletion.resolve(); await tick(); stale.resolve({ memories: [{ id: 'one', kind: 'fact', content: 'erased text' }] }); await refreshing;
  assert.equal(f.el('memory-list').children.filter((row) => !row.hidden).length, 0);
  history.reject(unavailable('history')); await removing;
  assert.equal(f.server.memories.length, 0); assert.equal(f.el('memory-list').children.filter((row) => !row.hidden).length, 0);
  assert.match(f.el('memory-status').textContent, /history/);
});

test('non-voice separate new memories remain additive when queued behind another mutation', async () => {
  const first = deferred(); const added = [];
  const f = nonvoiceFixture((route, options, server) => {
    if (route === '/api/memories/one' && options?.method === 'PUT') return first.promise;
    if (route === '/api/memories' && options?.method === 'POST') {
      added.push(options.body.content); server.memories.push({ id: 'new-' + added.length, ...options.body }); return {};
    }
  });
  f.server.memories = [{ id: 'one', kind: 'fact', content: 'original' }]; f.openSettings(); await tick();
  const editing = f.el('memory-list').children[0].children[2].onclick(); await tick();
  f.el('memory-content').value = 'first new memory'; const a = f.el('memory-form').onsubmit({ preventDefault() {} });
  f.el('memory-content').value = 'second new memory'; const b = f.el('memory-form').onsubmit({ preventDefault() {} }); await tick();
  assert.deepEqual(added, []); first.resolve(); await Promise.all([editing, a, b]);
  assert.deepEqual(added, ['first new memory', 'second new memory']);
  assert.deepEqual(f.el('memory-list').children.map((row) => row.children[0].value), ['original', 'first new memory', 'second new memory']);
});

test('non-voice memory recovery preserves the focused draft and caret selection', async () => {
  const f = nonvoiceFixture(); f.server.memories = [{ id: 'one', kind: 'fact', content: 'saved' }];
  f.openSettings(); await tick();
  const input = f.el('memory-list').children[0].children[0];
  input.value = 'unsaved draft'; input.focus(); input.setSelectionRange(2, 5, 'backward');
  f.reconnect(); await tick();
  assert.equal(f.focused(), input); assert.equal(input.value, 'unsaved draft');
  assert.deepEqual([input.selectionStart, input.selectionEnd, input.selectionDirection], [2, 5, 'backward']);
});

test('stop during previous-turn cancellation never opens a late microphone', async () => {
  const wait = deferred(); const f = fixture({ cancel: () => wait.promise });
  f.start(); await tick(); f.stop(); wait.resolve(); await tick();
  assert.equal(f.streams.length, 0); assert.equal(f.recording(), false);
  assert.deepEqual(f.calls.microphone, [false]);
});

test('repeated start while cancel waits owns only the latest stream', async () => {
  const wait = deferred(); const f = fixture({ cancel: (call) => call === 1 ? wait.promise : undefined });
  f.start(); await tick(); f.bridge.action('voice-toggle'); await tick();
  wait.resolve(); await tick();
  assert.equal(f.streams.length, 1); assert.equal(f.recording(), true);
  f.stop(); await tick(); assert.ok(f.streams.every((stream) => stream.track.stopped));
  assert.equal(f.recording(), false); assert.equal(f.calls.asr, 0);
});

test('late permission and getUserMedia results are discarded without touching a newer capture', async () => {
  const wait = deferred(); let attempts = 0;
  const f = fixture({ media: async (_constraints, makeStream) => ++attempts === 1 ? wait.promise : makeStream('new') });
  f.start(); await tick(); f.start(); await tick();
  const old = { track: { stopped: false, stop() { this.stopped = true; } }, getTracks() { return [this.track]; } };
  wait.resolve(old); await tick();
  assert.equal(old.track.stopped, true); assert.equal(f.streams[0].track.stopped, false); assert.equal(f.recording(), true);
  f.stop(); await tick();
  const grant = deferred(); const g = fixture({ permission: (enabled) => enabled ? grant.promise : undefined });
  g.start(); await tick(); g.close(); grant.resolve(); await tick();
  assert.equal(g.streams.length, 0); assert.equal(g.recording(), false);
});

test('unavailable output device cannot block microphone recording or recognition', async () => {
  const f = fixture({ sink: async () => { throw new Error('speaker disconnected'); }, resume: async () => { throw new Error('output unavailable'); } });
  f.el('speaker-device').value = 'disconnected';
  f.start(); await tick(); assert.equal(f.recording(), true);
  f.start(); await tick();
  assert.equal(f.calls.asr, 1); assert.equal(f.calls.submitted.length, 1);
  assert.equal(f.calls.sink, 0); assert.equal(f.calls.resumed, 0);
});

test('automatic stop keeps the first minute when the final recorder chunk exceeds the timer', async () => {
  const f = fixture({ decode: async () => ({ sampleRate: 16000, length: 968000, numberOfChannels: 1, getChannelData: () => new Float32Array(968000).fill(0.1) }) });
  f.start(); await tick();
  [...f.timers.values()][0](); await tick();
  assert.equal(f.calls.asr, 1); assert.equal(f.calls.submitted.length, 1);
  const request = f.calls.api.find((item) => item.route === '/api/voice/transcribe');
  const wav = Buffer.from(request.options.body.audio_base64, 'base64');
  assert.equal(wav.readUInt32LE(40), 16000 * 60 * 2);
  assert.equal(f.streams[0].track.stopped, true);
});

test('late device refresh releases only its stream and cannot revoke a newer recording', async () => {
  const wait = deferred(); let attempts = 0;
  const f = fixture({ media: async (_constraints, makeStream) => ++attempts === 1 ? wait.promise : makeStream('new') });
  const refreshing = f.el('refresh-audio-devices').onclick(); await tick();
  f.start(); await tick();
  const grants = [...f.calls.microphone];
  const stale = { track: { stopped: false, stop() { this.stopped = true; } }, getTracks() { return [this.track]; } };
  wait.resolve(stale); await refreshing;
  assert.equal(stale.track.stopped, true); assert.equal(f.streams[0].track.stopped, false);
  assert.equal(f.recording(), true); assert.deepEqual(f.calls.microphone, grants);
  f.stop(); await tick();
});

test('stop and pagehide discard delayed enumeration without changing selected devices', async () => {
  for (const interruption of ['stop', 'close']) {
    const wait = deferred(); const f = fixture({ devices: () => wait.promise });
    f.el('microphone-device').value = 'chosen-mic';
    const refreshing = f.el('refresh-audio-devices').onclick(); await tick();
    f[interruption]();
    wait.resolve([{ kind: 'audioinput', deviceId: 'replacement' }]); await refreshing;
    assert.equal(f.el('microphone-device').value, 'chosen-mic');
    assert.equal(f.streams[0].track.stopped, true);
  }
});

test('device refresh reports progress and counts beside the settings button', async () => {
  const wait = deferred(); const f = fixture({ devices: () => wait.promise });
  const refreshing = f.el('refresh-audio-devices').onclick(); await tick();
  assert.equal(f.el('refresh-audio-devices').disabled, true);
  assert.match(f.el('audio-device-status').textContent, /正在读取/);
  wait.resolve([{kind:'audioinput',deviceId:'mic'},{kind:'audiooutput',deviceId:'speaker'}]);
  await refreshing;
  assert.equal(f.el('refresh-audio-devices').disabled, false);
  assert.match(f.el('audio-device-status').textContent, /1 个麦克风选项、1 个播放设备选项/);
  assert.equal(f.streams[0].track.stopped, true);
});

test('device refresh errors are visible in settings and a stale failure cannot overwrite a newer result', async () => {
  const f = fixture({ media: async () => { throw new Error('synthetic permission refused'); } });
  await f.el('refresh-audio-devices').onclick();
  assert.match(f.el('audio-device-status').textContent, /无法读取.*synthetic permission refused/);
  assert.equal(f.el('refresh-audio-devices').disabled, false);
  const wait = deferred(); let attempt = 0;
  const g = fixture({ devices: async () => ++attempt === 1 ? wait.promise : [] });
  const stale = g.el('refresh-audio-devices').onclick(); await tick();
  await g.el('refresh-audio-devices').onclick();
  const message = g.el('audio-device-status').textContent;
  wait.reject(new Error('late failure')); await stale;
  assert.equal(g.el('audio-device-status').textContent, message);
  assert.equal(g.el('refresh-audio-devices').disabled, false);
});

test('permission rejection reports only observed denial without claiming an OS setting is disabled', async () => {
  const f = fixture({ media: async () => { throw Object.assign(new Error('Permission denied'), {name:'NotAllowedError'}); } });
  await f.el('refresh-audio-devices').onclick();
  assert.match(f.el('audio-device-status').textContent, /本次麦克风访问未获许可/);
  assert.doesNotMatch(f.el('audio-device-status').textContent, /系统|隐私/);
  f.start(); await tick();
  assert.match(f.el('media-status').textContent, /本次麦克风访问未获许可/);
  assert.doesNotMatch(f.el('media-status').textContent, /系统|隐私/);
});

test('old recorder data error ended and timer callbacks cannot stop a newer recording', async () => {
  const f = fixture(); f.start(); await tick();
  const old = f.recorders[0], oldTimer = [...f.timers.values()][0];
  f.stop(); f.start(); await tick();
  old.ondataavailable({ data: { size: 9 * 1024 * 1024 } }); old.onerror(); old.stream.track.events.ended(); oldTimer(); await old.onstop(); await tick();
  assert.equal(f.recording(), true); assert.equal(f.streams[1].track.stopped, false); assert.equal(f.calls.asr, 0);
  f.stop(); await tick();
});

test('pending ASR submission retains capture ownership and is invalidated by new speech', async () => {
  const wait = deferred(); const f = fixture({ submit: () => wait.promise });
  f.start(); await tick(); f.start(); await tick();
  assert.equal(f.calls.submitted.length, 1);
  const pending = f.calls.submitted[0].options;
  assert.equal(pending.signal.aborted, false); assert.equal(pending.isCurrent(), true);
  f.start(); await tick();
  assert.equal(pending.signal.aborted, true); assert.equal(pending.isCurrent(), false);
  wait.reject(new Error('old submission rejected')); await tick();
  assert.equal(f.recording(), true); assert.equal(f.streams[1].track.stopped, false);
  assert.ok(!f.el('media-status').textContent.includes('old submission rejected')); f.stop(); await tick();
});

test('late ASR result after stop never submits text', async () => {
  const wait = deferred(); const f = fixture({ transcribe: () => wait.promise });
  f.start(); await tick(); f.start(); await tick(); f.stop();
  wait.resolve({ text: '已取消的合成问题' }); await tick();
  assert.equal(f.calls.submitted.length, 0); assert.equal(f.recording(), false);
});

test('quiet submission handoff aborts and releases capture while retaining recognized text', async () => {
  const wait = deferred(); const f = fixture({ submit: () => wait.promise });
  f.start(); await tick(); f.start(); await tick();
  const pending = f.calls.submitted[0].options;
  assert.equal(f.el('media-status').textContent, '听到：合成问题');
  f.companion.stopRecording(true, { quiet: true });
  assert.equal(pending.signal.aborted, true); assert.equal(pending.isCurrent(), false);
  assert.equal(f.streams[0].track.stopped, true); assert.equal(f.recording(), false);
  assert.equal(f.el('media-status').textContent, '听到：合成问题');
  wait.resolve(); await tick();
  f.start(); await tick(); f.stop(); await tick();
  assert.equal(f.el('media-status').textContent, '已停止声音。');
});

test('switching microphones cancels this recording and the next recording uses the selected device', async () => {
  const f = fixture(); f.el('microphone-device').value = 'A'; f.start(); await tick();
  f.el('microphone-device').value = 'B'; f.el('microphone-device').onchange(); await tick();
  assert.equal(f.recording(), false); assert.equal(f.streams[0].track.stopped, true); assert.equal(f.calls.asr, 0);
  assert.match(f.el('media-status').textContent, /取消本轮.*重新说话/);
  f.start(); await tick(); assert.equal(f.streams[1].device, 'B'); f.stop(); await tick();
});

test('a normal utterance reaches submit once with a live cancellation token', async () => {
  const f = fixture(); f.start(); await tick(); f.start(); await tick();
  assert.equal(f.calls.asr, 1); assert.equal(f.calls.submitted.length, 1);
  assert.equal(f.calls.submitted[0].text, '合成问题'); assert.equal(f.calls.submitted[0].options.signal.aborted, false);
  assert.equal(f.streams[0].track.stopped, true); assert.equal(f.recording(), false);
  const request = f.calls.api.find((item) => item.route === '/api/voice/transcribe');
  assert.equal(request.options.body.mime_type, 'audio/wav');
  const wav = Buffer.from(request.options.body.audio_base64, 'base64');
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF'); assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
  assert.equal(wav.readUInt32LE(24), 16000); assert.equal(wav.readUInt16LE(22), 1); assert.equal(wav.readUInt16LE(34), 16);
  assert.equal(f.calls.decoded, 1);
});

test('vision disables immediately, revokes image turns, and waits before enabling a source', async () => {
  const wait = deferred(); const f = fixture({ revoke: () => wait.promise });
  const selecting = f.enable('window:A'); await tick();
  assert.equal(f.el('vision-enabled').checked, true); assert.match(f.el('vision-status').textContent, /正在选择画面/);
  assert.deepEqual(f.calls.vision, ['revoke', 'stop']); assert.equal(await f.companion.captureForTurn(), null);
  wait.resolve(); await selecting;
  assert.deepEqual(f.calls.vision, ['revoke', 'stop', 'select:window:A']);
  const capture = await f.companion.captureForTurn(); assert.equal(f.companion.frameStillAllowed(capture), true);
  const stopping = f.disable(); assert.equal(f.companion.frameStillAllowed(capture), false); await stopping;
  assert.equal(await f.companion.captureForTurn(), null);
});

test('checked selection intent can be revoked during both cancellation and preview waits', async () => {
  const revoke = deferred(); const f = fixture({ revoke: () => revoke.promise });
  const selecting = f.enable('window:A');
  assert.equal(f.el('vision-enabled').checked, true);
  const stopping = f.disable();
  assert.equal(f.el('vision-enabled').checked, false);
  revoke.resolve(); await Promise.all([selecting, stopping]);
  assert.equal(f.calls.vision.some((call) => call.startsWith('select:')), false);
  assert.equal(await f.companion.captureForTurn(), null);

  const preview = deferred(); const g = fixture({ select: () => preview.promise });
  const previewing = g.enable('window:B'); await tick();
  assert.equal(g.el('vision-enabled').checked, true);
  assert.equal(await g.companion.captureForTurn(), null);
  await g.disable(); preview.resolve(g.frame('window:B')); await previewing;
  assert.equal(g.el('vision-enabled').checked, false);
  assert.equal(g.el('vision-preview').hidden, true);
  assert.equal(await g.companion.captureForTurn(), null);

  const h = fixture({ select: async () => { throw new Error('synthetic unavailable'); } });
  await h.enable('window:C');
  assert.equal(h.el('vision-enabled').checked, false);
  assert.match(h.el('vision-status').textContent, /synthetic unavailable/);
});

test('late failed source selection and capture cannot disable a newer chosen source', async () => {
  const select = deferred(), image = deferred();
  const f = fixture({ select: (id) => id === 'window:A' ? select.promise : f.frame(id), capture: () => image.promise });
  const older = f.enable('window:A'); await tick(); await f.enable('window:B');
  select.reject(new Error('old source unavailable')); await older;
  assert.equal(f.el('vision-enabled').checked, true); assert.match(f.el('vision-status').textContent, /window:B/);
  const pending = f.companion.captureForTurn();
  await f.enable('window:C'); image.reject(new Error('old frame revoked'));
  await assert.rejects(pending, /old frame revoked/);
  assert.equal(f.el('vision-enabled').checked, true); assert.match(f.el('vision-status').textContent, /window:C/);
});

test('memory snapshot waits for pending request revocation and never mutates after cancellation failure', async () => {
  const pending = deferred(); const f = fixture({ invalidate: () => pending.promise });
  const saving = f.el('create-memory-backup').onclick(); await tick();
  assert.equal(f.calls.api.some((item) => item.route === '/api/memory/backups' && item.options?.method === 'POST'), false);
  pending.reject(new Error('synthetic durable revocation failed'));
  await saving;
  assert.equal(f.calls.api.some((item) => item.options?.method === 'POST'), false);
  assert.match(f.el('memory-backup-status').textContent, /synthetic durable revocation failed/);
});


test('voice preserves the match and selected source captured before microphone input', async () => {
  const binding = { sessionId: 'a'.repeat(32), contextEpoch: 7,
    match: { match_id: 'match-' + 'b'.repeat(32), expected_revision: 11 },
    guideTarget: { guide_id: 'guide-' + 'c'.repeat(32), revision_id: 'revision-' + 'd'.repeat(32) } };
  let reads = 0;
  const f = fixture({ binding: async () => { reads++; return binding; } });
  f.start(); await tick(); f.start(); await tick();
  assert.equal(reads, 1);
  const request = f.calls.api.find((item) => item.route === '/api/voice/transcribe');
  assert.deepEqual(request.options.body.match, binding.match);
  assert.equal(request.options.body.session_id, binding.sessionId);
  assert.equal(f.calls.submitted[0].options.inputOrigin, 'voice');
  assert.equal(f.calls.submitted[0].options.binding, binding);
});

test('a stale match invalidates a delayed ASR result before a user turn is submitted', async () => {
  const wait = deferred(); let valid = true;
  const f = fixture({ transcribe: () => wait.promise, bindingCurrent: () => valid });
  f.start(); await tick(); f.start(); await tick();
  assert.equal(f.calls.asr, 1);
  valid = false;
  wait.resolve({ text: '旧局迟到语音' }); await tick();
  assert.equal(f.calls.submitted.length, 0);
  f.stop(); await tick();
  assert.ok(f.streams.every((stream) => stream.track.stopped));
});

for (const interruption of ['stop', 'new recording', 'match change']) {
  test(`delayed WAV conversion sends no obsolete audio after ${interruption}`, async () => {
    const wait = deferred(); let valid = true;
    const f = fixture({ decode: async (_value, decoded) => { await wait.promise; return decoded(); }, bindingCurrent: () => valid });
    f.start(); await tick(); f.start(); await tick();
    assert.equal(f.calls.decoded, 1); assert.equal(f.calls.asr, 0);
    if (interruption === 'match change') valid = false;
    else { f.stop(); if (interruption === 'new recording') { f.start(); await tick(); } }
    wait.resolve(); await tick();
    assert.equal(f.calls.asr, 0); assert.equal(f.calls.submitted.length, 0);
    if (interruption === 'new recording') { assert.equal(f.recording(), true); assert.equal(f.streams[1].track.stopped, false); }
    f.stop(); await tick();
  });
}

test('recording refreshes provider before microphone and refuses a missing local engine', async () => {
  const f = fixture({ api: async (route) => route === '/api/voice/config' ? { voice_provider: 'local', local: { state: 'missing' } } : {} });
  f.start(); await tick();
  assert.equal(f.streams.length, 0); assert.equal(f.calls.asr, 0);
  assert.match(f.el('media-status').textContent, /下载并启用免费语音/);
  const wait = deferred();
  const g = fixture({ api: async (route) => route === '/api/voice/config' ? wait.promise : {} });
  g.start(); await tick(); g.stop(); wait.resolve({ voice_provider: 'local', local: { state: 'ready' } }); await tick();
  assert.equal(g.streams.length, 0); assert.equal(g.calls.asr, 0);
});

test('local utterances also carry actual PCM WAV and keep their original binding', async () => {
  const f = fixture({ api: async (route) => route === '/api/voice/config' ? { voice_provider: 'local', local: { state: 'ready' } } : {} });
  f.start(); await tick(); f.start(); await tick();
  const request = f.calls.api.find((item) => item.route === '/api/voice/transcribe');
  assert.equal(request.options.body.mime_type, 'audio/wav');
  assert.equal(Buffer.from(request.options.body.audio_base64, 'base64').readUInt32LE(24), 16000);
  assert.equal(f.calls.submitted.length, 1);
});

test('decoder failure or excessive decoded duration never uploads partial recording', async () => {
  for (const decode of [async () => { throw new Error('unsupported recording'); }, async () => ({ sampleRate: 16000, length: 960001, numberOfChannels: 1 })]) {
    const f = fixture({ decode }); f.start(); await tick(); f.start(); await tick();
    assert.equal(f.calls.asr, 0); assert.equal(f.calls.submitted.length, 0);
    assert.match(f.el('media-status').textContent, /语音识别未完成/);
  }
});

test('local settings preserve cloud credentials and require explicit download', async () => {
  const cloud = { voice_provider: 'openai', asr_base_url: 'https://speech.example/v1', tts_base_url: 'https://speech.example/v1', asr_key_set: true, tts_key_set: true, local: { state: 'missing' } };
  let saved = { ...cloud };
  const f = fixture({ api: async (route, options) => {
    if (route === '/api/voice/config') { if (options?.method === 'PUT') saved = { ...saved, ...options.body }; return saved; }
    return {};
  } });
  f.openSettings(); await tick();
  assert.equal(f.el('voice-provider').value, 'openai');
  f.el('asr-api-key').value = 'unsaved-secret'; f.el('clear-asr-key').checked = true;
  f.el('voice-provider').value = 'local'; f.el('voice-provider').onchange();
  assert.equal(f.el('cloud-voice-settings').hidden, true); assert.equal(f.el('local-voice-settings').hidden, false);
  assert.equal(f.el('asr-api-key').value, 'unsaved-secret'); assert.equal(f.el('clear-asr-key').checked, true);
  await f.el('voice-form').onsubmit({ preventDefault() {} });
  const put = f.calls.api.find((item) => item.route === '/api/voice/config' && item.options?.method === 'PUT');
  assert.equal(JSON.stringify(put.options.body), JSON.stringify({ voice_provider: 'local', local_tts_engine: 'kokoro', local_tts_speaker_id: 46 }));
  assert.equal(f.el('asr-api-key').value, 'unsaved-secret');
  assert.equal(saved.asr_base_url, cloud.asr_base_url); assert.equal(saved.asr_key_set, true);
  assert.equal(f.el('test-voice').disabled, true);
  assert.equal(f.calls.api.some((item) => item.route === '/api/voice/local/install'), false);
});

test('local speaker setting loads, validates and saves independently of the cloud voice name', async () => {
  let saved = { voice_provider: 'local', local_tts_speaker_id: 57, tts_voice: 'cloud-only', local: { state: 'ready' } };
  const f = fixture({ api: async (route, options) => {
    if (route === '/api/voice/config') { if (options?.method === 'PUT') saved = { ...saved, ...options.body }; return saved; }
    return {};
  } });
  f.openSettings(); await tick(); assert.equal(f.el('local-tts-speaker-id').value, '57');
  f.el('local-tts-speaker-id').value = '3';
  await f.el('voice-form').onsubmit({ preventDefault() {} });
  assert.equal(saved.local_tts_speaker_id, 3); assert.equal(saved.tts_voice, 'cloud-only');
  const count = f.calls.api.filter((item) => item.options?.method === 'PUT').length;
  for (const invalid of ['', '-1', '103', '46.5', '1e1']) {
    f.el('local-tts-speaker-id').value = invalid;
    await f.el('voice-form').onsubmit({ preventDefault() {} });
  }
  assert.equal(f.calls.api.filter((item) => item.options?.method === 'PUT').length, count);
  assert.match(f.el('voice-config-status').textContent, /0.*102/);
});

test('speaker changes stop an owned reply and expose only a bounded copy of timing metadata', () => {
  const f = fixture(); f.el('speak-replies').checked = true;
  f.companion.beginTurn('synthetic-turn', 'synthetic-session', null);
  f.el('local-tts-speaker-id').onchange(); assert.equal(f.companion.speakingTurn('synthetic-turn'), false);
  for (let i = 0; i < 300; i++) f.companion.stopSpeech();
  const rows = f.companion.voiceTimings(); assert.equal(rows.length, 256);
  assert.ok(rows.every((row) => row.stage === 'generation_stopped' && Number.isFinite(row.at_ms)));
  rows[0].stage = 'modified'; rows.length = 0;
  assert.equal(f.companion.voiceTimings().length, 256);
  assert.equal(f.companion.voiceTimings()[0].stage, 'generation_stopped');
});

test('explicit local install polls, supports cancellation and refreshes after reopening settings', async () => {
  let local = { state: 'missing' }, provider = 'openai';
  const f = fixture({ api: async (route, options) => {
    if (route === '/api/voice/config') { if (options?.method === 'PUT') provider = options.body.voice_provider; return { voice_provider: provider, local }; }
    if (route === '/api/voice/local/install') { local = { state: 'installing', downloaded_bytes: 5, total_bytes: 10 }; return local; }
    if (route === '/api/voice/local/cancel') { local = { state: 'missing', message: '已取消下载。' }; return local; }
    if (route === '/api/voice/local') return local;
    return {};
  } });
  f.openSettings(); await tick(); f.el('voice-provider').value = 'local'; f.el('voice-provider').onchange();
  await f.el('install-local-voice').onclick();
  const mutations = f.calls.api.filter((item) => item.options?.method);
  assert.equal(mutations[0].route, '/api/voice/config'); assert.equal(JSON.stringify(mutations[0].options.body), '{"voice_provider":"local","local_tts_engine":"kokoro"}');
  assert.equal(mutations[1].route, '/api/voice/local/install'); assert.equal(JSON.stringify(mutations[1].options.body), '{"confirm":true}');
  assert.equal(f.el('cancel-local-voice').hidden, false); assert.match(f.el('local-voice-status').textContent, /50%/);
  assert.equal(f.timers.size, 1);
  f.closeSettings(); assert.equal(f.timers.size, 0);
  f.openSettings(); await tick(); assert.equal(f.timers.size, 1);
  await f.el('cancel-local-voice').onclick(); assert.equal(f.timers.size, 0); assert.equal(f.el('cancel-local-voice').hidden, true);
  await f.el('install-local-voice').onclick();
  local = { state: 'ready' };
  await [...f.timers.values()][0](); await tick();
  assert.equal(f.el('test-voice').disabled, false); assert.equal(f.el('install-local-voice').hidden, true);
  f.close(); assert.equal(f.timers.size, 0);
});

test('closed settings ignore an in-flight local status result and pagehide clears polling', async () => {
  const wait = deferred();
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local: { state: 'installing' } };
    if (route === '/api/voice/local') return wait.promise;
    return {};
  } });
  f.openSettings(); await tick();
  const polling = [...f.timers.values()][0]();
  f.closeSettings(); wait.resolve({ state: 'ready' }); await polling;
  assert.equal(f.el('test-voice').disabled, true);
  f.openSettings(); await tick(); assert.equal(f.timers.size, 1);
  f.close(); assert.equal(f.timers.size, 0);
});

test('failed local download shows a retry and keeps cloud fields disabled until selected', async () => {
  let local = { state: 'missing' }, installs = 0;
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local };
    if (route === '/api/voice/local/install') { installs++; return { state: 'installing' }; }
    if (route === '/api/voice/local') return local;
    return {};
  } });
  f.openSettings(); await tick();
  assert.equal(f.el('asr-base-url').disabled, true); assert.equal(f.el('clear-tts-key').disabled, true);
  await f.el('install-local-voice').onclick();
  local = { state: 'error', message: '资源校验失败，请重试。' };
  await [...f.timers.values()][0](); await tick();
  assert.match(f.el('local-voice-status').textContent, /校验失败/);
  assert.match(f.el('install-local-voice').textContent, /重试/); assert.equal(f.el('install-local-voice').disabled, false);
  await f.el('install-local-voice').onclick(); assert.equal(installs, 2);
  f.el('voice-provider').value = 'openai'; f.el('voice-provider').onchange();
  assert.equal(f.el('asr-base-url').disabled, false); assert.equal(f.el('clear-tts-key').disabled, false);
  f.close(); assert.equal(f.timers.size, 0);
});

for (const provider of ['openai', 'local']) {
  test('saved voice execution state follows ' + provider + ' after settings close discards form hydration', async () => {
    const read = deferred(); let reads = 0;
    const qwen = { state: 'ready', supported: true, loaded: false };
    let saved = { voice_provider: provider === 'local' ? 'openai' : 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
    const f = fixture({ api: (route, options) => {
      if (route === '/api/voice/config') {
        if (options?.method === 'PUT') { saved = { ...saved, ...options.body }; return { ...saved }; }
        return ++reads === 2 ? read.promise : { ...saved };
      }
      if (route === '/api/voice/qwen/load') return { ...qwen, loaded: true };
      return {};
    } });
    f.openSettings(); await tick(); f.el('voice-provider').value = provider; f.el('voice-provider').onchange();
    const saving = f.el('voice-form').onsubmit({ preventDefault() {} }); await tick(); f.closeSettings();
    read.resolve({ ...saved }); await saving;
    f.el('speak-replies').checked = true; f.companion.prepareSpeech({ newTurn: true }); await tick();
    assert.equal(saved.voice_provider, provider);
    assert.equal(f.calls.api.filter((call) => call.route === '/api/voice/qwen/load').length, provider === 'local' ? 1 : 0);
  });
}

test('accepted voice save survives a failed follow-up GET and obsolete config reads', async () => {
  const old = deferred(); let reads = 0;
  const qwen = { state: 'ready', supported: true, loaded: false };
  const initial = { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
  const f = fixture({ api: (route, options) => {
    if (route !== '/api/voice/config') return {};
    if (options?.method === 'PUT') return { ...initial, ...options.body };
    if (++reads === 1) return initial;
    if (reads === 2) return old.promise;
    return Promise.reject(unavailable('config refresh'));
  } });
  f.openSettings(); await tick(); f.reconnect(); await tick();
  f.el('voice-provider').value = 'openai'; f.el('voice-provider').onchange();
  await f.el('voice-form').onsubmit({ preventDefault() {} }); old.resolve(initial); await tick();
  f.el('speak-replies').checked = true; f.companion.prepareSpeech({ newTurn: true }); await tick();
  assert.equal(f.calls.api.some((call) => call.route === '/api/voice/qwen/load'), false);
});

test('local installation persists its provider despite a config read begun while the enabling PUT waits', async () => {
  const put = deferred(), stale = deferred(); let reads = 0;
  const qwen = { state: 'ready', supported: true, loaded: false };
  const initial = { voice_provider: 'openai', local_tts_engine: 'qwen', local: { state: 'missing' }, qwen };
  const f = fixture({ api: (route, options) => {
    if (route === '/api/voice/config') {
      if (options?.method === 'PUT') return put.promise.then(() => ({ ...initial, ...options.body }));
      return ++reads === 1 ? initial : stale.promise;
    }
    if (route === '/api/voice/local/install') return { state: 'ready' };
    if (route === '/api/voice/qwen/load') return { ...qwen, loaded: true };
    return {};
  } });
  f.openSettings(); await tick(); f.el('voice-provider').value = 'local'; f.el('voice-provider').onchange();
  const installing = f.el('install-local-voice').onclick(); await tick(); f.reconnect(); await tick();
  put.resolve(); await installing; stale.resolve(initial); await tick();
  f.el('speak-replies').checked = true; f.companion.prepareSpeech({ newTurn: true }); await tick();
  assert.equal(f.calls.api.filter((call) => call.route === '/api/voice/qwen/load').length, 1);
  assert.equal(f.el('voice-provider').value, 'local');
});

test('voice save refresh preserves a newer editable field and clear-key choice', async () => {
  const put = deferred(); const initial = { voice_provider: 'openai', asr_model: 'saved-asr', tts_voice: 'saved-voice', local: { state: 'ready' } };
  let saved = { ...initial };
  const f = fixture({ api: (route, options) => {
    if (route !== '/api/voice/config') return {};
    if (options?.method === 'PUT') return put.promise.then(() => { saved = { ...saved, ...options.body }; return saved; });
    return { ...saved };
  } });
  f.openSettings(); await tick(); f.el('asr-model').value = 'submitted';
  const saving = f.el('voice-form').onsubmit({ preventDefault() {} }); await tick();
  f.el('asr-model').value = 'newer draft'; f.el('clear-tts-key').checked = true;
  put.resolve(); await saving;
  assert.equal(f.el('asr-model').value, 'newer draft'); assert.equal(f.el('clear-tts-key').checked, true);
  assert.equal(saved.asr_model, 'submitted');
});

for (const failed of [false, true]) {
  test('local download cancellation restores voice controls after ' + (failed ? 'failure' : 'success'), async () => {
    const f = fixture({ api: (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'kokoro', local: { state: 'installing' } };
      if (route === '/api/voice/local/cancel') return failed ? Promise.reject(unavailable('cancel')) : { state: 'missing' };
      return {};
    } });
    f.openSettings(); await tick(); await f.el('cancel-local-voice').onclick();
    assert.equal(f.el('voice-provider').disabled, false); assert.equal(f.el('local-tts-engine').disabled, false);
    assert.equal(f.el('refresh-qwen-voice').disabled, false);
  });
}

test('Qwen selection exposes missing resources and preserves cloud drafts and the saved Kokoro speaker', async () => {
  const qwen = { state: 'missing', supported: true, loaded: false };
  let saved = { voice_provider: 'local', local_tts_engine: 'kokoro', local_tts_speaker_id: 57,
    asr_key_set: true, tts_key_set: true, tts_voice: 'synthetic-cloud', local: { state: 'ready' }, qwen };
  const f = fixture({ api: async (route, options) => {
    if (route === '/api/voice/config') { if (options?.method === 'PUT') saved = { ...saved, ...options.body }; return saved; }
    if (route === '/api/voice/qwen/status') return qwen;
    return {};
  } });
  f.openSettings(); await tick();
  f.el('asr-api-key').value = 'synthetic-asr-draft'; f.el('tts-api-key').value = 'synthetic-tts-draft';
  f.el('local-tts-engine').value = 'qwen'; f.el('local-tts-engine').onchange(); await tick();
  assert.equal(f.el('qwen-voice-settings').hidden, false); assert.equal(f.el('kokoro-voice-settings').hidden, true);
  assert.match(f.el('qwen-voice-status').textContent, /资源缺失/);
  assert.equal(f.el('test-voice').disabled, true); assert.equal(f.el('load-qwen-voice').disabled, true);
  await f.el('voice-form').onsubmit({ preventDefault() {} });
  const put = f.calls.api.find((item) => item.options?.method === 'PUT');
  assert.equal(JSON.stringify(put.options.body), JSON.stringify({ voice_provider: 'local', local_tts_engine: 'qwen' }));
  assert.equal(saved.local_tts_speaker_id, 57); assert.equal(saved.tts_voice, 'synthetic-cloud'); assert.equal(saved.asr_key_set, true);
  assert.equal(f.el('asr-api-key').value, 'synthetic-asr-draft'); assert.equal(f.el('tts-api-key').value, 'synthetic-tts-draft');
  assert.equal(f.calls.api.some((item) => /\/(?:install|load)$/.test(item.route)), false);
  assert.match(f.el('voice-config-status').textContent, /资源尚未就绪/);
});

test('Qwen preheat is explicit, waits visibly, rejects duplicate clicks and releases only through its route', async () => {
  const wait = deferred(); let qwen = { state: 'ready', supported: true, loaded: false, gpu_required: true };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'missing' }, qwen };
    if (route === '/api/voice/qwen/load') return wait.promise;
    if (route === '/api/voice/qwen/unload') return { ...qwen, loaded: false };
    return {};
  } });
  f.openSettings(); await tick();
  assert.equal(f.el('test-voice').disabled, false, 'Qwen TTS does not depend on the Kokoro resource state');
  assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
  const loading = f.el('load-qwen-voice').onclick(); await tick();
  assert.match(f.el('qwen-voice-status').textContent, /正在加载.*数十秒/);
  assert.equal(f.el('voice-provider').disabled, false); assert.equal(f.el('test-voice').disabled, true);
  await f.el('load-qwen-voice').onclick();
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  qwen = { ...qwen, loaded: true }; wait.resolve(qwen); await loading;
  assert.match(f.el('qwen-voice-status').textContent, /已预热/); assert.equal(f.el('unload-qwen-voice').disabled, false);
  await f.el('unload-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, false); assert.equal(f.el('unload-qwen-voice').disabled, true);
  for (const call of f.calls.api.filter((item) => /\/(load|unload)$/.test(item.route))) {
    assert.equal(call.options.method, 'POST'); assert.match(call.options.body.request_id, /^[a-f0-9]{32}$/);
  }
});

test('Qwen load errors are visible, refresh can recover, and unsaved choices cannot preload', async () => {
  let selected = 'kokoro', fail = true;
  const qwen = { state: 'ready', supported: true, loaded: false };
  const f = fixture({ api: async (route, options) => {
    if (route === '/api/voice/config') {
      if (options?.method === 'PUT') selected = options.body.local_tts_engine;
      return { voice_provider: 'local', local_tts_engine: selected, local: { state: 'ready' }, qwen };
    }
    if (route === '/api/voice/qwen/status') return qwen;
    if (route === '/api/voice/qwen/load') { if (fail) throw new Error('synthetic GPU unavailable'); return { ...qwen, loaded: true }; }
    return {};
  } });
  f.openSettings(); await tick(); f.el('local-tts-engine').value = 'qwen'; f.el('local-tts-engine').onchange(); await tick();
  assert.equal(f.el('load-qwen-voice').disabled, true);
  await f.el('voice-form').onsubmit({ preventDefault() {} }); await f.el('load-qwen-voice').onclick();
  assert.match(f.el('qwen-voice-status').textContent, /预热失败.*Qwen 请求未完成/);
  assert.equal(f.el('local-tts-engine').disabled, false); assert.equal(f.el('test-voice').disabled, true);
  fail = false; await f.el('refresh-qwen-voice').onclick(); await f.el('load-qwen-voice').onclick();
  assert.match(f.el('qwen-voice-status').textContent, /已预热/);
});

test('engine switches abort pending Qwen audio and ignore its late result', async () => {
  const wait = deferred();
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: { state: 'ready', loaded: true, supported: true } };
    if (route === '/api/voice/synthesize') return wait.promise;
    return {};
  } });
  f.openSettings(); await tick(); f.el('speak-replies').checked = true;
  f.companion.beginTurn('synthetic-turn', 'synthetic-session'); f.companion.text('synthetic-turn', '我会先陪你把眼前这件事理清楚，');
  const request = f.calls.api.find((item) => item.route === '/api/voice/synthesize'); assert.ok(request);
  f.el('local-tts-engine').value = 'kokoro'; f.el('local-tts-engine').onchange();
  assert.equal(request.options.signal.aborted, true);
  wait.resolve({ audio_base64: 'c3ludGhldGlj' }); await tick();
  assert.equal(f.calls.decoded, 0); assert.equal(f.companion.speakingTurn('synthetic-turn'), false);
  assert.ok(f.calls.api.some((item) => item.route === '/api/voice/cancel'));
});

test('Qwen still transcribes through the shared local route and retains numeric input end timing', async () => {
  const f = fixture({ api: async (route) => route === '/api/voice/config'
    ? { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: { state: 'missing' } } : {} });
  f.start(); await tick(); f.start(); await tick();
  assert.equal(f.calls.asr, 1); assert.equal(f.calls.submitted.length, 1);
  assert.ok(Number.isFinite(f.calls.submitted[0].options.inputEndedAt));
  assert.equal(f.calls.api.some((item) => item.route.includes('/qwen/')), false);
  f.el('speak-replies').checked = true;
  f.companion.beginTurn('synthetic-timing', 'synthetic-session', null, false, { submittedAt: 20, inputEndedAt: 10 });
  const rows = f.companion.voiceTimings().filter((row) => ['input_ended', 'turn_submitted'].includes(row.stage));
  assert.deepEqual(Array.from(rows, (row) => row.at_ms), [10, 20]); assert.equal(rows[0].generation, rows[1].generation);
});

test('Qwen preheats once on an explicit reading choice and leaves an owned speech generation intact', async () => {
  const wait = deferred();
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: { state: 'ready', supported: true, loaded: false } };
    if (route === '/api/voice/qwen/load') return wait.promise;
    return {};
  } });
  f.openSettings(); await tick();
  assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
  f.el('speak-replies').checked = true; f.companion.beginTurn('owned-turn', 'synthetic-session');
  f.el('speak-replies').onchange(); f.companion.prepareSpeech(); f.companion.prepareSpeech(); await tick();
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  assert.equal(f.companion.speakingTurn('owned-turn'), true);
  wait.resolve({ state: 'ready', supported: true, loaded: true }); await tick();
  f.companion.prepareSpeech(); assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
});

test('automatic Qwen preload failure is visible and does not retry or block SenseVoice input', async () => {
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: { state: 'ready', supported: true, loaded: false } };
    if (route === '/api/voice/qwen/load') throw new Error('synthetic preload failed');
    return {};
  } });
  f.el('speak-replies').checked = true; f.openSettings(); await tick();
  assert.match(f.el('media-status').textContent, /预热失败.*Qwen 请求未完成/);
  f.companion.prepareSpeech(); f.companion.prepareSpeech(); await tick();
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  f.start(); await tick(); assert.equal(f.recording(), true); f.stop(); await tick();
});

test('automatic preload excludes cloud, Kokoro, unsupported or missing Qwen resources', async () => {
  for (const config of [
    { voice_provider: 'openai', local_tts_engine: 'qwen', qwen: { state: 'ready', supported: true } },
    { voice_provider: 'local', local_tts_engine: 'kokoro', qwen: { state: 'ready', supported: true } },
    { voice_provider: 'local', local_tts_engine: 'qwen', qwen: { state: 'missing', supported: true } },
    { voice_provider: 'local', local_tts_engine: 'qwen', qwen: { state: 'ready', supported: false } },
  ]) {
    const f = fixture({ api: async (route) => route === '/api/voice/config' ? config : {} });
    f.el('speak-replies').checked = true; f.openSettings(); await tick(); f.companion.prepareSpeech();
    assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
  }
});

test('a new turn preloads after backend idle unload even when the renderer still caches loaded true', async () => {
  let resident = true; const load = deferred();
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: { state: 'ready', supported: true, loaded: resident } };
    if (route === '/api/voice/qwen/load') { resident = true; return load.promise; }
    return {};
  } });
  f.el('speak-replies').checked = true; f.openSettings(); await tick();
  assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
  resident = false; // Backend's idle eviction has no renderer notification.
  f.companion.prepareSpeech({ newTurn: true }); f.companion.prepareSpeech({ newTurn: true }); await tick();
  assert.equal(resident, true);
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  assert.equal(f.calls.api.some((item) => item.route === '/api/voice/synthesize'), false, 'warmup precedes first synthesized segment');
  load.resolve({ state: 'ready', supported: true, loaded: true }); await tick();
  f.companion.prepareSpeech(); assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
});

test('explicit Qwen release survives settings reopen until a new speech demand', async () => {
  let resident = true;
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: { state: 'ready', supported: true, loaded: resident } };
    if (route === '/api/voice/qwen/unload') resident = false;
    if (route === '/api/voice/qwen/load') resident = true;
    return { state: 'ready', supported: true, loaded: resident };
  } });
  f.el('speak-replies').checked = true; f.openSettings(); await tick();
  await f.el('unload-qwen-voice').onclick(); assert.equal(resident, false);
  f.closeSettings(); f.openSettings(); await tick(); f.companion.prepareSpeech();
  assert.equal(resident, false); assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
  f.companion.prepareSpeech({ newTurn: true }); await tick();
  assert.equal(resident, true); assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
});


function pendingQwenFixture() {
  const load = deferred(), unload = deferred();
  const qwen = { state: 'ready', supported: true, loaded: false };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
    if (route === '/api/voice/qwen/load') return load.promise;
    if (route === '/api/voice/qwen/unload') return unload.promise;
    return {};
  } });
  return { f, load, unload, qwen };
}

test('release supersedes cold preload but remains busy until backend cleanup confirms completion', async () => {
  const { f, load, unload, qwen } = pendingQwenFixture();
  f.openSettings(); await tick();
  const loading = f.el('load-qwen-voice').onclick();
  assert.equal(f.el('unload-qwen-voice').disabled, false);
  assert.match(f.el('unload-qwen-voice').textContent, /取消预热/);
  assert.equal(f.el('voice-provider').disabled, false); assert.equal(f.el('local-tts-engine').disabled, false);
  const releasing = f.el('unload-qwen-voice').onclick();
  const request = f.calls.api.find((item) => item.route.endsWith('/load'));
  const releaseRequest = f.calls.api.find((item) => item.route.endsWith('/unload'));
  assert.equal(request.options.signal.aborted, true);
  assert.match(request.options.body.request_id, /^[a-f0-9]{32}$/);
  assert.equal(releaseRequest.options.body.request_id, request.options.body.request_id);
  load.resolve({ ...qwen, loaded: true }); await loading; await tick();
  assert.match(f.el('qwen-voice-status').textContent, /正在停止.*释放/);
  assert.equal(f.el('voice-provider').disabled, true); assert.equal(f.el('test-voice').disabled, true);
  assert.equal(f.el('load-qwen-voice').disabled, true); assert.equal(f.el('unload-qwen-voice').disabled, true);
  f.el('speak-replies').checked = true; f.companion.prepareSpeech({ newTurn: true });
  await f.el('unload-qwen-voice').onclick();
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/unload')).length, 1);
  unload.resolve(qwen); await releasing;
  assert.equal(f.el('voice-provider').disabled, false); assert.equal(f.el('load-qwen-voice').disabled, false);
  assert.equal(f.el('unload-qwen-voice').disabled, true);
  f.companion.prepareSpeech(); await tick();
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
});

for (const loadResult of ['success', 'failure']) for (const releaseResult of ['success', 'failure']) {
  test('late preload ' + loadResult + ' cannot override release ' + releaseResult + ' or disable its retry', async () => {
    const { f, load, unload, qwen } = pendingQwenFixture();
    f.openSettings(); await tick();
    const loading = f.el('load-qwen-voice').onclick();
    const releasing = f.el('unload-qwen-voice').onclick();
    if (releaseResult === 'success') unload.resolve(qwen);
    else unload.reject(new Error('synthetic cleanup failed'));
    await releasing;
    const message = f.el('qwen-voice-status').textContent;
    if (loadResult === 'success') load.resolve({ ...qwen, loaded: true });
    else load.reject(new Error('synthetic late preload failure'));
    await loading; await tick();
    assert.equal(f.el('qwen-voice-status').textContent, message);
    assert.equal(f.el('unload-qwen-voice').disabled, releaseResult === 'success');
    if (releaseResult === 'failure') {
      assert.match(message, /释放失败.*Qwen 请求未完成/);
      assert.match(f.el('media-status').textContent, /释放失败/);
      await f.el('unload-qwen-voice').onclick();
      const requests = f.calls.api.filter((item) => item.route.endsWith('/unload'));
      assert.equal(requests.length, 2);
      assert.equal(requests[1].options.body.request_id, requests[0].options.body.request_id);
    }
    f.el('speak-replies').checked = true; f.companion.prepareSpeech(); await tick();
    assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  });
}

for (const completion of ['success', 'failure']) {
  test('old preload ' + completion + ' cannot alter a new preload after release', async () => {
    const old = deferred(), fresh = deferred(); let loads = 0;
    const qwen = { state: 'ready', supported: true, loaded: false };
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
      if (route === '/api/voice/qwen/load') return ++loads === 1 ? old.promise : fresh.promise;
      if (route === '/api/voice/qwen/unload') return qwen;
      return {};
    } });
    f.openSettings(); await tick(); f.el('speak-replies').checked = true;
    const loading = f.el('load-qwen-voice').onclick();
    await f.el('unload-qwen-voice').onclick();
    f.companion.prepareSpeech({ newTurn: true }); await tick();
    const requests = f.calls.api.filter((item) => item.route.endsWith('/load'));
    assert.equal(requests.length, 2); assert.notEqual(requests[1].options.body.request_id, requests[0].options.body.request_id);
    if (completion === 'success') old.resolve({ ...qwen, loaded: true });
    else old.reject(new Error('synthetic obsolete failure'));
    await loading; await tick();
    assert.match(f.el('qwen-voice-status').textContent, /正在加载/);
    assert.equal(f.el('load-qwen-voice').disabled, true);
    assert.equal(f.el('unload-qwen-voice').disabled, false);
    f.companion.prepareSpeech({ newTurn: true }); assert.equal(loads, 2);
    fresh.resolve({ ...qwen, loaded: true }); await tick();
    assert.match(f.el('qwen-voice-status').textContent, /已预热/);
  });
}

for (const interruption of ['stop-audio', 'reading-off', 'engine', 'provider', 'pagehide']) {
  test(interruption + ' immediately stops speech and cancels pending preload by the same request ID', async () => {
    const { f, load, unload, qwen } = pendingQwenFixture();
    f.openSettings(); await tick(); f.el('speak-replies').checked = true;
    f.companion.prepareSpeech({ newTurn: true }); f.companion.beginTurn('synthetic-owned', 'synthetic-session');
    if (interruption === 'stop-audio') f.stop();
    if (interruption === 'reading-off') { f.el('speak-replies').checked = false; f.el('speak-replies').onchange(); }
    if (interruption === 'engine') { f.el('local-tts-engine').value = 'kokoro'; f.el('local-tts-engine').onchange(); }
    if (interruption === 'provider') { f.el('voice-provider').value = 'openai'; f.el('voice-provider').onchange(); }
    if (interruption === 'pagehide') f.close();
    assert.equal(f.companion.speakingTurn('synthetic-owned'), false);
    const loading = f.calls.api.find((item) => item.route.endsWith('/load'));
    const release = f.calls.api.find((item) => item.route.endsWith('/unload'));
    assert.equal(loading.options.signal.aborted, true);
    assert.equal(release.options.body.request_id, loading.options.body.request_id);
    assert.equal(f.el('test-voice').disabled, true);
    f.stop(); assert.equal(f.calls.api.filter((item) => item.route.endsWith('/unload')).length, 1);
    unload.resolve(qwen); await tick();
    load.resolve({ ...qwen, loaded: true }); await tick();
    assert.doesNotMatch(f.el('qwen-voice-status').textContent, /已预热/);
    assert.equal(f.calls.decoded, 0);
  });
}

for (const read of ['config', 'status']) {
  test('a stale ' + read + ' response cannot restore loaded status after explicit release', async () => {
    const wait = deferred(); let configs = 0;
    const qwen = { state: 'ready', supported: true, loaded: true };
    const value = { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return ++configs === 1 ? value : wait.promise;
      if (route === '/api/voice/qwen/status') return wait.promise;
      if (route === '/api/voice/qwen/unload') return { ...qwen, loaded: false };
      return {};
    } });
    f.el('speak-replies').checked = true; f.openSettings(); await tick();
    if (read === 'config') f.openSettings();
    else f.el('refresh-qwen-voice').onclick();
    await f.el('unload-qwen-voice').onclick();
    wait.resolve(read === 'config' ? value : qwen); await tick();
    assert.equal(f.el('unload-qwen-voice').disabled, true); assert.equal(f.el('load-qwen-voice').disabled, false);
    assert.doesNotMatch(f.el('qwen-voice-status').textContent, /已预热/);
    assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
  });
}


for (const action of ['load', 'unload']) for (const outcome of ['success', 'failure']) {
  test('config read started during Qwen ' + action + ' cannot override its completed ' + outcome, async () => {
    const actionWait = deferred(), configWait = deferred(); let configs = 0;
    const before = { state: 'ready', supported: true, loaded: action === 'unload' };
    const config = { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen: before };
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return ++configs === 1 ? config : configWait.promise;
      if (route === '/api/voice/qwen/' + action) return actionWait.promise;
      return {};
    } });
    f.openSettings(); await tick(); f.el('speak-replies').checked = true;
    const operation = f.el(action + '-qwen-voice').onclick();
    f.openSettings(); await tick();
    assert.equal(configs, 2, 'the delayed config request begins after the mutation starts');
    if (outcome === 'success') actionWait.resolve({ ...before, loaded: action === 'load' });
    else actionWait.reject(new Error('synthetic operation failure'));
    await operation;
    const settledMessage = f.el('qwen-voice-status').textContent;
    configWait.resolve(config); await tick();
    assert.equal(f.el('qwen-voice-status').textContent, settledMessage);
    const loadCalls = f.calls.api.filter((item) => item.route.endsWith('/load'));
    assert.equal(loadCalls.length, action === 'load' ? 1 : 0, 'a stale unloaded snapshot must not trigger another preload');
    if (outcome === 'failure') {
      assert.match(settledMessage, /失败.*Qwen 请求未完成/);
      assert.equal(f.el('unload-qwen-voice').disabled, false, 'cleanup retry remains available');
    } else {
      assert.equal(f.el('load-qwen-voice').disabled, action === 'load');
      assert.equal(f.el('unload-qwen-voice').disabled, action === 'unload');
    }
  });
}


for (const automatic of [false, true]) {
  test((automatic ? 'automatic' : 'explicit') + ' Qwen memory refusal immediately shows safe RAM and commit requirements without refresh', async () => {
    const secret = 'PRIVATE_EXCEPTION C:\\synthetic-private\\worker.py traceback token=DO_NOT_SHOW';
    const qwen = { state: 'ready', supported: true, loaded: false,
      preflight: { available_ram_mib: 1536, available_commit_mib: 8192, available_vram_mib: 3072 } };
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
      if (route === '/api/voice/qwen/load') throw Object.assign(new Error(secret), { code: 'qwen_memory_pressure', status: 400, detail: secret, publicMessage: secret });
      return {};
    } });
    f.openSettings(); await tick();
    if (automatic) { f.el('speak-replies').checked = true; f.el('speak-replies').onchange(); await tick(); }
    else await f.el('load-qwen-voice').onclick();
    const message = f.el('qwen-voice-status').textContent;
    assert.match(message, /预热失败.*可用内存或系统提交空间不足/);
    assert.match(message, /保护线.*RAM 1536 MiB.*commit.*8192 MiB.*8 GiB/);
    assert.match(message, /释放资源后重试/); assert.doesNotMatch(message, /PRIVATE|synthetic-private|traceback|DO_NOT_SHOW|重新启动/);
    assert.equal(f.calls.api.some((item) => item.route === '/api/voice/qwen/status'), false);
    if (automatic) assert.equal(f.el('media-status').textContent, message);
  });
}

for (const code of ['qwen_gpu_memory', 'qwen_resource_exhausted']) {
  test(code + ' uses the safe code to show relevant numeric resource requirements', async () => {
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen',
        qwen: { state: 'ready', supported: true, loaded: false, preflight: { available_ram_mib: 1536, available_commit_mib: 8192, available_vram_mib: 3072 } } };
      if (route === '/api/voice/qwen/load') throw Object.assign(new Error('PRIVATE_STACK'), { code, detail: 'PRIVATE_PATH', publicMessage: 'PRIVATE_MESSAGE' });
      return {};
    } });
    f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
    const message = f.el('qwen-voice-status').textContent;
    assert.match(message, /显存.*不足/); assert.match(message, /保护线.*3072 MiB.*3 GiB/);
    assert.doesNotMatch(message, /PRIVATE/);
    if (code === 'qwen_resource_exhausted') assert.match(message, /RAM 1536 MiB.*8192 MiB/);
  });
}

test('invalid preflight fields cannot inject error text and fall back to the fixed protection requirements', async () => {
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen',
      qwen: { state: 'ready', supported: true, loaded: false,
        preflight: { available_ram_mib: 'PRIVATE_PATH', available_commit_mib: -1, available_vram_mib: Infinity } } };
    if (route === '/api/voice/qwen/load') throw Object.assign(new Error('PRIVATE_ERROR'), { code: 'qwen_resource_exhausted' });
    return {};
  } });
  f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
  const message = f.el('qwen-voice-status').textContent;
  assert.match(message, /1536 MiB.*8192 MiB.*3072 MiB/);
  assert.doesNotMatch(message, /PRIVATE|Infinity/);
});

for (const code of ['qwen_unknown_worker_error', 'request_failed', '__proto__', undefined]) {
  test('Qwen errors never display arbitrary detail or exception text for ' + String(code), async () => {
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen: { state: 'ready', supported: true, loaded: false } };
      if (route === '/api/voice/qwen/load') throw Object.assign(new Error('PRIVATE_STACK_AND_PATH'), { code, detail: 'PRIVATE_DETAIL', publicMessage: 'PRIVATE_PUBLIC_MESSAGE' });
      return {};
    } });
    f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
    assert.match(f.el('qwen-voice-status').textContent, /预热失败.*Qwen 请求未完成/);
    assert.doesNotMatch(f.el('qwen-voice-status').textContent, /PRIVATE/);
  });
}

test('Qwen status refresh failure also suppresses arbitrary exception text', async () => {
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen: { state: 'ready', supported: true, loaded: false } };
    if (route === '/api/voice/qwen/status') throw new Error('PRIVATE_TRACEBACK_PATH');
    return {};
  } });
  f.openSettings(); await tick(); await f.el('refresh-qwen-voice').onclick();
  assert.match(f.el('qwen-voice-status').textContent, /读取 Qwen 状态失败/);
  assert.doesNotMatch(f.el('qwen-voice-status').textContent, /PRIVATE/);
});

test('Qwen synthesis memory errors show safe resource requirements immediately in the conversation', async () => {
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen',
      qwen: { state: 'ready', supported: true, loaded: true, preflight: { available_ram_mib: 1536, available_commit_mib: 8192, available_vram_mib: 3072 } } };
    if (route === '/api/voice/synthesize') throw Object.assign(new Error('PRIVATE_EXCEPTION'), { code: 'qwen_memory_pressure', status: 400, publicMessage: 'PRIVATE_PATH' });
    return {};
  } });
  f.openSettings(); await tick(); f.el('speak-replies').checked = true;
  f.companion.beginTurn('synthetic-memory', 'synthetic-session');
  f.companion.text('synthetic-memory', '我会先陪你把眼前这件事理清楚，'); await tick();
  assert.match(f.el('media-status').textContent, /可用内存或系统提交空间不足.*RAM 1536 MiB.*8192 MiB/);
  assert.doesNotMatch(f.el('media-status').textContent, /PRIVATE/);
});

test('Qwen resource refusal permits a direct manual retry after refresh and settings reopen with a fresh request ID', async () => {
  const retry = deferred(); let loads = 0;
  let qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', local: { state: 'ready' }, qwen };
    if (route === '/api/voice/qwen/status') return qwen;
    if (route === '/api/voice/qwen/load') {
      if (++loads === 1) {
        qwen = { ...qwen, state: 'error', message: '合成资源不足，保护拒绝预热。' };
        throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' });
      }
      return retry.promise;
    }
    return {};
  } });
  f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, false);
  assert.equal(f.el('load-qwen-voice').textContent, '重试预热 Qwen');
  assert.equal(f.el('test-voice').disabled, true);
  f.el('speak-replies').checked = true; f.el('speak-replies').onchange();
  f.companion.prepareSpeech({ newTurn: true });
  await f.el('refresh-qwen-voice').onclick();
  f.closeSettings(); f.openSettings(); await tick();
  assert.equal(loads, 1, 'error refresh, settings and new turns cannot automatically retry');
  assert.equal(f.el('load-qwen-voice').disabled, false);
  assert.equal(f.el('load-qwen-voice').textContent, '重试预热 Qwen');
  const loading = f.el('load-qwen-voice').onclick(); await tick();
  await f.el('load-qwen-voice').onclick(); f.companion.prepareSpeech({ newTurn: true });
  assert.equal(loads, 2); assert.equal(f.el('load-qwen-voice').disabled, true);
  assert.equal(f.el('unload-qwen-voice').disabled, false);
  const requests = f.calls.api.filter((item) => item.route.endsWith('/load'));
  for (const request of requests) assert.match(request.options.body.request_id, /^[a-f0-9]{32}$/);
  assert.notEqual(requests[0].options.body.request_id, requests[1].options.body.request_id);
  qwen = { ...qwen, state: 'ready', message: '', loaded: true };
  retry.resolve(qwen); await loading;
  assert.match(f.el('qwen-voice-status').textContent, /已预热/);
  assert.equal(f.el('load-qwen-voice').textContent, '预热 Qwen');
  assert.equal(f.el('load-qwen-voice').disabled, true);
  assert.equal(f.calls.api.some((item) => item.route.endsWith('/unload')), false, 'retry does not require an explicit release');
});

test('only known recoverable Qwen load codes offer manual retry without trusting arbitrary error text', async () => {
  for (const code of ['qwen_memory_pressure', 'qwen_gpu_memory', 'qwen_resource_exhausted', 'qwen_timeout', 'qwen_inference_failed', 'qwen_response']) {
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen: { state: 'ready', supported: true, loaded: false } };
      if (route.endsWith('/load')) throw Object.assign(new Error('PRIVATE'), { code, detail: 'PRIVATE', publicMessage: 'PRIVATE' });
      return {};
    } });
    f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
    assert.equal(f.el('load-qwen-voice').disabled, false, code);
    assert.equal(f.el('load-qwen-voice').textContent, '重试预热 Qwen');
    assert.doesNotMatch(f.el('qwen-voice-status').textContent, /PRIVATE/);
  }
});

test('a retry that discovers unavailable binding or an unknown failure cannot inherit its old resource retry eligibility', async () => {
  for (const code of ['qwen_binding_invalid', 'qwen_not_ready', 'qwen_unsupported', 'qwen_gpu_unavailable', 'qwen_closed', 'unknown', undefined]) {
    let loads = 0;
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen: { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 } };
      if (route.endsWith('/load')) throw Object.assign(new Error('PRIVATE'), { code: ++loads === 1 ? 'qwen_memory_pressure' : code });
      return {};
    } });
    f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
    assert.equal(f.el('load-qwen-voice').disabled, false);
    await f.el('load-qwen-voice').onclick();
    assert.equal(loads, 2); assert.equal(f.el('load-qwen-voice').disabled, true, String(code));
    await f.el('load-qwen-voice').onclick(); assert.equal(loads, 2);
  }
});

test('refreshed unsupported, missing or unverified Qwen binding states revoke previous resource retry eligibility', async () => {
  for (const change of [
    { supported: false }, { state: 'missing' }, { loaded: true },
    { idle_unload_seconds: undefined }, { idle_unload_seconds: '300' }, { idle_unload_seconds: 0 }, { idle_unload_seconds: Infinity },
  ]) {
    const qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen };
      if (route.endsWith('/load')) throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' });
      if (route.endsWith('/status')) return { ...qwen, state: 'error', message: '合成状态错误', ...change };
      return {};
    } });
    f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
    assert.equal(f.el('load-qwen-voice').disabled, false);
    await f.el('refresh-qwen-voice').onclick();
    assert.equal(f.el('load-qwen-voice').disabled, true, JSON.stringify(change));
    await f.el('load-qwen-voice').onclick();
    assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  }
});

test('manual resource retry still requires the selected provider and engine to be saved', async () => {
  let loads = 0;
  const qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen };
    if (route.endsWith('/load')) { loads++; throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' }); }
    return {};
  } });
  f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
  f.el('voice-provider').value = 'openai'; f.el('voice-provider').onchange();
  assert.equal(f.el('load-qwen-voice').disabled, true); await f.el('load-qwen-voice').onclick();
  f.el('voice-provider').value = 'local'; f.el('voice-provider').onchange();
  f.el('local-tts-engine').value = 'kokoro'; f.el('local-tts-engine').onchange();
  assert.equal(f.el('load-qwen-voice').disabled, true); await f.el('load-qwen-voice').onclick();
  assert.equal(loads, 1);
});

test('explicit release clears resource retry eligibility and a cleanup failure only permits release retry', async () => {
  const cleanup = deferred(); let releases = 0;
  const qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen };
    if (route.endsWith('/load')) throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' });
    if (route.endsWith('/unload')) return ++releases === 1 ? cleanup.promise : qwen;
    if (route.endsWith('/status')) return { ...qwen, state: 'error' };
    return {};
  } });
  f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
  const releasing = f.el('unload-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, true); assert.equal(f.el('unload-qwen-voice').disabled, true);
  cleanup.reject(Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' })); await releasing;
  assert.equal(f.el('load-qwen-voice').disabled, true);
  assert.equal(f.el('unload-qwen-voice').disabled, false);
  await f.el('refresh-qwen-voice').onclick(); await f.el('load-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, true);
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1);
  await f.el('unload-qwen-voice').onclick();
  const releaseRequests = f.calls.api.filter((item) => item.route.endsWith('/unload'));
  assert.equal(releaseRequests[0].options.body.request_id, releaseRequests[1].options.body.request_id);
  f.el('speak-replies').checked = true; f.companion.prepareSpeech();
  assert.equal(f.calls.api.filter((item) => item.route.endsWith('/load')).length, 1, 'release still suppresses background preheat');
});

test('an obsolete preload error cannot clear a newer manual retry or restore its retry eligibility', async () => {
  const old = deferred(), retry = deferred(); let loads = 0;
  const qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen };
    if (route.endsWith('/load')) {
      loads++;
      if (loads === 1) return old.promise;
      if (loads === 2) throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' });
      return retry.promise;
    }
    if (route.endsWith('/unload')) return qwen;
    return {};
  } });
  f.openSettings(); await tick();
  const obsolete = f.el('load-qwen-voice').onclick();
  await f.el('unload-qwen-voice').onclick(); await f.el('load-qwen-voice').onclick();
  const loading = f.el('load-qwen-voice').onclick();
  old.reject(Object.assign(new Error('PRIVATE'), { code: 'qwen_binding_invalid' })); await obsolete;
  assert.match(f.el('qwen-voice-status').textContent, /正在加载/);
  assert.equal(f.el('load-qwen-voice').disabled, true);
  assert.equal(f.el('unload-qwen-voice').disabled, false);
  retry.resolve({ ...qwen, loaded: true }); await loading;
  assert.match(f.el('qwen-voice-status').textContent, /已预热/);
  assert.equal(f.el('load-qwen-voice').textContent, '预热 Qwen');
  assert.equal(loads, 3);
});

for (const read of ['config', 'status']) {
  test('a stale ' + read + ' error cannot overwrite successful manual Qwen retry', async () => {
    const stale = deferred(); let loads = 0, configs = 0;
    const qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
    const config = { voice_provider: 'local', local_tts_engine: 'qwen', qwen };
    const f = fixture({ api: async (route) => {
      if (route === '/api/voice/config') return ++configs === 1 ? config : stale.promise;
      if (route.endsWith('/status')) return stale.promise;
      if (route.endsWith('/load')) {
        if (++loads === 1) throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' });
        return { ...qwen, loaded: true };
      }
      return {};
    } });
    f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
    if (read === 'config') f.openSettings(); else f.el('refresh-qwen-voice').onclick();
    await f.el('load-qwen-voice').onclick();
    const error = { ...qwen, state: 'error', message: '过期资源错误' };
    stale.resolve(read === 'config' ? { ...config, qwen: error } : error); await tick();
    assert.match(f.el('qwen-voice-status').textContent, /已预热/);
    assert.equal(f.el('load-qwen-voice').disabled, true);
    assert.equal(f.el('load-qwen-voice').textContent, '预热 Qwen');
    assert.equal(loads, 2);
  });
}

test('a new renderer cannot infer resource retry eligibility from status message or binding metadata alone', async () => {
  const qwen = { state: 'error', supported: true, loaded: false, idle_unload_seconds: 300, message: 'qwen_memory_pressure 内存不足' };
  const f = fixture({ api: async (route) => route === '/api/voice/config'
    ? { voice_provider: 'local', local_tts_engine: 'qwen', qwen } : qwen });
  f.openSettings(); await tick(); await f.el('refresh-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, true); await f.el('load-qwen-voice').onclick();
  assert.equal(f.calls.api.some((item) => item.route.endsWith('/load')), false);
});

test('a ready refresh clears previous resource retry eligibility before a later unclassified status error', async () => {
  let refreshes = 0;
  const qwen = { state: 'ready', supported: true, loaded: false, idle_unload_seconds: 300 };
  const f = fixture({ api: async (route) => {
    if (route === '/api/voice/config') return { voice_provider: 'local', local_tts_engine: 'qwen', qwen };
    if (route.endsWith('/load')) throw Object.assign(new Error('PRIVATE'), { code: 'qwen_memory_pressure' });
    if (route.endsWith('/status')) return ++refreshes === 1 ? qwen : { ...qwen, state: 'error' };
    return {};
  } });
  f.openSettings(); await tick(); await f.el('load-qwen-voice').onclick();
  await f.el('refresh-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, false);
  assert.equal(f.el('load-qwen-voice').textContent, '预热 Qwen');
  await f.el('refresh-qwen-voice').onclick();
  assert.equal(f.el('load-qwen-voice').disabled, true);
});
