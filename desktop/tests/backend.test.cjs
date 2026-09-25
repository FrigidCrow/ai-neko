'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');
const { commandFor, initializePaths, requestJSON, OwnedBackend } = require('../lib/backend.cjs');

test('production resolves only the bundled backend and development only this repo venv', () => {
  assert.match(commandFor({ packaged: true, resourcesPath: '/app/resources', appPath: '/app/resources/app' }).command,
    /resources[/\\]backend[/\\]ai-neko\.exe$/);
  assert.deepEqual(commandFor({ packaged: false, resourcesPath: '/irrelevant', appPath: '/repo/desktop', platform: 'darwin' }),
    { command: path.join('/repo', '.venv', 'bin/python'), prefix: ['-m', 'ai_neko'] });
});

test('path bootstrap returns validated owned paths before the event loop can advance', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-paths-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const fixture = path.join(root, 'paths.cjs');
  const expected = { app_id: 'ai-neko', data_root: root, desktop_root: path.join(root, 'desktop') };
  await fs.writeFile(fixture, `
    require('node:assert/strict').deepEqual(process.argv.slice(2), ['paths', '--initialize']);
    setTimeout(() => process.stdout.write(${JSON.stringify(JSON.stringify(expected))}), 40);
  `);
  let advanced = false;
  queueMicrotask(() => { advanced = true; });
  const result = initializePaths({ command: process.execPath, prefix: [fixture] });
  assert.equal(advanced, false);
  assert.equal(typeof result.then, 'undefined');
  assert.deepEqual(result, expected);
});

test('path bootstrap rejects a foreign identity, relative roots and sibling profile paths', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-path-invalid-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const fixture = path.join(root, 'paths.cjs');
  const valid = { app_id: 'ai-neko', data_root: root, desktop_root: path.join(root, 'desktop') };
  for (const invalid of [null, { ...valid, app_id: 'another-app' },
    { ...valid, data_root: 'relative', desktop_root: 'relative/desktop' },
    { ...valid, desktop_root: path.join(root, 'another-profile') }]) {
    await fs.writeFile(fixture, `process.stdout.write(${JSON.stringify(JSON.stringify(invalid))});`);
    assert.throws(() => initializePaths({ command: process.execPath, prefix: [fixture] }),
      { message: 'ai-neko 数据目录校验失败。' });
  }
});

test('path bootstrap bounds output and does not disclose child stdout or stderr on failure', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-path-output-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const fixture = path.join(root, 'paths.cjs');
  for (const source of [
    "process.stderr.write('synthetic-private-provider-detail'); process.exit(2);",
    "process.stdout.write('x'.repeat(20000));",
  ]) {
    await fs.writeFile(fixture, source);
    assert.throws(() => initializePaths({ command: process.execPath, prefix: [fixture] }),
      { message: '无法初始化 ai-neko 专属数据目录。请检查目录设置与权限。' });
  }
});

test('owned backend boots through its private pipe and graceful EOF waits for its exit', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-child-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const fixture = path.join(root, 'backend.cjs');
  await fs.writeFile(fixture, `
    const http = require('node:http');
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ app_id: 'ai-neko', status: 'ok' }));
    });
    server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({
      event: 'connection', app_id: 'ai-neko', protocol_version: 1, pid: process.pid,
      host: '127.0.0.1', port: server.address().port, token: 'a'.repeat(43),
      instance_id: 'a'.repeat(32), http_url: 'http://127.0.0.1:' + server.address().port,
    })));
    process.stdin.resume();
    process.stdin.on('end', () => server.close());
  `);
  let ready;
  const readyPromise = new Promise((resolve) => { ready = resolve; });
  const updates = [];
  const backend = new OwnedBackend({ command: process.execPath, prefix: [fixture] }, root,
    (status) => { updates.push(status); if (status.state === 'ready') ready(); });
  t.after(() => backend.stop());
  backend.start();
  let timeout;
  try { await Promise.race([readyPromise, new Promise((_resolve, reject) => {
    timeout = setTimeout(() => reject(new Error('Synthetic child startup timeout')), 5000);
  })]); } finally { clearTimeout(timeout); }
  assert.equal((await backend.request({ method: 'GET', path: '/api/config' })).status, 200);
  await backend.stop();
  assert.equal(backend.child.exitCode, 0);
  assert.equal(backend.connection, null);
  assert.equal(updates.some((value) => value.state === 'failed'), false);
  assert.equal((await backend.request({ method: 'GET', path: '/api/config' })).status, 503);
});

test('loopback proxy sends bearer only to its owned endpoint and rejects redirects', async (t) => {
  let redirected = false;
  const destination = http.createServer((_request, response) => { redirected = true; response.end('{}'); });
  const source = http.createServer((request, response) => {
    assert.equal(request.headers.authorization, 'Bearer synthetic-token');
    assert.equal(request.headers.origin, undefined);
    if (request.url === '/redirect') {
      response.writeHead(302, { Location: `http://127.0.0.1:${destination.address().port}/stolen` });
      response.end('{}');
    } else { response.setHeader('Content-Type', 'application/json'); response.end('{"ok":true}'); }
  });
  await Promise.all([new Promise((resolve) => source.listen(0, '127.0.0.1', resolve)),
    new Promise((resolve) => destination.listen(0, '127.0.0.1', resolve))]);
  t.after(() => { source.close(); destination.close(); });
  const connection = { port: source.address().port, token: 'synthetic-token' };
  assert.deepEqual(await requestJSON(connection, 'GET', '/api/config'), { status: 200, body: { ok: true } });
  await assert.rejects(requestJSON(connection, 'GET', '/redirect'), /重定向/);
  assert.equal(redirected, false);
});
