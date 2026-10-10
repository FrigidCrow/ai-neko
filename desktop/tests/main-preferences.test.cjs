'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const preferences = require('../lib/preferences.cjs');
const security = require('../lib/security.cjs');
const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
const plain = (value) => JSON.parse(JSON.stringify(value));

// Execute the unchanged main entrypoint and its registered IPC handlers, with
// synthetic Electron/connection objects and real, isolated preference files.
// No Electron process, backend, model, media device, or user data is started.
async function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-main-prefs-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const baseline = { scale: 1, alwaysOnTop: true, bounds: { x: 10, y: 20, width: 800, height: 680 } };
  preferences.savePreferences(root, baseline);
  const handlers = new Map(), timers = new Map(), sent = [], topChanges = [];
  let window, ready, failed;
  const initialized = new Promise((resolve, reject) => { ready = resolve; failed = reject; });
  class Window extends EventEmitter {
    constructor(options) {
      super(); window = this; this.options = options; this.alwaysOnTop = options.alwaysOnTop;
      this.webContents = new EventEmitter();
      this.webContents.mainFrame = { url: security.ENTRY_URL };
      this.webContents.isDestroyed = () => false;
      this.webContents.send = (channel, value) => sent.push({ channel, value: plain(value) });
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.session = Object.assign(new EventEmitter(), {
        setPermissionRequestHandler() {}, setPermissionCheckHandler() {}, setDevicePermissionHandler() {},
        webRequest: { onBeforeRequest() {} },
      });
    }
    isDestroyed() { return false; }
    getBounds() { return { ...baseline.bounds }; }
    setMenu() {}
    loadURL() { return Promise.resolve(); }
    setAlwaysOnTop(value) { this.alwaysOnTop = value; topChanges.push(value); }
  }
  const app = Object.assign(new EventEmitter(), {
    setName() {}, setAppUserModelId() {}, setPath() {}, getAppPath: () => path.join(__dirname, '..'),
    requestSingleInstanceLock: () => true, whenReady: () => Promise.resolve(), isPackaged: false,
    quit() { failed(new Error('Synthetic main quit before initialization')); },
  });
  class Tray extends EventEmitter { setToolTip() {} setContextMenu() {} }
  const modules = {
    electron: {
      app, BrowserWindow: Window, Tray, Menu: { buildFromTemplate: () => ({}) },
      nativeImage: { createFromBuffer: () => ({ setTemplateImage() {} }) },
      screen: Object.assign(new EventEmitter(), {
        getPrimaryDisplay: () => ({ id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1080 } }),
        getAllDisplays: () => [],
      }),
      protocol: { registerSchemesAsPrivileged() {}, handle() {} },
      ipcMain: { handle: (name, callback) => handlers.set(name, callback), on() {} },
      dialog: { showErrorBox: (...args) => failed(new Error(args.join(': '))) },
    },
    'node:fs/promises': require('node:fs/promises'), 'node:path': path,
    './lib/security.cjs': security, './lib/preferences.cjs': preferences,
    './lib/backend.cjs': {
      commandFor: () => ({}), initializePaths: () => ({ desktop_root: root, data_root: root }),
      OwnedBackend: class { start() { ready(); } },
    },
    './lib/vision.cjs': { VisionCapture: class { disable() {} async settled() {} async stop() {} }, microphonePermission() {} },
    './lib/window-capture.cjs': { createWindowCapture: () => ({}) },
    './lib/tray-icon.cjs': { trayIconPNG: () => Buffer.alloc(0) },
    './lib/consent.cjs': { readTerms: async () => ({ hash: 'synthetic' }), hasConsent: async () => true },
  };
  vm.runInNewContext(source, {
    require(name) { if (!(name in modules)) throw new Error('Unexpected test dependency: ' + name); return modules[name]; },
    process: { platform: process.platform },
    setTimeout(callback) { const id = Symbol(); timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); }, clearInterval() {},
  }, { filename: 'desktop/main.cjs' });
  await initialized;
  const event = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  return {
    root, baseline, window, sent, topChanges,
    // ipcRenderer.invoke rejects when the real registered main handler throws.
    invoke: (name, value) => Promise.resolve().then(() => handlers.get(name)(event, value)).then(plain),
    move() { window.emit('move'); for (const [id, callback] of [...timers]) { timers.delete(id); callback(); } },
  };
}

test('appearance IPC reports success only after the submitted preferences reach disk', async (t) => {
  const f = await fixture(t);
  assert.deepEqual(await f.invoke('ai-neko:preferences', { scale: 1.2, alwaysOnTop: false }),
    { scale: 1.2, alwaysOnTop: false });
  assert.deepEqual(preferences.loadPreferences(f.root), { ...f.baseline, scale: 1.2, alwaysOnTop: false });
  assert.deepEqual(await f.invoke('ai-neko:get-preferences'), { scale: 1.2, alwaysOnTop: false });
  assert.deepEqual(f.topChanges, [false]);
});

test('failed appearance write rejects without committing drafts and can be retried after recovery', async (t) => {
  const f = await fixture(t);
  const blocker = path.join(f.root, `preferences.json.${process.pid}.tmp`);
  const marker = 'Synthetic exclusive-open conflict, never a user file';
  fs.writeFileSync(blocker, marker, { flag: 'wx' });
  // An existing exclusive temporary destination causes a real filesystem error
  // on every platform without changing permissions, ACLs, or system settings.
  await assert.rejects(f.invoke('ai-neko:preferences', { scale: 1.05, alwaysOnTop: false }), (error) => {
    assert.equal(error.message, '外观未保存，请重试。');
    assert.equal(error.message.includes(f.root), false);
    return true;
  });
  assert.equal(fs.readFileSync(blocker, 'utf8'), marker);
  assert.deepEqual(preferences.loadPreferences(f.root), f.baseline);
  assert.deepEqual(await f.invoke('ai-neko:get-preferences'), { scale: 1, alwaysOnTop: true });
  assert.deepEqual(f.topChanges, []);
  assert.equal(f.window.alwaysOnTop, true);
  fs.unlinkSync(blocker);
  f.move();
  assert.deepEqual(preferences.loadPreferences(f.root), f.baseline,
    'background position saves must not silently persist the rejected appearance draft');
  assert.deepEqual(await f.invoke('ai-neko:preferences', { scale: 1.05, alwaysOnTop: false }),
    { scale: 1.05, alwaysOnTop: false });
  assert.deepEqual(preferences.loadPreferences(f.root), { ...f.baseline, scale: 1.05, alwaysOnTop: false });
  assert.deepEqual(await f.invoke('ai-neko:get-preferences'), { scale: 1.05, alwaysOnTop: false });
  assert.deepEqual(f.topChanges, [false]);
});

test('appearance rename EBUSY removes its temporary file and a later retry commits only after recovery', async (t) => {
  const f = await fixture(t);
  const file = path.join(f.root, 'preferences.json');
  const temporary = `${file}.${process.pid}.tmp`;
  const originalBytes = fs.readFileSync(file);
  const rename = fs.renameSync;
  let denied = 0;
  // Inject only the owned file's rename boundary. The production writer still
  // opens/writes/closes its real temporary file and performs failure cleanup.
  // This deterministic EBUSY is not a native Windows sharing-lock acceptance test.
  const failure = t.mock.method(fs, 'renameSync', function (source, destination) {
    if (destination !== file) return rename.call(this, source, destination);
    denied += 1;
    assert.equal(source, temporary);
    assert.equal(JSON.parse(fs.readFileSync(temporary, 'utf8')).scale, 1.05);
    throw Object.assign(new Error(`Synthetic EBUSY for ${file}`), { code: 'EBUSY' });
  });
  await assert.rejects(f.invoke('ai-neko:preferences', { scale: 1.05, alwaysOnTop: false }),
    (error) => error.message === '外观未保存，请重试。');
  assert.equal(denied, 1);
  assert.equal(fs.existsSync(temporary), false, 'the production writer removes its failed temporary file');
  assert.deepEqual(fs.readFileSync(file), originalBytes);
  assert.deepEqual(await f.invoke('ai-neko:get-preferences'), { scale: 1, alwaysOnTop: true });
  assert.equal(f.window.alwaysOnTop, true);
  assert.deepEqual(f.topChanges, []);
  failure.mock.restore();
  f.move();
  assert.deepEqual(preferences.loadPreferences(f.root), f.baseline,
    'a recovered background save must not commit the rejected appearance draft');
  assert.deepEqual(await f.invoke('ai-neko:preferences', { scale: 1.05, alwaysOnTop: false }),
    { scale: 1.05, alwaysOnTop: false });
  assert.deepEqual(preferences.loadPreferences(f.root), { ...f.baseline, scale: 1.05, alwaysOnTop: false });
  assert.deepEqual(await f.invoke('ai-neko:get-preferences'), { scale: 1.05, alwaysOnTop: false });
  assert.deepEqual(f.topChanges, [false]);
  assert.equal(fs.existsSync(temporary), false);
});

test('appearance IPC rejects a destroyed window before writing or applying its draft', async (t) => {
  const f = await fixture(t);
  const file = path.join(f.root, 'preferences.json');
  const originalBytes = fs.readFileSync(file);
  // Keep the sender valid so this reaches the window-lifecycle guard itself.
  f.window.isDestroyed = () => true;
  await assert.rejects(f.invoke('ai-neko:preferences', { scale: 1.05, alwaysOnTop: false }),
    (error) => error.message === '外观未保存，请重试。');
  assert.deepEqual(fs.readFileSync(file), originalBytes);
  assert.deepEqual(await f.invoke('ai-neko:get-preferences'), { scale: 1, alwaysOnTop: true });
  assert.deepEqual(f.topChanges, []);
  assert.equal(f.window.alwaysOnTop, true);
  assert.equal(fs.existsSync(`${file}.${process.pid}.tmp`), false);
});

test('background position persistence remains best effort when its file write fails', async (t) => {
  const f = await fixture(t);
  const blocker = path.join(f.root, `preferences.json.${process.pid}.tmp`);
  fs.writeFileSync(blocker, 'Synthetic conflict', { flag: 'wx' });
  assert.doesNotThrow(() => f.move());
  assert.deepEqual(preferences.loadPreferences(f.root), f.baseline);
  assert.equal(f.sent.at(-1).channel, 'ai-neko:status');
  assert.match(f.sent.at(-1).value.message, /位置暂时无法保存/);
});
