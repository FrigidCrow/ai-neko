'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { VisionCapture, microphonePermission } = require('../lib/vision.cjs');
const { SpeechQueue } = require('../renderer/media.js');
const { validateRequest, ENTRY_URL } = require('../lib/security.cjs');
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const source = (id, name) => ({ id, name, thumbnail: { isEmpty: () => false, toBitmap: () => Buffer.from([90, 110, 140, 255]), toJPEG: () => Buffer.from('synthetic image only') } });

test('vision requires explicit listed source and returns only that source with current metadata', async () => {
  const requests = [];
  const capture = new VisionCapture(async (options) => { requests.push(options); return [source('window:1:0', 'synthetic game'), source('screen:0:0', 'private screen')]; });
  await assert.rejects(capture.capture());
  await assert.rejects(capture.select('window:1:0'));
  const list = await capture.list();
  assert.equal('data_url' in list[0], false);
  assert.equal(requests[0].thumbnailSize.width, 0);
  const first = await capture.select('window:1:0');
  assert.equal(first.source_name, 'synthetic game');
  assert.equal(first.source_id, 'window:1:0');
  assert.ok(Date.now() - first.captured_at < 1000);
  assert.deepEqual(requests[1].types, ['window']);
  const second = await capture.capture();
  assert.notEqual(first.frame_id, second.frame_id);
});

test('revoking vision rejects already pending capture and makes no further captures', async () => {
  const wait = deferred(); let calls = 0;
  const capture = new VisionCapture(async () => { calls += 1; return calls === 1 ? [source('window:1:0', 'game')] : wait.promise; });
  await capture.list();
  const selected = capture.select('window:1:0');
  capture.disable();
  wait.resolve([source('window:1:0', 'game')]);
  await assert.rejects(selected, /关闭/);
  await assert.rejects(capture.capture(), /未开启/);
  assert.equal(calls, 2);
});

test('closed or replaced source cannot silently become a screen or similarly named window', async () => {
  let sources = [source('window:1:0', 'game')];
  const capture = new VisionCapture(async () => sources);
  await capture.list(); await capture.select('window:1:0');
  sources = [source('window:2:0', 'game'), source('screen:0:0', 'full screen')];
  await assert.rejects(capture.capture(), /关闭/);
  await assert.rejects(capture.capture(), /未开启/);
});

test('empty and black source failures remain explicit', async () => {
  const item = source('window:1:0', 'game');
  item.thumbnail.toBitmap = () => Buffer.from([0, 0, 0, 255]);
  const capture = new VisionCapture(async () => [item]);
  await capture.list();
  await assert.rejects(capture.select(item.id), /黑屏/);
  await assert.rejects(capture.capture(), /未开启/);
});

test('microphone grant is owned main frame, audio only, time bound; no camera or display capture', () => {
  const main = { mainFrame: { url: ENTRY_URL }, isDestroyed: () => false };
  assert.equal(microphonePermission(main, main, 'media', { mediaType: 'audio', isMainFrame: true }, 200, 100), true);
  assert.equal(microphonePermission(main, main, 'media', { mediaTypes: ['audio'], requestingUrl: ENTRY_URL }, 200, 100), true);
  for (const [contents, permission, details, time] of [
    [main, 'media', { mediaTypes: ['audio', 'video'] }, 100],
    [main, 'media', { mediaType: 'video' }, 100],
    [main, 'display-capture', { mediaType: 'audio' }, 100],
    [main, 'media', { mediaType: 'audio', isMainFrame: false }, 100],
    [main, 'media', { mediaType: 'audio', requestingUrl: 'https://evil.example' }, 100],
    [main, 'media', { mediaType: 'audio' }, 250],
    [{ ...main }, 'media', { mediaType: 'audio' }, 100],
  ]) assert.equal(microphonePermission(contents, main, permission, details, 200, time), false);
});

test('speech waits for sentences, preserves order, omits sources and flushes last fragment', async () => {
  const spoken = [];
  const queue = new SpeechQueue({ synthesize: async (text) => text, play: async (text) => spoken.push(text) });
  queue.append('先升级'); await tick(); assert.deepEqual(spoken, []);
  queue.append('人口。[S1] 再调整'); queue.append('站位', true); await tick();
  assert.deepEqual(spoken, ['先升级人口。', '再调整站位']);
});

test('stop while synthesizing rejects late audio, next generation can speak immediately', async () => {
  const wait = deferred(); const spoken = [];
  const queue = new SpeechQueue({ synthesize: async (text) => text === '旧。' ? wait.promise : text, play: async (text) => spoken.push(text) });
  queue.append('旧。后面也不读。'); queue.stop(); queue.append('新。'); await tick();
  wait.resolve('旧。'); await tick(); assert.deepEqual(spoken, ['新。']);
});

test('stop aborts an active audio source and empties every queued sentence', async () => {
  let stopped = false; const played = [];
  const queue = new SpeechQueue({ synthesize: async (text) => text, play: (text, signal) => new Promise((resolve) => { played.push(text); signal.addEventListener('abort', () => { stopped = true; resolve(); }, { once: true }); }) });
  queue.append('第一句。第二句。'); await tick(); queue.stop(); await tick();
  assert.equal(stopped, true); assert.deepEqual(played, ['第一句。']);
  assert.equal(queue.queue.length, 0);
});

test('new media and memory routes remain closed and payload bounds stay route specific', () => {
  for (const [method, path] of [['GET', '/api/persona'], ['PUT', '/api/persona'], ['GET', '/api/voice/config'], ['PUT', '/api/voice/config'], ['POST', '/api/voice/transcribe'], ['POST', '/api/voice/cancel'], ['GET', '/api/memories'], ['POST', '/api/memories'], ['PUT', '/api/memories/id'], ['DELETE', '/api/memories/id'], ['PUT', '/api/memory/config']]) assert.doesNotThrow(() => validateRequest({ method, path }));
  assert.doesNotThrow(() => validateRequest({ method: 'POST', path: '/api/voice/transcribe', body: { audio_base64: 'a'.repeat(100000) } }));
  assert.throws(() => validateRequest({ method: 'PUT', path: '/api/persona', body: { text: 'a'.repeat(100000) } }));
  assert.throws(() => validateRequest({ method: 'POST', path: '/api/voice/transcribe', body: { audio_base64: 'a'.repeat(12 * 1024 * 1024) } }));
  for (const route of ['/api/voice/arbitrary', '/api/memories/id/../config', '/api/memories?all=true']) assert.throws(() => validateRequest({ method: 'POST', path: route }));
});

test('superseded selection failure cannot revoke a newer successful selection', async () => {
  const wait = deferred(); let calls = 0;
  const sources = [source('window:1:0', 'first'), source('window:2:0', 'second')];
  const capture = new VisionCapture(async () => { calls++; return calls === 2 ? wait.promise : sources; });
  await capture.list(); const first = capture.select('window:1:0');
  await capture.select('window:2:0'); wait.resolve(sources);
  await assert.rejects(first, /关闭/);
  assert.equal((await capture.capture()).source_id, 'window:2:0');
});

test('failed synthesis stops the rest of that reply until the next explicit generation', async () => {
  let attempts = 0; const spoken = [];
  const queue = new SpeechQueue({ synthesize: async (text) => { attempts++; if (attempts === 1) throw new Error('synthetic network failure'); return text; }, play: async (text) => spoken.push(text) });
  queue.append('失败。后续。'); await tick(); queue.append('新片段也不自动重试。'); await tick();
  assert.equal(attempts, 1); queue.stop(); queue.append('下一轮。'); await tick();
  assert.deepEqual(spoken, ['下一轮。']);
});

test('sentences bind turn and segment identity before generation ends; only actual play announces started', async () => {
  const wait = deferred(); const seen = []; const activity = [];
  const queue = new SpeechQueue({ synthesize: async (text) => text,
    play: async (_audio, _signal, segment, started) => { seen.push(segment); await wait.promise; started(); },
    onActivity: (active) => activity.push(active),
  });
  queue.begin({ sessionId: 'session-a', turnId: 'turn-a' }); activity.length = 0;
  queue.append('Hello.你好；结尾', true); await tick();
  assert.deepEqual(activity, []);
  assert.equal(seen[0].text, 'Hello.');
  assert.deepEqual(seen[0].context, { sessionId: 'session-a', turnId: 'turn-a' });
  assert.match(seen[0].segment_id, /^[a-f0-9]{32}$/);
  wait.resolve(); await tick();
  assert.deepEqual(seen.map((item) => item.text), ['Hello.', '你好；', '结尾']);
  assert.equal(new Set(seen.map((item) => item.segment_id)).size, 3);
  assert.equal(activity[0], true);
});

test('URL periods do not create spoken URL fragments and punctuation alone is silent', async () => {
  const spoken = [];
  const queue = new SpeechQueue({ synthesize: async (text) => text, play: async (text) => spoken.push(text) });
  queue.append('参考 https://example.'); await tick();
  assert.deepEqual(spoken, []);
  queue.append('com/path 。下一步。！！', true); await tick();
  assert.deepEqual(spoken, ['参考  。', '下一步。']);
});
