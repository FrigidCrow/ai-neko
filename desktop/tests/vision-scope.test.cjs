'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { VisionCapture } = require('../lib/vision.cjs');
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const source = (id, name) => ({ id, name, get thumbnail() { throw new Error('Enumeration pixels must never be accessed'); } });
const first = source('window:12:0', 'Synthetic A'), second = source('window:34:0', 'Synthetic B');
const bytes = Buffer.from('synthetic frame bytes');
function fixture(options = {}) {
  const enumerations = [], identities = [], captures = [];
  const adapter = {
    async identify(sources, context) {
      identities.push({ sources, ...context });
      return sources.map((entry) => ({ ...entry, pid: 20, started: '638900000000000000' }));
    },
    async capture(selected, context) { captures.push({ selected, ...context }); return bytes; },
    ...options.adapter,
  };
  const vision = new VisionCapture(async (request) => {
    enumerations.push(request);
    assert.deepEqual(request, { types: ['window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false });
    return options.getSources ? options.getSources() : [first, second, source('screen:0:0', 'Excluded screen')];
  }, adapter, options.decode || (() => ({ isEmpty: () => false, getSize: () => ({ width: 100, height: 60 }), toBitmap: () => Buffer.from([20, 30, 40, 255]) })));
  return { vision, enumerations, identities, captures };
}

test('enumeration exposes labels only; each frame uses exactly one selected native identity', async () => {
  const { vision, enumerations, identities, captures } = fixture();
  const entries = await vision.list();
  assert.deepEqual(entries, [first, second].map(({ id, name }) => ({ id, name, kind: 'window' })));
  assert.equal(captures.length, 0);
  assert.equal(identities.length, 1);
  const a = await vision.select(first.id), b = await vision.select(second.id);
  assert.equal(a.source_id, first.id); assert.equal(b.source_id, second.id);
  assert.deepEqual(captures.map(({ selected }) => selected.id), [first.id, second.id]);
  assert.equal(captures[0].selected.pid, 20);
  assert.equal(captures[0].selected.started, '638900000000000000');
  assert.equal(a.data_url, `data:image/jpeg;base64,${bytes.toString('base64')}`);
  assert.equal(enumerations.length, 5);
  await assert.rejects(vision.select('screen:0:0'), /选择/);
  await assert.rejects(vision.capture(), /未开启/);
  assert.equal(captures.length, 2);
});

test('full titles, including suffix beyond display truncation, bind the selected identity', async () => {
  let title = 'x'.repeat(240) + 'A';
  const { vision, captures } = fixture({ getSources: () => [source(first.id, title)] });
  assert.equal((await vision.list())[0].name.length, 240);
  title = 'x'.repeat(240) + 'B';
  await assert.rejects(vision.select(first.id), /关闭/);
  assert.equal(captures.length, 0);
});

test('metadata identity mismatch is excluded, never substituted by a different window', async () => {
  const { vision, captures } = fixture({ adapter: { identify: async () => [{ id: first.id, name: 'wrong title', pid: 1, started: '1' }] } });
  assert.deepEqual(await vision.list(), []);
  await assert.rejects(vision.select(first.id), /选择/);
  assert.equal(captures.length, 0);
});

test('failed list refresh revokes old selection and makes every old option unselectable', async () => {
  let fail = false;
  const { vision, captures } = fixture({ getSources: () => { if (fail) throw new Error('metadata unavailable'); return [first]; } });
  await vision.list(); await vision.select(first.id); fail = true;
  await assert.rejects(vision.list(), /metadata/);
  await assert.rejects(vision.select(first.id), /选择/);
  await assert.rejects(vision.capture(), /未开启/);
  assert.equal(captures.length, 1);
});

test('source disappearance or replacement after capture discards the frame', async () => {
  let sources = [first];
  const { vision } = fixture({ getSources: () => sources, adapter: { capture: async () => { sources = [second]; return bytes; } } });
  await vision.list();
  await assert.rejects(vision.select(first.id), /关闭/);
  await assert.rejects(vision.capture(), /未开启/);
});

test('native identity rejection revokes selection without retry or fallback', async () => {
  let attempts = 0;
  const { vision } = fixture({ adapter: { capture: async () => { attempts++; throw new Error('窗口进程已变化'); } } });
  await vision.list();
  await assert.rejects(vision.select(first.id), /变化/);
  await assert.rejects(vision.capture(), /未开启/);
  assert.equal(attempts, 1);
});

test('stop aborts owned work immediately and waits for native cleanup before resolving', async () => {
  const cleanup = deferred(); let signal, stopped = false;
  const { vision } = fixture({ adapter: { capture: async (_source, context) => { signal = context.signal; return cleanup.promise; } } });
  await vision.list(); const pending = vision.select(first.id); await tick();
  const rejected = assert.rejects(pending, /关闭/);
  const stop = vision.stop().then(() => { stopped = true; });
  assert.equal(signal.aborted, true); await tick(); assert.equal(stopped, false);
  cleanup.resolve(bytes); await Promise.all([rejected, stop]);
  assert.equal(stopped, true); assert.equal(vision.operations.size, 0);
});

test('new selection waits for old helper cleanup and an old failure cannot revoke it', async () => {
  const old = deferred(); const attempted = []; let firstSignal;
  const { vision } = fixture({ adapter: { capture: async (selected, { signal }) => {
    attempted.push(selected.id); if (selected.id === first.id) { firstSignal = signal; return old.promise; } return bytes;
  } } });
  await vision.list(); const a = vision.select(first.id); await tick();
  const rejected = assert.rejects(a, /关闭/); const b = vision.select(second.id); await tick();
  assert.equal(firstSignal.aborted, true); assert.deepEqual(attempted, [first.id]);
  old.reject(new Error('old helper failure')); await rejected;
  assert.equal((await b).source_id, second.id);
  assert.equal((await vision.capture()).source_id, second.id);
});

test('disable during metadata enumeration prevents the native capture from starting', async () => {
  const wait = deferred(); let calls = 0;
  const { vision, captures } = fixture({ getSources: () => ++calls === 2 ? wait.promise : [first] });
  await vision.list(); const pending = vision.select(first.id); await tick();
  vision.disable(); wait.resolve([first]);
  await assert.rejects(pending, /关闭/); assert.equal(captures.length, 0);
});

test('stale metadata listing cannot overwrite the current list or acquire native work after disable', async () => {
  const wait = deferred(); let calls = 0;
  const { vision, identities } = fixture({ getSources: () => ++calls === 1 ? wait.promise : [second] });
  const old = vision.list(); const rejected = assert.rejects(old, /关闭/);
  await tick();
  assert.equal((await vision.list())[0].id, second.id);
  wait.resolve([first]); await rejected; assert.equal(identities.length, 1);
  assert.equal((await vision.select(second.id)).source_id, second.id);
});

test('concurrent same-selection capture is refused without disrupting the active capture', async () => {
  const wait = deferred(); let count = 0;
  const { vision } = fixture({ adapter: { capture: async () => ++count === 2 ? wait.promise : bytes } });
  await vision.list(); await vision.select(first.id);
  const pending = vision.capture(); await tick();
  await assert.rejects(vision.capture(), /正在/); wait.resolve(bytes);
  assert.equal((await pending).source_id, first.id); assert.equal(count, 2);
});

for (const [name, buffer, decoded, error] of [
  ['empty bytes', Buffer.alloc(0), null, /为空/],
  ['oversized bytes', Buffer.alloc(2 * 1024 * 1024 + 1), null, /过大/],
  ['empty decoded image', bytes, { isEmpty: () => true }, /关闭/],
  ['oversized decoded image', bytes, { isEmpty: () => false, getSize: () => ({ width: 1367, height: 768 }) }, /关闭/],
  ['black pixels', bytes, { isEmpty: () => false, getSize: () => ({ width: 1, height: 1 }), toBitmap: () => Buffer.from([0, 0, 0, 255]) }, /黑屏/],
]) test(`${name} fails closed`, async () => {
  const { vision } = fixture({ adapter: { capture: async () => buffer }, decode: () => decoded });
  await vision.list(); await assert.rejects(vision.select(first.id), error);
  await assert.rejects(vision.capture(), /未开启/);
});
