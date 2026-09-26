'use strict';

const { spawn, execFile, execFileSync } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const { validateDescriptor } = require('./security.cjs');

function commandFor({ packaged, resourcesPath, appPath, platform = process.platform }) {
  if (packaged) return { command: path.join(resourcesPath, 'backend', 'ai-neko.exe'), prefix: [] };
  const root = path.dirname(appPath);
  return {
    command: path.join(root, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    prefix: ['-m', 'ai_neko'],
  };
}

function initializePaths(command) {
  // This bounded bootstrap must finish in the initial main-process turn so
  // Electron's profile paths can be assigned before its ready event.
  let stdout;
  try {
    stdout = execFileSync(command.command, [...command.prefix, 'paths', '--initialize'], {
      windowsHide: true, timeout: 30000, killSignal: 'SIGKILL', maxBuffer: 16384,
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });
  } catch {
    throw new Error('无法初始化 ai-neko 专属数据目录。请检查目录设置与权限。');
  }
  try {
    const value = JSON.parse(stdout);
    if (!value || value.app_id !== 'ai-neko' || typeof value.data_root !== 'string' ||
        typeof value.desktop_root !== 'string' || !path.isAbsolute(value.data_root) ||
        value.desktop_root !== path.join(value.data_root, 'desktop')) throw new Error();
    return value;
  } catch { throw new Error('ai-neko 数据目录校验失败。'); }
}

function requestJSON(connection, method, route, encoded) {
  return new Promise((resolve, reject) => {
    const headers = { Authorization: `Bearer ${connection.token}`, Accept: 'application/json' };
    if (encoded !== undefined) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(encoded);
    }
    const request = http.request({ hostname: '127.0.0.1', port: connection.port,
      path: route, method, headers, agent: false }, (response) => {
      const chunks = [];
      let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > (route.startsWith('/api/voice/') ? 12 : 8) * 1024 * 1024) request.destroy(new Error('Response too large'));
        else chunks.push(chunk);
      });
      response.on('error', () => reject(new Error('本地服务连接已中断。')));
      response.on('end', () => {
        if (response.statusCode >= 300 && response.statusCode < 400) {
          return reject(new Error('本地服务返回了不允许的重定向。'));
        }
        try {
          resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) });
        } catch { reject(new Error('本地服务返回了无效响应。')); }
      });
    });
    request.setTimeout(route.startsWith('/api/voice/') ? 125000 : 15000, () => request.destroy());
    request.on('error', () => reject(new Error('无法连接本地服务，请退出后重新启动。')));
    request.end(encoded);
  });
}

class OwnedBackend {
  constructor(command, dataRoot, onStatus) {
    this.command = command;
    this.dataRoot = dataRoot;
    this.onStatus = onStatus;
    this.child = null;
    this.connection = null;
    this.stopping = false;
    this.timer = null;
    this.exitPromise = Promise.resolve();
  }

  start() {
    this.child = spawn(this.command.command,
      [...this.command.prefix, 'serve', '--desktop-parent', '--desktop-parent-pid',
        String(process.pid), '--data-dir', this.dataRoot], {
        // libuv's default Windows job kills children immediately with the host,
        // bypassing Python's cleanup. Keep pipes and our process reference, but
        // let the captured parent HANDLE request an orderly shutdown instead.
        detached: process.platform === 'win32',
        windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
      });
    const child = this.child;
    // Consume but never echo backend streams: credentials only cross the private
    // stdout pipe, and diagnostics may contain user-supplied provider addresses.
    child.stderr.resume();
    child.stdin.on('error', () => {});
    let pending = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      pending += chunk;
      if (pending.length > 65536) {
        pending = '';
        this.onStatus({ state: 'failed', message: '本地服务启动响应无效。' });
        void this.stop();
        return;
      }
      let end;
      while ((end = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, end);
        pending = pending.slice(end + 1);
        if (this.connection || this.stopping) continue;
        let candidate;
        try { candidate = validateDescriptor(JSON.parse(line)); } catch { continue; }
        this.connection = candidate;
        void requestJSON(candidate, 'GET', '/health').then((result) => {
          if (this.stopping || this.child !== child || child.exitCode !== null) return;
          if (result.status !== 200 || result.body.app_id !== 'ai-neko') throw new Error();
          clearTimeout(this.timer);
          this.onStatus({ state: 'ready', message: '可以和我聊天啦。' });
        }).catch(() => {
          if (!this.stopping) {
            this.onStatus({ state: 'failed', message: '本地服务验证失败，请退出后重新启动。' });
            void this.stop();
          }
        });
      }
    });
    this.exitPromise = new Promise((resolve) => {
      child.once('exit', (code) => {
        clearTimeout(this.timer);
        this.connection = null;
        if (!this.stopping) {
          this.onStatus({ state: 'failed', message: code === 3
            ? '此数据目录已有服务运行。请先退出旧版 ai-neko，再重新启动桌宠。'
            : '本地服务已结束。猫娘仍在这里，请退出后重新启动。' });
        }
        resolve();
      });
      child.once('error', () => {
        clearTimeout(this.timer);
        this.onStatus({ state: 'failed', message: '无法启动内置服务，请检查应用文件是否完整。' });
        resolve();
      });
    });
    this.timer = setTimeout(() => {
      this.onStatus({ state: 'failed', message: '本地服务启动超时，请退出后重新启动。' });
      void this.stop();
    }, 45000);
    this.timer.unref();
  }

  async request({ method, path: route, encoded }) {
    if (!this.connection || this.stopping) return { status: 503, body: { detail: '本地服务尚未准备好。' } };
    try { return await requestJSON(this.connection, method, route, encoded); }
    catch (error) { return { status: 503, body: { detail: error.message } }; }
  }

  async stop() {
    if (this.stopping) return this.exitPromise;
    this.stopping = true;
    clearTimeout(this.timer);
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    // EOF requests normal shutdown. Windows also holds a native handle to this
    // host because inherited pipe copies can delay EOF after a host crash.
    child.stdin.end();
    let timer;
    const exited = await Promise.race([this.exitPromise.then(() => true), new Promise((resolve) => {
      timer = setTimeout(() => resolve(false), 6500);
    })]);
    clearTimeout(timer);
    if (exited || child.exitCode !== null || child.signalCode !== null) return;
    // Only the still-live process spawned by this instance. Windows virtualenv
    // launchers may own a Python child, so terminate its tree, never a name or
    // a PID read from the connection descriptor.
    if (process.platform === 'win32') {
      await new Promise((resolve) => execFile('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'],
        { windowsHide: true, timeout: 5000 }, () => resolve()));
    } else child.kill('SIGKILL');
    await Promise.race([this.exitPromise, new Promise((resolve) => {
      timer = setTimeout(resolve, 3000);
    })]);
    clearTimeout(timer);
  }
}

module.exports = { commandFor, initializePaths, requestJSON, OwnedBackend };
