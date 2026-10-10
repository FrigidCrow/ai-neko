'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { EventEmitter, getEventListeners } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createWindowCapture, verifyWindowCaptureHelper } = require('../lib/window-capture.cjs');

const source = { id: 'window:12345:0', name: '合成测试窗口', pid: 42, started: '639271872000000000' };
// The wrapper checks transport and JPEG boundaries; VisionCapture decodes pixels.
const jpeg = Buffer.from([0xff, 0xd8, 1, 2, 0xff, 0xd9]);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function fixture(options = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdin = new EventEmitter();
  child.input = [];
  child.stdin.end = (data) => { child.input.push(Buffer.from(data)); options.onInput?.(child); };
  child.kills = 0;
  child.kill = () => { child.kills += 1; return true; };
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const api = createWindowCapture({
    platform: 'win32', verifyHelper() {},
    spawn(executable, args, config) {
      calls.push({ executable, args, config });
      options.onSpawn?.(child);
      return child;
    },
    setTimeout(callback, duration) { const id = ++timerId; timers.set(id, { callback, duration }); return id; },
    clearTimeout(id) { timers.delete(id); },
    ...options.adapter,
  });
  return {
    api, child, calls, timers,
    output(value) { child.stdout.emit('data', Buffer.from(JSON.stringify(value), 'utf8')); },
    close(code = 0, signal = null) { child.emit('close', code, signal); },
    expire() { assert.equal(timers.size, 1); [...timers.values()][0].callback(); },
  };
}

function expectClean(f, signal) {
  assert.equal(f.timers.size, 0);
  if (signal) assert.equal(getEventListeners(signal, 'abort').length, 0);
  for (const [emitter, events] of [
    [f.child, ['error', 'close']], [f.child.stdin, ['error']],
    [f.child.stdout, ['data', 'error']], [f.child.stderr, ['data', 'error']],
  ]) for (const event of events) assert.equal(emitter.listenerCount(event), 0, event);
}

test('identify uses a fixed hidden native executable and exact image-free metadata protocol', async () => {
  const f = fixture();
  const controller = new AbortController();
  const pending = f.api.identify([{ ...source, ignored: 'not serialized' }], { signal: controller.signal });
  assert.equal(f.calls.length, 1);
  const call = f.calls[0];
  assert.equal(call.executable, path.resolve(__dirname, '../lib/window-capture.exe'));
  assert.deepEqual(call.args, []);
  assert.equal(call.config.windowsHide, true);
  assert.equal(call.config.shell, false);
  assert.deepEqual(call.config.stdio, ['pipe', 'pipe', 'pipe']);
  assert.deepEqual(JSON.parse(f.child.input[0].toString('utf8')), {
    operation: 'identify', sources: [{ id: source.id, name: source.name }],
  });
  assert.equal([...f.timers.values()][0].duration, 15000);
  f.output({ sources: [source] });
  f.close();
  assert.deepEqual(await pending, [source]);
  assert.equal(f.child.kills, 0);
  expectClean(f, controller.signal);
});

test('capture returns only bounded decoded JPEG after the owned helper closes', async () => {
  const f = fixture();
  const controller = new AbortController();
  const pending = f.api.capture({ ...source, ignored: 'not serialized' }, { signal: controller.signal });
  let settled = false;
  pending.finally(() => { settled = true; });
  assert.deepEqual(JSON.parse(f.child.input[0].toString('utf8')), { operation: 'capture', source });
  f.output({ jpeg: jpeg.toString('base64') });
  f.child.emit('exit', 0, null);
  await tick();
  assert.equal(settled, false, 'exit and completed stdout cannot replace close');
  f.close();
  assert.deepEqual(await pending, jpeg);
  expectClean(f, controller.signal);
});

test('identify may omit vanished windows but preserves complete names without truncation', async () => {
  const f = fixture();
  const full = { ...source, name: '合'.repeat(4096) };
  const pending = f.api.identify([full, { id: 'window:99:1', name: 'vanished' }]);
  f.output({ sources: [full] }); f.close();
  assert.deepEqual(await pending, [full]);
});

test('screen, malformed, zero, negative, overflow and imprecise window IDs fail before launch', async () => {
  for (const id of ['screen:0:0', 'window:0:0', 'window:-1:0', 'window:01:0', 'window:1:2',
    'window:1:0\n', 'window:9223372036854775808:0', 'window:1e5:0', 123]) {
    const f = fixture();
    await assert.rejects(f.api.identify([{ id, name: source.name }]), { code: 'VISION_CAPTURE_INVALID' });
    await assert.rejects(f.api.capture({ ...source, id }), { code: 'VISION_CAPTURE_INVALID' });
    assert.equal(f.calls.length, 0);
  }
});

test('source identity retains decimal 64-bit handles without numeric rounding', async () => {
  const f = fixture();
  const large = { ...source, id: 'window:9223372036854775807:1' };
  const pending = f.api.capture(large);
  assert.equal(JSON.parse(f.child.input[0]).source.id, large.id);
  f.output({ jpeg: jpeg.toString('base64') }); f.close(); await pending;
});

test('invalid process identity, started ticks and full title reject before launch', async () => {
  const invalid = [
    { pid: 0 }, { pid: -1 }, { pid: 1.5 }, { pid: 0x80000000 }, { pid: 0x100000000 }, { pid: '42' },
    { started: '' }, { started: '0' }, { started: '01' }, { started: '1e6' },
    { started: '3155378976000000000' }, { started: 639271872000000000 },
    { name: 'a'.repeat(4097) }, { name: 'one\0two' }, { name: null },
  ];
  for (const changes of invalid) {
    const f = fixture();
    await assert.rejects(f.api.capture({ ...source, ...changes }), { code: 'VISION_CAPTURE_INVALID' });
    assert.equal(f.calls.length, 0);
  }
});

test('array count, duplicate handles and UTF-8 input byte limit reject before launch', async () => {
  for (const sources of [null, [source, source], Array.from({ length: 251 }, (_, i) => ({ id: `window:${i + 1}:0`, name: '' })),
    Array.from({ length: 250 }, (_, i) => ({ id: `window:${i + 1}:0`, name: '合'.repeat(4096) }))]) {
    const f = fixture();
    await assert.rejects(f.api.identify(sources), { code: 'VISION_CAPTURE_INVALID' });
    assert.equal(f.calls.length, 0);
  }
});

test('unsupported platform and nonabsolute helper paths cannot launch helpers', async () => {
  for (const adapter of [{ platform: 'linux' }, { platform: 'darwin' }, { helperPath: 'window-capture.exe' },
    { helperPath: 'C:\\Windows\0x' }]) {
    const f = fixture({ adapter });
    await assert.rejects(f.api.capture(source));
    assert.equal(f.calls.length, 0);
  }
});

test('the build verifier is run before every launch and stale artifacts fail before spawn', async () => {
  let verified = 0;
  const f = fixture({ adapter: { verifyHelper(helperPath) {
    assert.equal(helperPath, path.resolve(__dirname, '../lib/window-capture.exe'));
    verified += 1;
    if (verified === 2) throw new Error('SECRET_BUILD_PATH');
  } } });
  const first = f.api.capture(source);
  f.output({ jpeg: jpeg.toString('base64') }); f.close(); await first;
  await assert.rejects(f.api.capture(source), (error) => {
    assert.equal(error.code, 'VISION_CAPTURE_BUILD');
    assert.match(error.message, /npm run build:window-capture/);
    assert.doesNotMatch(error.message, /SECRET/); return true;
  });
  assert.equal(verified, 2); assert.equal(f.calls.length, 1);
  expectClean(f);
});

test('abort during synchronous artifact verification cannot launch the executable', async () => {
  const controller = new AbortController();
  const f = fixture({ adapter: { verifyHelper() { controller.abort(); } } });
  await assert.rejects(f.api.capture(source, { signal: controller.signal }), { code: 'VISION_CAPTURE_ABORTED' });
  assert.equal(f.calls.length, 0); expectClean(f, controller.signal);
});

test('production verifier accepts matching local source and artifact and rejects stale or missing files', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-capture-verifier-'));
  const executable = path.join(directory, 'window-capture.exe');
  const code = path.join(directory, 'window-capture.cs');
  const build = path.join(directory, 'window-capture.build.json');
  // These are inert bytes solely for hashing; this test never executes a helper.
  const sourceBytes = Buffer.from('synthetic source bytes');
  const artifactBytes = Buffer.from('synthetic executable bytes, not a program');
  const manifest = {
    version: 1,
    source_sha256: createHash('sha256').update(sourceBytes).digest('hex'),
    executable_sha256: createHash('sha256').update(artifactBytes).digest('hex'),
    compiler: 'synthetic compiler', compiler_version: 'synthetic version',
  };
  const restore = () => {
    fs.writeFileSync(code, sourceBytes); fs.writeFileSync(executable, artifactBytes);
    fs.writeFileSync(build, JSON.stringify(manifest));
  };
  try {
    restore(); assert.doesNotThrow(() => verifyWindowCaptureHelper(executable));
    for (const missing of [code, executable, build]) {
      restore(); fs.unlinkSync(missing);
      assert.throws(() => verifyWindowCaptureHelper(executable), { code: 'VISION_CAPTURE_BUILD' });
    }
    for (const stale of [code, executable]) {
      restore(); fs.appendFileSync(stale, 'changed');
      assert.throws(() => verifyWindowCaptureHelper(executable), { code: 'VISION_CAPTURE_BUILD' });
    }
    for (const value of ['{', JSON.stringify({ ...manifest, version: 2 }),
      JSON.stringify({ ...manifest, executable_sha256: '0'.repeat(64) }),
      JSON.stringify({ ...manifest, source_sha256: manifest.source_sha256.toUpperCase() }),
      ' '.repeat(64 * 1024 + 1)]) {
      restore(); fs.writeFileSync(build, value);
      assert.throws(() => verifyWindowCaptureHelper(executable), { code: 'VISION_CAPTURE_BUILD' });
    }
    assert.throws(() => verifyWindowCaptureHelper(path.join(directory, 'different.exe')), { code: 'VISION_CAPTURE_BUILD' });
  } finally {
    // Remove only these three exact, test-owned files, then their empty directory.
    for (const file of [code, executable, build]) if (fs.existsSync(file)) fs.unlinkSync(file);
    fs.rmdirSync(directory);
  }
});

test('already aborted requests cannot spawn or attach listeners', async () => {
  const f = fixture();
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.api.capture(source, { signal: controller.signal }), { name: 'AbortError', code: 'VISION_CAPTURE_ABORTED' });
  assert.equal(f.calls.length, 0);
  expectClean(f, controller.signal);
});

test('abort during spawn kills only the returned owned helper and writes no request', async () => {
  const controller = new AbortController();
  const f = fixture({ onSpawn: () => controller.abort() });
  const pending = f.api.capture(source, { signal: controller.signal });
  const rejected = assert.rejects(pending, { code: 'VISION_CAPTURE_ABORTED' });
  assert.equal(f.child.input.length, 0);
  assert.equal(f.child.kills, 1);
  f.output({ jpeg: jpeg.toString('base64') }); f.close();
  await rejected;
  expectClean(f, controller.signal);
});

test('abort waits for close, ignores late success and cleans every listener and timer', async () => {
  const f = fixture(); const controller = new AbortController();
  const pending = f.api.capture(source, { signal: controller.signal });
  const rejected = assert.rejects(pending, { code: 'VISION_CAPTURE_ABORTED' });
  let settled = false; pending.then(() => { settled = true; }, () => { settled = true; });
  f.child.stdout.emit('data', Buffer.from('{"jpeg":"'));
  controller.abort();
  f.expire();
  f.output({ jpeg: jpeg.toString('base64') });
  await tick();
  assert.equal(settled, false);
  assert.equal(f.child.kills, 1);
  f.close(); await rejected;
  expectClean(f, controller.signal);
  controller.abort();
  assert.equal(f.child.kills, 1);
});

test('timeout kills only its helper and rejects after close even when late output is valid', async () => {
  const f = fixture(); const controller = new AbortController();
  const pending = f.api.identify([source], { signal: controller.signal });
  const rejected = assert.rejects(pending, { code: 'VISION_CAPTURE_TIMEOUT' });
  let settled = false; pending.catch(() => { settled = true; });
  f.expire(); f.output({ sources: [source] });
  await tick(); assert.equal(settled, false); assert.equal(f.child.kills, 1);
  f.close(null, 'SIGTERM'); await rejected; expectClean(f, controller.signal);
});

test('failed OS termination retries only its owned child and never invents closure or returns a late frame', async () => {
  for (const result of ['false', 'throw']) {
    const f = fixture(); const controller = new AbortController();
    f.child.kill = () => {
      f.child.kills += 1;
      if (result === 'throw') throw new Error('synthetic OS termination denied');
      return false;
    };
    const pending = f.api.capture(source, { signal: controller.signal });
    const rejected = assert.rejects(pending, { code: 'VISION_CAPTURE_ABORTED' });
    let settled = false; pending.then(() => { settled = true; }, () => { settled = true; });
    controller.abort(); assert.equal(f.child.kills, 1);
    f.expire(); f.output({ jpeg: jpeg.toString('base64') });
    await tick();
    assert.equal(settled, false, 'failed termination cannot stand in for actual close');
    assert.equal(f.child.kills, 2, 'the watchdog retries the same child after failed termination');
    assert.equal(f.calls.length, 1, 'no replacement helper or shared-process termination');
    f.close(); await rejected; expectClean(f, controller.signal);
  }
});

test('a synchronous kill error does not recurse and a later successful kill still waits for close', async () => {
  const f = fixture(); const controller = new AbortController();
  f.child.kill = () => {
    f.child.kills += 1;
    if (f.child.kills === 1) {
      f.child.emit('error', new Error('synthetic synchronous kill error'));
      return false;
    }
    return true;
  };
  const pending = f.api.capture(source, { signal: controller.signal });
  const rejected = assert.rejects(pending, { code: 'VISION_CAPTURE_ABORTED' });
  let settled = false; pending.catch(() => { settled = true; });
  controller.abort(); assert.equal(f.child.kills, 1);
  f.expire(); assert.equal(f.child.kills, 2);
  f.expire(); assert.equal(f.child.kills, 2, 'a successful termination request is not repeated');
  await tick(); assert.equal(settled, false);
  f.close(null, 'SIGTERM'); await rejected; expectClean(f, controller.signal);
});

test('spawn errors are sanitized and clean the abort listener without a child', async () => {
  const controller = new AbortController();
  const f = fixture({ adapter: { spawn() { throw new Error('SECRET_NATIVE_PATH_AND_TITLE'); } } });
  await assert.rejects(f.api.capture(source, { signal: controller.signal }), (error) => {
    assert.equal(error.code, 'VISION_CAPTURE_UNAVAILABLE');
    assert.doesNotMatch(error.message, /SECRET/); assert.equal(error.cause, undefined); return true;
  });
  expectClean(f, controller.signal);
});

test('child and pipe errors remain pending until close and do not expose raw exceptions', async () => {
  for (const stream of ['child', 'stdin', 'stdout', 'stderr']) {
    const f = fixture(); const controller = new AbortController();
    const pending = f.api.capture(source, { signal: controller.signal });
    const rejected = assert.rejects(pending, (error) => {
      assert.doesNotMatch(error.message, /SECRET/); return true;
    });
    let settled = false; pending.catch(() => { settled = true; });
    (stream === 'child' ? f.child : f.child[stream]).emit('error', new Error('SECRET_NATIVE_PATH_AND_TITLE'));
    await tick(); assert.equal(settled, false); assert.equal(f.child.kills, 1);
    f.close(1); await rejected; expectClean(f, controller.signal);
  }
});

test('native failure and stderr are sanitized with no retained native diagnostics', async () => {
  for (const [error, expected] of [['capture_failed', 'VISION_CAPTURE_GONE'], ['native_unavailable', 'VISION_CAPTURE_UNAVAILABLE']]) {
    const f = fixture(); const pending = f.api.capture(source);
    f.child.stderr.emit('data', Buffer.from('SECRET_WINDOW_TITLE_AND_NATIVE_ERROR'));
    f.output({ error }); f.close(1);
    await assert.rejects(pending, (result) => {
      assert.equal(result.code, expected); assert.doesNotMatch(result.message, /SECRET/); return true;
    });
    expectClean(f);
  }
});

test('truncated, empty and multiple JSON responses fail closed without a screen retry', async () => {
  for (const output of ['', '{"jpeg":', '{}\n{}', 'null', '[]', '{"jpeg":"/9j/2Q==","extra":true}']) {
    const f = fixture(); const pending = f.api.capture(source);
    f.child.stdout.emit('data', Buffer.from(output)); f.close();
    await assert.rejects(pending, { code: 'VISION_CAPTURE_PROTOCOL' });
    assert.equal(f.calls.length, 1); expectClean(f);
  }
});

test('identify rejects added, changed, duplicate and malformed window identities', async () => {
  for (const sources of [
    [{ ...source, id: 'window:54321:0' }], [{ ...source, name: 'changed title' }], [source, source],
    [{ ...source, pid: 0 }], [{ ...source, started: 'not ticks' }], [{ ...source, extra: true }],
    [{ ...source, id: 'screen:0:0' }],
  ]) {
    const f = fixture(); const pending = f.api.identify([source]);
    f.output({ sources }); f.close();
    await assert.rejects(pending, { code: 'VISION_CAPTURE_PROTOCOL' }); expectClean(f);
  }
});

test('malformed UTF-8 cannot be repaired into a matching source identity', async () => {
  const f = fixture(); const requested = { ...source, name: '\ufffd' };
  const pending = f.api.identify([requested]);
  f.child.stdout.emit('data', Buffer.concat([
    Buffer.from('{"sources":[{"id":"window:12345:0","name":"'), Buffer.from([0xff]),
    Buffer.from('","pid":42,"started":"639271872000000000"}]}'),
  ]));
  f.close(); await assert.rejects(pending, { code: 'VISION_CAPTURE_PROTOCOL' }); expectClean(f);
});

test('malformed base64, non-JPEG bytes and oversized JPEGs are rejected', async () => {
  const oversized = Buffer.alloc(2 * 1024 * 1024 + 1, 1);
  oversized[0] = 0xff; oversized[1] = 0xd8; oversized[oversized.length - 2] = 0xff; oversized[oversized.length - 1] = 0xd9;
  for (const encoded of ['', '%%%%', 'abc', '/9j/2Q=\n', Buffer.from('not jpeg').toString('base64'),
    Buffer.from([0xff, 0xd8, 0, 0]).toString('base64'), '/9j/2R==', oversized.toString('base64')]) {
    const f = fixture(); const pending = f.api.capture(source);
    f.output({ jpeg: encoded }); f.close();
    await assert.rejects(pending, { code: 'VISION_CAPTURE_PROTOCOL' }); expectClean(f);
  }
});

test('stdout and stderr share a bounded output budget and close controls rejection', async () => {
  for (const stream of ['stdout', 'stderr', 'combined']) {
    const f = fixture(); const pending = f.api.capture(source);
    const rejected = assert.rejects(pending, { code: 'VISION_CAPTURE_PROTOCOL' });
    let settled = false; pending.catch(() => { settled = true; });
    if (stream === 'combined') {
      f.child.stdout.emit('data', Buffer.alloc(2 * 1024 * 1024, 32));
      f.child.stderr.emit('data', Buffer.alloc(1024 * 1024 + 1, 32));
    } else f.child[stream].emit('data', Buffer.alloc(3 * 1024 * 1024 + 1, 32));
    await tick(); assert.equal(settled, false); assert.equal(f.child.kills, 1);
    f.close(); await rejected; expectClean(f);
  }
});

test('the maximum permitted JPEG remains accepted without truncating bytes', async () => {
  const image = Buffer.alloc(2 * 1024 * 1024, 1);
  image[0] = 0xff; image[1] = 0xd8; image[image.length - 2] = 0xff; image[image.length - 1] = 0xd9;
  const f = fixture(); const pending = f.api.capture(source);
  f.output({ jpeg: image.toString('base64') }); f.close();
  assert.deepEqual(await pending, image); expectClean(f);
});

test('parallel requests kill only the cancelled operation and keep the other operation live', async () => {
  const first = fixture(); const second = fixture(); const controller = new AbortController();
  const children = [first.child, second.child]; const timers = new Set();
  const api = createWindowCapture({
    platform: 'win32', verifyHelper() {}, spawn: () => children.shift(),
    setTimeout(callback) { timers.add(callback); return callback; },
    clearTimeout(callback) { timers.delete(callback); },
  });
  const cancelled = api.capture(source, { signal: controller.signal });
  const rejected = assert.rejects(cancelled, { code: 'VISION_CAPTURE_ABORTED' });
  const retained = api.capture({ ...source, id: 'window:99:0' });
  controller.abort();
  assert.equal(first.child.kills, 1); assert.equal(second.child.kills, 0);
  first.close(null, 'SIGTERM'); second.output({ jpeg: jpeg.toString('base64') }); second.close();
  await rejected; assert.deepEqual(await retained, jpeg);
  assert.equal(timers.size, 0);
  expectClean(first, controller.signal); expectClean(second);
});
