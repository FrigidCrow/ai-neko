'use strict';

const { app, BrowserWindow, Tray, Menu, nativeImage, screen, protocol, ipcMain, shell, dialog, desktopCapturer, globalShortcut } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { ENTRY_URL, CONSENT_URL, CSP, trustedSender, validateRequest, externalURL, localAsset } = require('./lib/security.cjs');
const { clampBounds, validatePreferences, loadPreferences, savePreferences } = require('./lib/preferences.cjs');
const { commandFor, initializePaths, OwnedBackend } = require('./lib/backend.cjs');
const { VisionCapture, microphonePermission } = require('./lib/vision.cjs');
const vision = new VisionCapture((options) => desktopCapturer.getSources(options));
let microphoneUntil = 0;
const { trayIconPNG } = require('./lib/tray-icon.cjs');
const { readTerms, hasConsent, acceptTerms } = require('./lib/consent.cjs');

app.setName('ai-neko');
app.setAppUserModelId('io.frigidcrow.ai-neko');
protocol.registerSchemesAsPrivileged([{ scheme: 'ai-neko', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: true,
} }]);

let mainWindow = null;
let consentWindow = null;
let tray = null;
let backend = null;
let paths = null;
let preferences = null;
let status = { state: 'starting', message: '正在准备本地聊天服务…' };
let quitting = false;
let allowedToQuit = false;
let dragTimer = null;
let dragging = false;
let interactive = true;
let persistTimer = null;

function send(channel, value) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, value);
}

function setStatus(value) { status = value; send('ai-neko:status', value); }

function publicPreferences() { return { scale: preferences.scale, alwaysOnTop: preferences.alwaysOnTop }; }

function workAreas() {
  const primary = screen.getPrimaryDisplay();
  return [primary, ...screen.getAllDisplays().filter((value) => value.id !== primary.id)]
    .map((value) => value.workArea);
}

function persist() {
  if (!preferences || !paths || !mainWindow || mainWindow.isDestroyed()) return;
  preferences.bounds = mainWindow.getBounds();
  try { savePreferences(paths.desktop_root, preferences); }
  catch { setStatus({ ...status, message: '桌宠位置暂时无法保存，请检查数据目录权限。' }); }
}

function setInteractive(value) {
  if (!mainWindow || mainWindow.isDestroyed() || interactive === value) return;
  interactive = value;
  mainWindow.setIgnoreMouseEvents(!value, { forward: true });
}

function stopDrag() {
  clearInterval(dragTimer);
  dragTimer = null;
  dragging = false;
  persist();
}

function beginDrag() {
  if (dragging || !mainWindow) return;
  dragging = true;
  setInteractive(true);
  const origin = screen.getCursorScreenPoint();
  const initial = mainWindow.getBounds();
  const started = Date.now();
  dragTimer = setInterval(() => {
    if (!mainWindow || mainWindow.isDestroyed() || Date.now() - started > 120000) return stopDrag();
    const pointer = screen.getCursorScreenPoint();
    const next = clampBounds({ x: initial.x + pointer.x - origin.x, y: initial.y + pointer.y - origin.y }, workAreas());
    mainWindow.setBounds(next);
  }, 16);
}

function reveal(action) {
  if (consentWindow && !consentWindow.isDestroyed()) {
    consentWindow.show(); consentWindow.focus(); return;
  }
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.setBounds(clampBounds(mainWindow.getBounds(), workAreas()));
  mainWindow.show();
  mainWindow.focus();
  if (action) send('ai-neko:action', action);
}

function resetPosition() {
  if (!mainWindow) return;
  mainWindow.setBounds(clampBounds(null, workAreas()));
  reveal();
  persist();
}

function menu() {
  return Menu.buildFromTemplate([
    { label: mainWindow?.isVisible() ? '隐藏桌宠' : '显示桌宠', click: () => {
      if (mainWindow?.isVisible()) mainWindow.hide(); else reveal();
    } },
    { label: '和猫娘聊天', click: () => reveal('show-chat') },
    { label: '恢复到主屏', click: resetPosition },
    { label: '设置', click: () => reveal('settings') },
    { type: 'separator' },
    { label: '退出 ai-neko', click: () => app.quit() },
  ]);
}

function installIPC() {
  const requireSender = (event) => {
    if (!trustedSender(event, mainWindow?.webContents)) throw new Error('Untrusted application frame');
  };
  ipcMain.handle('ai-neko:request', async (event, value) => {
    requireSender(event);
    const request = validateRequest(value);
    return backend ? backend.request(request) : { status: 503, body: { detail: '本地服务尚未准备好。' } };
  });
  ipcMain.handle('ai-neko:vision-list', async (event) => { requireSender(event); return vision.list(); });
  ipcMain.handle('ai-neko:vision-select', async (event, id) => { requireSender(event); return vision.select(id); });
  ipcMain.handle('ai-neko:vision-capture', async (event) => { requireSender(event); return vision.capture(); });
  ipcMain.handle('ai-neko:vision-stop', (event) => { requireSender(event); return vision.disable(); });
  ipcMain.handle('ai-neko:microphone', (event, enabled) => {
    requireSender(event);
    microphoneUntil = enabled === true ? Date.now() + 15000 : 0;
    return enabled === true;
  });
  ipcMain.handle('ai-neko:voice-shortcut', (event, enabled) => {
    requireSender(event);
    globalShortcut.unregister('CommandOrControl+Shift+Space');
    if (enabled !== true) return false;
    return globalShortcut.register('CommandOrControl+Shift+Space', () => send('ai-neko:action', 'voice-toggle'));
  });
  ipcMain.handle('ai-neko:status', (event) => { requireSender(event); return status; });
  ipcMain.handle('ai-neko:get-preferences', (event) => { requireSender(event); return publicPreferences(); });
  ipcMain.handle('ai-neko:preferences', (event, value) => {
    requireSender(event);
    Object.assign(preferences, validatePreferences(value));
    mainWindow.setAlwaysOnTop(preferences.alwaysOnTop);
    persist();
    return publicPreferences();
  });
  ipcMain.handle('ai-neko:external', async (event, value) => {
    requireSender(event);
    await shell.openExternal(externalURL(value));
    return true;
  });
  ipcMain.on('ai-neko:interactive', (event, value) => {
    if (trustedSender(event, mainWindow?.webContents) && typeof value === 'boolean' && !dragging) setInteractive(value);
  });
  ipcMain.on('ai-neko:drag', (event, phase) => {
    if (!trustedSender(event, mainWindow?.webContents)) return;
    if (phase === 'start') beginDrag();
    else if (phase === 'end') stopDrag();
  });
  ipcMain.on('ai-neko:menu', (event) => {
    if (trustedSender(event, mainWindow?.webContents)) menu().popup({ window: mainWindow });
  });
  ipcMain.on('ai-neko:quit', (event) => {
    if (trustedSender(event, mainWindow?.webContents)) app.quit();
  });
}

function secureContents(contents) {
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('will-navigate', (event) => event.preventDefault());
  contents.on('will-redirect', (event) => event.preventDefault());
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.session.setPermissionRequestHandler((webContents, permission, callback, details) => {
    callback(microphonePermission(webContents, mainWindow?.webContents, permission, details, microphoneUntil));
  });
  contents.session.setPermissionCheckHandler((webContents, permission, _origin, details) =>
    microphonePermission(webContents, mainWindow?.webContents, permission, details, microphoneUntil));
  contents.session.setDevicePermissionHandler(() => false);
  contents.session.on('will-download', (event) => event.preventDefault());
  contents.session.webRequest.onBeforeRequest((details, callback) => {
    const permitted = details.url.startsWith('ai-neko://app/') || details.url.startsWith('devtools://');
    callback({ cancel: !permitted });
  });
}

async function installProtocol() {
  const mime = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.png': 'image/png',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml',
    '.woff': 'font/woff', '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8',
  };
  protocol.handle('ai-neko', async (request) => {
    try {
      if (!['GET', 'HEAD'].includes(request.method)) return new Response('', { status: 405 });
      const file = await localAsset(app.getAppPath(), request.url);
      const bytes = request.method === 'HEAD' ? null : await fs.readFile(file);
      return new Response(bytes, { headers: {
        'Content-Type': mime[path.extname(file).toLowerCase()] || 'application/octet-stream',
        'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
      } });
    } catch { return new Response('Not found', { status: 404 }); }
  });
}

async function createPetWindow() {
  preferences = loadPreferences(paths.desktop_root);
  const bounds = clampBounds(preferences.bounds, workAreas());
  mainWindow = new BrowserWindow({
    ...bounds, title: 'ai-neko · 猫娘桌宠', show: false,
    frame: false, transparent: true, backgroundColor: '#00000000',
    resizable: false, maximizable: false, fullscreenable: false,
    alwaysOnTop: preferences.alwaysOnTop, skipTaskbar: true, hasShadow: false,
    webPreferences: { preload: path.join(app.getAppPath(), 'preload.cjs'),
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      nodeIntegrationInWorker: false, webSecurity: true,
      allowRunningInsecureContent: false, webviewTag: false, backgroundThrottling: false,
      devTools: !app.isPackaged,
    },
  });
  secureContents(mainWindow.webContents);
  mainWindow.setMenu(null);
  mainWindow.once('ready-to-show', () => mainWindow.showInactive());
  mainWindow.on('close', (event) => {
    if (!quitting) { event.preventDefault(); stopDrag(); mainWindow.hide(); }
  });
  mainWindow.on('move', () => { clearTimeout(persistTimer); persistTimer = setTimeout(persist, 300); });
  mainWindow.webContents.on('render-process-gone', () => { vision.disable(); microphoneUntil = 0; stopDrag(); app.quit(); });
  mainWindow.webContents.on('did-start-navigation', () => { vision.disable(); microphoneUntil = 0; globalShortcut.unregister('CommandOrControl+Shift+Space'); });
  installIPC();
  const icon = nativeImage.createFromBuffer(trayIconPNG());
  if (process.platform === 'darwin') icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip('ai-neko · 猫娘桌宠');
  tray.on('click', () => reveal());
  tray.on('right-click', () => tray.popUpContextMenu(menu()));
  tray.on('double-click', () => reveal('show-chat'));
  // Linux trays often expose only context menus, while Windows/macOS refresh on
  // right-click. Keep both paths usable.
  if (process.platform === 'linux') tray.setContextMenu(menu());
  for (const event of ['display-removed', 'display-metrics-changed', 'display-added']) {
    screen.on(event, () => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.setBounds(clampBounds(mainWindow.getBounds(), workAreas()));
        persist();
      }
    });
  }
  await mainWindow.loadURL(ENTRY_URL);
}

async function confirmTerms() {
  const terms = await readTerms(app.getAppPath());
  if (await hasConsent(paths.desktop_root, terms.hash)) return true;
  return new Promise((resolve, reject) => {
    let accepted = false;
    let saving = false;
    consentWindow = new BrowserWindow({ width: 820, height: 720,
      title: '猫娘素材与 Live2D 使用条款', show: false,
      webPreferences: { preload: path.join(app.getAppPath(), 'consent/preload.cjs'),
        contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true,
        devTools: !app.isPackaged, webviewTag: false,
      },
    });
    secureContents(consentWindow.webContents);
    consentWindow.setMenu(null);
    const requireConsentSender = (event) => {
      if (!trustedSender(event, consentWindow?.webContents, CONSENT_URL)) throw new Error('Untrusted consent frame');
    };
    ipcMain.handle('ai-neko:consent-terms', (event) => { requireConsentSender(event); return terms.text; });
    ipcMain.handle('ai-neko:consent-accept', async (event) => {
      requireConsentSender(event);
      if (saving) return;
      saving = true;
      try {
        await acceptTerms(paths.desktop_root, terms.hash);
        accepted = true;
        // Resolve the invoke before replacing its document.
        setImmediate(() => consentWindow?.close());
        return true;
      } catch { saving = false; throw new Error('无法保存使用条款同意记录。'); }
    });
    ipcMain.on('ai-neko:consent-decline', (event) => {
      if (trustedSender(event, consentWindow?.webContents, CONSENT_URL)) consentWindow.close();
    });
    consentWindow.once('ready-to-show', () => consentWindow.show());
    consentWindow.once('closed', () => {
      consentWindow = null;
      ipcMain.removeHandler('ai-neko:consent-terms');
      ipcMain.removeHandler('ai-neko:consent-accept');
      ipcMain.removeAllListeners('ai-neko:consent-decline');
      resolve(accepted);
    });
    consentWindow.loadURL(CONSENT_URL).catch(reject);
  });
}

async function start() {
  const command = commandFor({ packaged: app.isPackaged, resourcesPath: process.resourcesPath, appPath: app.getAppPath() });
  // Do not yield before setting userData/sessionData: Electron may otherwise
  // finish ready and initialize a default profile while the path child runs.
  paths = initializePaths(command);
  app.setPath('userData', paths.desktop_root);
  app.setPath('sessionData', paths.desktop_root);
  if (!app.requestSingleInstanceLock()) { app.quit(); return; }
  app.on('second-instance', () => reveal('show-chat'));
  await app.whenReady();
  if (process.platform === 'darwin') app.dock?.hide();
  await installProtocol();
  if (!(await confirmTerms())) { app.quit(); return; }
  await createPetWindow();
  backend = new OwnedBackend(command, paths.data_root, setStatus);
  backend.start();
}

app.on('window-all-closed', () => { if (!quitting && mainWindow) app.quit(); });
app.on('activate', () => reveal());
app.on('before-quit', (event) => {
  if (allowedToQuit) return;
  event.preventDefault();
  if (quitting) return;
  quitting = true;
  stopDrag();
  clearTimeout(persistTimer);
  persist();
  void (async () => {
    await backend?.stop();
    allowedToQuit = true;
    tray?.destroy();
    app.quit();
  })();
});

void start().catch(async () => {
  // Never echo caught backend errors or user configuration into logs/dialogs.
  await app.whenReady();
  dialog.showErrorBox('ai-neko 无法启动', '应用文件或独立数据目录未通过检查。请确认解压完整，数据目录可写，且没有其他 ai-neko 正在使用它。');
  app.quit();
});

app.on('will-quit', () => { vision.disable(); microphoneUntil = 0; if (app.isReady()) globalShortcut.unregisterAll(); });
