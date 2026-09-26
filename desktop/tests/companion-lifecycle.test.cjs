'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { SpeechQueue } = require('../renderer/media.js');
const source = fs.readFileSync(path.join(__dirname, '../renderer/companion.js'), 'utf8');
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

// Run the production renderer unchanged. Every device, frame and service below
// is synthetic; these tests never request an operating-system media permission.
function fixture(overrides = {}) {
  const nodes = new Map(), streams = [], recorders = [], timers = new Map(), listeners = new Map();
  const calls = { cancel: 0, asr: 0, submitted: [], microphone: [], vision: [] };
  const el = (id) => {
    if (!nodes.has(id)) nodes.set(id, { value: '', checked: false, hidden: false, textContent: '', attributes: {},
      setAttribute(key, value) { this.attributes[key] = value; }, removeAttribute(key) { delete this[key]; }, replaceChildren() {}, focus() {}, add() {} });
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
  class Reader { readAsDataURL() { this.result = 'data:audio/webm;base64,c3ludGhldGlj'; queueMicrotask(() => this.onload()); } }
  class Context { async resume() { if (overrides.resume) await overrides.resume(); } }
  const chat = {
    canRecord: () => true,
    cancelTurn: async () => { calls.cancel++; if (overrides.cancel) await overrides.cancel(calls.cancel); },
    api: async (route, options) => { if (route === '/api/voice/transcribe') { calls.asr++; return overrides.transcribe ? overrides.transcribe(options) : { text: '合成问题' }; } return {}; },
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
    window: { aiNeko: bridge, aiNekoChat: chat, aiNekoMedia: { SpeechQueue }, addEventListener(name, callback) { listeners.set(name, callback); } },
    document: { body: { dataset: {} }, getElementById: el, querySelector: () => null, querySelectorAll: () => [] },
    navigator: { mediaDevices: { getUserMedia: async (constraints) => overrides.media ? overrides.media(constraints, makeStream) : makeStream(constraints.audio.deviceId?.exact || 'default') } },
    MediaRecorder: Recorder, FileReader: Reader, AudioContext: Context, Blob, AbortController, DOMException, crypto: globalThis.crypto,
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; }, clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(source, scope, { filename: 'companion.js' });
  return { el, calls, streams, recorders, timers, bridge, chat, frame, companion: scope.window.aiNekoCompanion,
    recording: () => scope.document.body.dataset.recording === 'true', close: () => listeners.get('pagehide')(),
    start: () => el('record-voice').onclick(), stop: () => el('stop-audio').onclick(),
    enable: (id) => { el('vision-source').value = id; el('vision-enabled').checked = true; return el('vision-enabled').onchange({ target: el('vision-enabled') }); },
    disable: () => { el('vision-enabled').checked = false; return el('vision-enabled').onchange({ target: el('vision-enabled') }); },
  };
}

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

test('late initialization rejection cannot stop or report an error on the new recording', async () => {
  const wait = deferred(); let attempts = 0;
  const f = fixture({ resume: () => ++attempts === 1 ? wait.promise : undefined });
  f.start(); await tick(); f.stop(); f.start(); await tick();
  wait.reject(new Error('old speaker failure')); await tick();
  assert.equal(f.recording(), true); assert.equal(f.streams[1].track.stopped, false);
  assert.ok(!f.el('media-status').textContent.includes('old speaker failure')); f.stop(); await tick();
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
