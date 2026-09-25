'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { ENTRY_URL, CONSENT_URL, trustedSender, validateRequest, validateDescriptor, externalURL, localAsset } = require('../lib/security.cjs');

test('IPC accepts only the owned main frame at the exact local entry', () => {
  const frame = { url: ENTRY_URL };
  const contents = { mainFrame: frame, isDestroyed: () => false };
  const event = { sender: contents, senderFrame: frame };
  assert.equal(trustedSender(event, contents), true);
  assert.equal(trustedSender({ ...event, senderFrame: { url: ENTRY_URL } }, contents), false);
  frame.url = `${ENTRY_URL}?untrusted=true`;
  assert.equal(trustedSender(event, contents), false);
  frame.url = CONSENT_URL;
  assert.equal(trustedSender(event, contents), false);
  assert.equal(trustedSender(event, contents, CONSENT_URL), true);
});

test('API bridge has a closed route/method/body allowlist', () => {
  for (const request of [
    { method: 'GET', path: '/api/config' }, { method: 'GET', path: '/api/sessions' },
    { method: 'POST', path: '/api/sessions' },
    { method: 'GET', path: '/api/sessions/abc/turns/def/events?after=123' },
    { method: 'POST', path: '/api/sessions/abc/turns/def/ack', body: { sequence: 123 } },
    { method: 'PUT', path: '/api/config', body: { model_name: 'synthetic' } },
  ]) assert.doesNotThrow(() => validateRequest(request));
  for (const request of [
    { method: 'POST', path: '/shutdown' }, { method: 'GET', path: 'http://127.0.0.1/api/config' },
    { method: 'GET', path: '/api/config?x=y' }, { method: 'DELETE', path: '/api/sessions' },
    { method: 'POST', path: '/api/bootstrap', body: {} },
    { method: 'GET', path: '/api/sessions/../config' },
    { method: 'GET', path: '/api/sessions/abc/turns/def/events?after=1&after=2' },
    { method: 'GET', path: '/api/config', body: {} },
    { method: 'PUT', path: '/api/config', body: [] },
    { method: 'PUT', path: '/api/config', body: { value: '猫'.repeat(22000) } },
    { method: 'GET', path: '/api/config', token: 'injection' },
  ]) assert.throws(() => validateRequest(request));
});

test('connection identity cannot redirect authentication off loopback', () => {
  const descriptor = { event: 'connection', app_id: 'ai-neko', protocol_version: 1,
    host: '127.0.0.1', port: 12345, pid: 123, token: 'a'.repeat(43),
    instance_id: 'abcdefab-1234-5678-9123-abcdefabcdef', http_url: 'http://127.0.0.1:12345' };
  assert.equal(validateDescriptor(descriptor).port, 12345);
  for (const patch of [{ host: 'localhost' }, { port: 0 }, { port: '12345' },
    { http_url: 'https://example.com' }, { token: 'secret' }, { pid: -1 }, { event: 'log' }]) {
    assert.throws(() => validateDescriptor({ ...descriptor, ...patch }));
  }
});

test('source links cannot run local or privileged protocols', () => {
  assert.equal(externalURL('https://example.com/guide?q=cat#step'), 'https://example.com/guide?q=cat#step');
  for (const value of ['file:///tmp/test', 'javascript:alert(1)', 'ms-settings:foo',
    'https://user:password@example.com', 'https://example.com\n', '//example.com']) {
    assert.throws(() => externalURL(value));
  }
});

test('custom scheme exposes only contained assets and denies symlink escape', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-assets-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, 'renderer'));
  await fs.mkdir(path.join(root, 'consent'));
  await fs.writeFile(path.join(root, 'renderer/index.html'), 'safe');
  await fs.writeFile(path.join(root, 'main.cjs'), 'not public');
  await fs.writeFile(path.join(root, 'consent/preload.cjs'), 'not public');
  assert.equal(await localAsset(root, ENTRY_URL), await fs.realpath(path.join(root, 'renderer/index.html')));
  for (const url of ['ai-neko://app/main.cjs', 'ai-neko://app/renderer/%2e%2e/main.cjs',
    'ai-neko://evil/renderer/index.html', 'ai-neko://app/renderer/index.html?x=1',
    'ai-neko://app/renderer/%5c..%5cmain.cjs', 'ai-neko://app/consent/preload.cjs']) {
    await assert.rejects(localAsset(root, url));
  }
  // Symlink permission is not guaranteed on a non-admin Windows account.
  if (process.platform !== 'win32') {
    await fs.symlink('../main.cjs', path.join(root, 'renderer/escape.js'));
    await assert.rejects(localAsset(root, 'ai-neko://app/renderer/escape.js'));
  }
});
