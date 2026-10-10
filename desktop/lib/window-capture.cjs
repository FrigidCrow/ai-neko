'use strict';

const childProcess = require('node:child_process');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const MAX_INPUT = 2 * 1024 * 1024;
const MAX_OUTPUT = 3 * 1024 * 1024;
const MAX_JPEG = 2 * 1024 * 1024;
const MAX_HWND = 9223372036854775807n;
const MAX_STARTED = 3155378975999999999n;
const MESSAGES = Object.freeze({
  unsupported: '当前系统不支持安全窗口截屏。',
  invalid: '请选择列表中的有效窗口，不能使用整个屏幕。',
  aborted: '窗口观察已关闭，已取消截屏。',
  timeout: '窗口截屏超时，请重新选择窗口后重试。',
  unavailable: '安全窗口截屏助手不可用，请重新选择窗口后重试。',
  build: '窗口采集组件尚未构建或已过期，请运行 npm run build:window-capture。',
  gone: '所选窗口已关闭、发生变化或不支持安全截屏，请重新选择窗口。',
  protocol: '窗口截屏返回了无效数据，请重新选择窗口。',
});

function failure(kind) {
  const error = new Error(MESSAGES[kind]);
  error.code = `VISION_CAPTURE_${kind.toUpperCase()}`;
  if (kind === 'aborted') error.name = 'AbortError';
  return error;
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, keys) {
  return object(value) && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key));
}

function sourceIdentity(source, requireProcess = false) {
  if (!object(source) || typeof source.id !== 'string' || source.id.length > 30
    || typeof source.name !== 'string' || source.name.length > 4096 || source.name.includes('\0')) {
    throw failure('invalid');
  }
  const match = /^window:([1-9][0-9]*):[01]$/.exec(source.id);
  if (!match || BigInt(match[1]) > MAX_HWND) throw failure('invalid');
  const result = { id: source.id, name: source.name };
  if (requireProcess) {
    if (!Number.isInteger(source.pid) || source.pid < 1 || source.pid > 0x7fffffff
      || typeof source.started !== 'string' || !/^[1-9][0-9]{0,18}$/.test(source.started)
      || BigInt(source.started) > MAX_STARTED) throw failure('invalid');
    result.pid = source.pid;
    result.started = source.started;
  }
  return result;
}

function readBounded(file, limit) {
  const stat = fs.statSync(file);
  if (!stat.isFile() || stat.size < 1 || stat.size > limit) throw failure('build');
  const data = fs.readFileSync(file);
  if (!data.length || data.length > limit) throw failure('build');
  return data;
}

// Verify the locally built artifact against its exact adjacent source every time;
// the runtime never invokes a compiler or executes script text as a fallback.
function verifyWindowCaptureHelper(helperPath = path.join(__dirname, 'window-capture.exe')) {
  try {
    if (typeof helperPath !== 'string' || !path.isAbsolute(helperPath)
      || path.basename(helperPath) !== 'window-capture.exe') throw failure('build');
    const directory = path.dirname(helperPath);
    const manifest = JSON.parse(readBounded(path.join(directory, 'window-capture.build.json'), 64 * 1024).toString('utf8'));
    if (!object(manifest) || manifest.version !== 1
      || !/^[a-f0-9]{64}$/.test(manifest.source_sha256)
      || !/^[a-f0-9]{64}$/.test(manifest.executable_sha256)) throw failure('build');
    const source = readBounded(path.join(directory, 'window-capture.cs'), 1024 * 1024);
    const executable = readBounded(helperPath, 16 * 1024 * 1024);
    if (createHash('sha256').update(source).digest('hex') !== manifest.source_sha256
      || createHash('sha256').update(executable).digest('hex') !== manifest.executable_sha256) throw failure('build');
  } catch { throw failure('build'); }
}

/**
 * Each operation owns one short-lived native helper. Its protocol contains only
 * explicit window handles; no shell, source picker, stream, or screen fallback.
 * Once spawned, an operation settles only after `close`, including cancellation.
 */
function createWindowCapture({
  spawn = childProcess.spawn,
  platform = process.platform,
  helperPath = path.join(__dirname, 'window-capture.exe'),
  verifyHelper = verifyWindowCaptureHelper,
  timeoutMs = 15000,
  setTimeout: schedule = globalThis.setTimeout,
  clearTimeout: unschedule = globalThis.clearTimeout,
} = {}) {
  function run(payload, parseResult, { signal } = {}) {
    if (platform !== 'win32') return Promise.reject(failure('unsupported'));
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) {
      return Promise.reject(failure('unavailable'));
    }
    if (signal && (typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function')) {
      return Promise.reject(failure('invalid'));
    }
    if (signal?.aborted) return Promise.reject(failure('aborted'));
    const input = Buffer.from(JSON.stringify(payload), 'utf8');
    if (input.length > MAX_INPUT) return Promise.reject(failure('invalid'));
    try {
      if (typeof helperPath !== 'string' || !path.isAbsolute(helperPath) || helperPath.includes('\0')) throw failure('build');
      verifyHelper(helperPath);
    } catch { return Promise.reject(failure('build')); }
    if (signal?.aborted) return Promise.reject(failure('aborted'));

    return new Promise((resolve, reject) => {
      let child;
      let timer;
      let pendingError;
      let killed = false;
      let killing = false;
      let closed = false;
      let chunks = [];
      let outputBytes = 0;

      const killOwned = () => {
        if (!child || killed || killing || closed) return;
        // kill() can emit an error synchronously. Avoid reentrancy, and let the
        // watchdog retry this same child if the OS rejected the first attempt.
        killing = true;
        try { killed = child.kill() === true; } catch { /* Still wait for close. */ }
        finally { killing = false; }
      };
      const stop = (error) => {
        if (closed) return;
        pendingError ||= error;
        chunks = [];
        killOwned();
      };
      const onAbort = () => stop(failure('aborted'));
      const onError = () => stop(failure('unavailable'));
      const onInputError = () => stop(failure('unavailable'));
      const onOutputError = () => stop(failure('protocol'));
      const onOutput = (chunk) => {
        if (closed || pendingError) return;
        const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        outputBytes += bytes.length;
        if (outputBytes > MAX_OUTPUT) { stop(failure('protocol')); return; }
        chunks.push(bytes);
      };
      const onStderr = (chunk) => {
        if (closed || pendingError) return;
        // Never retain or expose native exception text, titles, or diagnostics.
        outputBytes += Buffer.isBuffer(chunk) ? chunk.length : Buffer.byteLength(chunk);
        if (outputBytes > MAX_OUTPUT) stop(failure('protocol'));
      };
      const cleanup = () => {
        if (timer !== undefined) unschedule(timer);
        signal?.removeEventListener('abort', onAbort);
        child?.removeListener('error', onError);
        child?.removeListener('close', onClose);
        child?.stdin?.removeListener('error', onInputError);
        child?.stdout?.removeListener('data', onOutput);
        child?.stdout?.removeListener('error', onOutputError);
        child?.stderr?.removeListener('data', onStderr);
        child?.stderr?.removeListener('error', onOutputError);
        chunks = [];
      };
      const onClose = (code, closeSignal) => {
        if (closed) return;
        closed = true;
        let result;
        if (!pendingError) {
          try {
            const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
            const value = JSON.parse(text);
            if (code !== 0 || closeSignal) {
              pendingError = failure(value?.error === 'native_unavailable' ? 'unavailable' : 'gone');
            } else {
              result = parseResult(value);
            }
          } catch { pendingError = failure(code === 0 && !closeSignal ? 'protocol' : 'unavailable'); }
        }
        cleanup();
        if (pendingError) reject(pendingError);
        else resolve(result);
      };

      signal?.addEventListener('abort', onAbort, { once: true });
      try {
        child = spawn(helperPath, [], {
          cwd: __dirname, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch {
        cleanup();
        reject(pendingError || failure('unavailable'));
        return;
      }
      child.once('close', onClose);
      child.on('error', onError);
      child.stdin?.on('error', onInputError);
      child.stdout?.on('error', onOutputError);
      child.stderr?.on('error', onOutputError);
      child.stdout?.on('data', onOutput);
      child.stderr?.on('data', onStderr);
      timer = schedule(() => stop(failure('timeout')), timeoutMs);
      // Abort can occur inside spawn, before there is a child to terminate.
      if (signal?.aborted || pendingError) { stop(pendingError || failure('aborted')); return; }
      if (!child.stdin || !child.stdout || !child.stderr) { stop(failure('unavailable')); return; }
      try { child.stdin.end(input); } catch { stop(failure('unavailable')); }
    });
  }

  return Object.freeze({
    async identify(sources, options) {
      if (!Array.isArray(sources) || sources.length > 250) throw failure('invalid');
      const inputs = sources.map((source) => sourceIdentity(source));
      const names = new Map(inputs.map((source) => [source.id, source.name]));
      if (names.size !== inputs.length) throw failure('invalid');
      return run({ operation: 'identify', sources: inputs }, (value) => {
        if (!exactKeys(value, ['sources']) || !Array.isArray(value.sources) || value.sources.length > inputs.length) {
          throw failure('protocol');
        }
        const seen = new Set();
        return value.sources.map((source) => {
          if (!exactKeys(source, ['id', 'name', 'pid', 'started'])) throw failure('protocol');
          const record = sourceIdentity(source, true);
          if (!names.has(record.id) || names.get(record.id) !== record.name || seen.has(record.id)) {
            throw failure('protocol');
          }
          seen.add(record.id);
          return record;
        });
      }, options);
    },
    async capture(source, options) {
      const input = sourceIdentity(source, true);
      return run({ operation: 'capture', source: input }, (value) => {
        if (!exactKeys(value, ['jpeg']) || typeof value.jpeg !== 'string' || !value.jpeg.length
          || value.jpeg.length > 4 * Math.ceil(MAX_JPEG / 3) || value.jpeg.length % 4 !== 0
          || !/^[A-Za-z0-9+/]*={0,2}$/.test(value.jpeg)) throw failure('protocol');
        const bytes = Buffer.from(value.jpeg, 'base64');
        if (bytes.length < 4 || bytes.length > MAX_JPEG || bytes.toString('base64') !== value.jpeg
          || bytes[0] !== 0xff || bytes[1] !== 0xd8
          || bytes[bytes.length - 2] !== 0xff || bytes[bytes.length - 1] !== 0xd9) throw failure('protocol');
        return bytes;
      }, options);
    },
  });
}

module.exports = { createWindowCapture, verifyWindowCaptureHelper };
