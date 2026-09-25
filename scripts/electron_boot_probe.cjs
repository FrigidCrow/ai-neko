'use strict';

// Diagnostic only: synthetic windows and disposable profiles, no model requests.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { _electron } = require('../desktop/node_modules/playwright');
const executablePath = require('../desktop/node_modules/electron');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-boot-'));
const tiny = path.join(temporary, 'tiny.cjs');
fs.writeFileSync(tiny, `const {app,BrowserWindow}=require('electron');
console.error('probe-main');
app.whenReady().then(async()=>{console.error('probe-ready');
const w=new BrowserWindow({show:true,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
await w.loadURL('data:text/html,<h1>Synthetic desktop probe</h1>'); console.error('probe-loaded');
setTimeout(()=>app.quit(),5000);});`);
const report = { platform: process.platform, electron: require('../desktop/package.json').devDependencies.electron, cases: [] };
const output = path.join(root, 'artifacts/electron-boot.json');
fs.mkdirSync(path.dirname(output), { recursive: true });
async function probe(name, entry, automated, isolated, flags = []) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (key.startsWith('AI_NEKO_') || key.startsWith('ELECTRON_') || key === 'NODE_OPTIONS') delete env[key];
  env.AI_NEKO_DATA_DIR = path.join(temporary, name, 'data'); env.PYTHONUTF8 = '1';
  if (isolated && process.platform === 'win32') {
    const profile = path.join(temporary, name, '合成 用户');
    fs.mkdirSync(path.join(profile, 'AppData/Local'), { recursive: true });
    env.USERPROFILE = profile; env.LOCALAPPDATA = path.join(profile, 'AppData/Local');
    env.APPDATA = path.join(profile, 'AppData/Roaming');
    env.PATH = [path.join(env.SYSTEMROOT || 'C:\\Windows', 'System32'), env.SYSTEMROOT || 'C:\\Windows'].join(';');
  }
  const record = { name, automated, isolated, flags }; report.cases.push(record);
  let app;
  try {
    if (automated) {
      app = await _electron.launch({ executablePath, args: [...flags, entry], cwd: root, env, timeout: 20000 });
      const page = await app.firstWindow({ timeout: 15000 });
      record.url = page.url(); record.status = 'WINDOW_CREATED';
    } else {
      const child = spawn(executablePath, [...flags, entry], { cwd: root, env, stdio: ['ignore','pipe','pipe'] });
      let log = ''; child.stdout.on('data', x => { log += x; }); child.stderr.on('data', x => { log += x; });
      record.exit = await new Promise(resolve => {
        const timer = setTimeout(() => { child.kill(); resolve('timeout'); }, 15000);
        child.once('exit', (code, signal) => { clearTimeout(timer); resolve({ code, signal }); });
      });
      record.log = log.slice(-6000); record.status = log.includes('probe-loaded') ? 'WINDOW_CREATED' : 'FAILED';
    }
  } catch (error) { record.status = 'FAILED'; record.error = String(error).slice(-6000); }
  finally { if (app) await app.close().catch(() => {}); }
  console.log(JSON.stringify(record));
  fs.writeFileSync(output, JSON.stringify(report, null, 2));
}
(async () => {
  await probe('plain-tiny', tiny, false, false);
  await probe('automated-tiny', tiny, true, false);
  await probe('isolated-tiny', tiny, true, true);
  await probe('software-tiny', tiny, true, true, ['--disable-gpu']);
  await probe('plain-source', path.join(root, 'desktop'), true, false);
  await probe('isolated-source', path.join(root, 'desktop'), true, true);
})().finally(() => fs.rmSync(temporary, { recursive: true, force: true }));
