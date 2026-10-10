'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../renderer/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../renderer/index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../renderer/style.css'), 'utf8');
const drain = async () => { for (let i = 0; i < 12; i++) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function server() { return { config: { model_base_url: 'http://127.0.0.1:9000', model: 'saved-model',
  search_provider: 'tavily', search_base_url: 'https://api.tavily.com', model_key_set: true, search_key_set: true,
  credential_storage: 'windows_credential_manager' }, preferences: { scale: 1, alwaysOnTop: true } }; }
async function fixture(options = {}) {
  const data = options.server || server(), nodes = new Map(), requests = [], preferenceWrites = [], storage = new Map(), windowEvents = new Map();
  let onStatus, onAction;
  class Element {
    constructor(id) {
      this.id = id; this.value = ''; this.checked = false; this.hidden = ['settings-panel', 'history-panel', 'guide-panel'].includes(id);
      this.disabled = false; this.dataset = {}; this.attributes = {}; this.listeners = new Map(); this.children = [];
      this.style = {}; this.textContent = ''; this.scrollHeight = 50; this.scrollTop = 0; this.clientHeight = 100;
      const classes = new Set(); this.classList = { add: (x) => classes.add(x), remove: (x) => classes.delete(x),
        contains: (x) => classes.has(x), toggle(x, yes) { if (yes === undefined ? !classes.has(x) : yes) classes.add(x); else classes.delete(x); } };
    }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    setAttribute(name, value) { this.attributes[name] = value; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    focus() { document.activeElement = this; }
    requestSubmit() { return this.listeners.get('submit')?.({ preventDefault() {} }); }
  }
  const el = (id) => { if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
  const document = { body: new Element('body'), visibilityState: 'visible', activeElement: null,
    getElementById: el, createElement: () => new Element(''), createTextNode: (text) => ({ textContent: text }),
    querySelectorAll: () => [], addEventListener() {} };
  const window = { addEventListener: (name, fn) => windowEvents.set(name, fn), dispatchEvent() {},
    aiNeko: {
      onStatus(fn) { onStatus = fn; }, onAction(fn) { onAction = fn; },
      getPreferences: () => options.preferences ? options.preferences() : Promise.resolve({ ...data.preferences }),
      status: async () => options.status ? options.status() : ({ state: 'ready' }),
      setPreferences: async (value) => {
        preferenceWrites.push({ ...value });
        await options.setPreferences?.(value, preferenceWrites.length);
        Object.assign(data.preferences, value);
        return { ...data.preferences };
      },
      request: async (request) => {
        requests.push(JSON.parse(JSON.stringify(request)));
        const custom = await options.request?.(request, requests);
        if (custom !== undefined) return custom;
        if (request.path === '/api/config') {
          if (request.method === 'PUT') {
            for (const key of ['model_base_url', 'model', 'search_provider', 'search_base_url']) if (key in request.body) data.config[key] = request.body[key];
            for (const kind of ['model', 'search']) {
              if (request.body['clear_' + kind + '_api_key']) data.config[kind + '_key_set'] = false;
              else if (request.body[kind + '_api_key']) data.config[kind + '_key_set'] = true;
            }
          }
          return { status: 200, body: { ...data.config } };
        }
        return { status: 200, body: request.path === '/api/sessions' ? { sessions: [] } :
          request.path === '/api/guides' ? { revision: 0, guides: [], selections: [] } : {} };
      },
    },
    aiNekoPet: { setScale() {}, refreshInteractive() {}, setState() {} },
    aiNekoCompanion: { clearKeys() {}, stopSpeech() {}, stopRecording() {} },
  };
  vm.runInNewContext(source, { window, document, URL, Event, DOMException, AbortController, performance, crypto: { randomUUID },
    localStorage: { setItem: (k, v) => storage.set(k, v), getItem: (k) => storage.get(k), removeItem: (k) => storage.delete(k) },
    setTimeout: (fn, ms) => { const timer = setTimeout(fn, ms); if (ms > 1000) timer.unref(); return timer; },
    clearTimeout, requestAnimationFrame: (fn) => setImmediate(fn) }, { filename: 'app.js' });
  await drain();
  return { el, data, requests, preferenceWrites, storage, document,
    open: () => el('open-settings').listeners.get('click')(),
    close: () => el('close-settings').listeners.get('click')(),
    save: () => el('settings-form').requestSubmit(),
    host: (value) => onStatus(value), action: (value) => onAction(value),
    edit(id, value, event = 'input') {
      const target = el(id); if (typeof value === 'boolean') target.checked = value; else target.value = value;
      target.listeners.get(event)?.({ target });
      el('settings-form').listeners.get(event)?.({ target });
    },
  };
}
const puts = (f) => f.requests.filter((r) => r.path === '/api/config' && r.method === 'PUT');

test('saving models remains in settings and a new renderer reads only persisted values', async () => {
  const data = server(), f = await fixture({ server: data }); f.open();
  f.edit('model-name', 'new-model');
  await f.save();
  assert.equal(f.el('settings-panel').hidden, false);
  assert.match(f.el('settings-message').textContent, /已保存/);
  assert.equal(f.el('save-settings').disabled, false);
  assert.equal(f.el('model-api-key').value, '');
  assert.equal(f.el('settings-form').attributes['aria-busy'], 'false');
  f.edit('model-name', 'unsaved-next');
  const reopened = await fixture({ server: data }); reopened.open();
  assert.equal(reopened.el('model-name').value, 'new-model');
  assert.equal(f.storage.size, 0, 'draft settings and Keys are never copied into localStorage');
});

test('pending save rejects repeat submit, settings reopen and ready events cannot re-enable its button', async () => {
  const wait = deferred();
  const f = await fixture({ request: (r) => r.method === 'PUT' ? wait.promise : undefined });
  f.open(); const saving = f.save(); await drain(); f.open(); f.host({ state: 'ready' }); await drain();
  assert.equal(f.el('save-settings').disabled, true);
  await f.save(); assert.equal(puts(f).length, 1);
  wait.resolve({ status: 200, body: { ...f.data.config } }); await saving;
  assert.equal(f.el('save-settings').disabled, false);
});

test('save completion preserves edits, a new Key and a new clear choice made while the request waits', async () => {
  const wait = deferred();
  const f = await fixture({ request: (r) => r.method === 'PUT' ? wait.promise : undefined });
  f.open(); f.edit('model-name', 'submitted'); f.edit('model-api-key', 'synthetic-first');
  const saving = f.save(); await drain();
  f.edit('model-name', 'newer-draft'); f.edit('model-api-key', 'synthetic-newer'); f.edit('clear-search-key', true, 'change');
  wait.resolve({ status: 200, body: { ...f.data.config, model: 'submitted' } }); await saving;
  assert.equal(f.el('model-name').value, 'newer-draft');
  assert.equal(f.el('model-api-key').value, 'synthetic-newer');
  assert.equal(f.el('clear-search-key').checked, true);
  assert.match(f.el('settings-message').textContent, /上次提交已保存.*当前修改尚未保存/);
});

test('failed save preserves drafts and exposes a retry in the same settings panel', async () => {
  let failed = true;
  const f = await fixture({ request: (r) => r.method === 'PUT' && failed ? { status: 422, body: { code: 'invalid_config' } } : undefined });
  f.open(); f.edit('model-name', 'retained'); f.edit('model-api-key', 'synthetic-retained');
  await f.save();
  assert.equal(f.el('settings-panel').hidden, false);
  assert.equal(f.el('model-api-key').value, 'synthetic-retained');
  assert.equal(f.el('model-name').value, 'retained');
  assert.match(f.el('settings-message').textContent, /未保存/);
  assert.equal(f.el('save-settings').disabled, false);
  failed = false; await f.save(); assert.equal(puts(f).length, 2);
  assert.match(f.el('settings-message').textContent, /已保存/);
});

test('reopening preserves non-Key drafts while leaving settings keeps the existing Key clearing policy', async () => {
  const f = await fixture(); f.open();
  f.edit('model-name', 'unsaved'); f.edit('model-api-key', 'synthetic-only');
  f.open(); assert.equal(f.el('model-api-key').value, 'synthetic-only', 'opening an already visible panel is not an exit');
  f.close(); assert.equal(f.el('model-api-key').value, '');
  f.open(); assert.equal(f.el('model-name').value, 'unsaved');
  assert.equal(f.el('model-api-key').value, '');
  assert.match(f.el('settings-message').textContent, /未保存/);
});

test('a delayed initial config fills untouched fields without overwriting early settings edits', async () => {
  const wait = deferred();
  const f = await fixture({ request: (r) => r.path === '/api/config' ? wait.promise : undefined });
  f.open(); f.edit('model-name', 'typed-before-connect');
  wait.resolve({ status: 200, body: { ...f.data.config } }); await drain();
  assert.equal(f.el('model-name').value, 'typed-before-connect');
  assert.equal(f.el('model-base-url').value, f.data.config.model_base_url);
  assert.equal(f.el('save-settings').disabled, false);
});

test('automatic history recovery preserves early settings drafts while explicit navigation still clears Keys', async () => {
  const wait = deferred(), sid = 'a'.repeat(32);
  const f = await fixture({ request: (r) => {
    if (r.path === '/api/config') return wait.promise;
    if (r.path === '/api/sessions') return { status: 200, body: { sessions: [{ id: sid, title: 'Synthetic history' }] } };
    if (r.path === '/api/sessions/' + sid) return { status: 200, body: { id: sid, title: 'Synthetic history', turns: [] } };
  } });
  f.open();
  f.edit('model-name', 'typed-before-history');
  f.edit('model-api-key', 'synthetic-model-draft');
  f.edit('search-api-key', 'synthetic-search-draft');
  wait.resolve({ status: 200, body: { ...f.data.config } }); await drain();
  assert.equal(f.el('settings-panel').hidden, false);
  assert.equal(f.el('model-name').value, 'typed-before-history');
  assert.equal(f.el('model-api-key').value, 'synthetic-model-draft');
  assert.equal(f.el('search-api-key').value, 'synthetic-search-draft');
  assert.equal(f.el('conversation-title').textContent, 'Synthetic history');
  assert.equal(f.document.body.dataset.chatReady, 'true');
  assert.equal(f.requests.filter((r) => r.path === '/api/sessions/' + sid).length, 1);
  assert.equal(puts(f).length, 0, 'restoring history must not save drafts');
  assert.deepEqual([...f.storage.entries()], [['ai-neko.desktop.last-session', sid]]);
  f.close();
  assert.equal(f.el('model-api-key').value, '');
  assert.equal(f.el('search-api-key').value, '');
  f.open(); f.edit('model-api-key', 'synthetic-new-draft');
  await f.el('session-list').children[0].listeners.get('click')();
  assert.equal(f.el('settings-panel').hidden, true);
  assert.equal(f.el('chat-panel').hidden, false);
  assert.equal(f.el('model-api-key').value, '');
});

test('a reconnect config response begun during save cannot overwrite the successful saved config', async () => {
  const save = deferred(), stale = deferred(); let gets = 0;
  const f = await fixture({ request: (r) => {
    if (r.path !== '/api/config') return;
    if (r.method === 'PUT') return save.promise;
    if (++gets > 1) return stale.promise;
  } });
  f.open(); f.edit('model-name', 'latest-saved'); const saving = f.save();
  f.host({ state: 'failed' }); f.host({ state: 'ready' }); await drain();
  save.resolve({ status: 200, body: { ...f.data.config, model: 'latest-saved' } }); await saving;
  stale.resolve({ status: 200, body: { ...f.data.config } }); await drain();
  f.open(); assert.equal(f.el('model-name').value, 'latest-saved');
  assert.equal(f.el('save-settings').disabled, false);
});

test('the latest ready event reconnects after an obsolete in-flight connection fails', async () => {
  const old = deferred(); let gets = 0;
  const f = await fixture({ request: (r) => r.path === '/api/config' && ++gets === 1 ? old.promise : undefined });
  f.open(); f.edit('model-name', 'local-draft');
  f.host({ state: 'failed' }); f.host({ state: 'ready' });
  old.reject(new Error('synthetic old connection failed')); await drain();
  assert.equal(gets, 2);
  assert.equal(f.el('save-settings').disabled, false);
  assert.equal(f.el('model-name').value, 'local-draft');
});

test('late appearance initialization cannot overwrite either field edited by the user', async () => {
  const wait = deferred(), f = await fixture({ preferences: () => wait.promise });
  f.edit('pet-scale', '1.2'); f.edit('pet-scale', '1.2', 'change');
  f.edit('always-on-top', false, 'change'); await drain();
  wait.resolve({ scale: 0.8, alwaysOnTop: true }); await drain();
  assert.equal(f.el('pet-scale').value, '1.2');
  assert.equal(f.el('always-on-top').checked, false);
  assert.equal(f.data.preferences.scale, 1.2);
  assert.equal(f.data.preferences.alwaysOnTop, false);
});

test('an early single appearance edit does not write an unknown field from default UI state', async () => {
  const wait = deferred(), f = await fixture({ preferences: () => wait.promise });
  f.edit('pet-scale', '1.25', 'change'); await drain();
  assert.deepEqual(f.preferenceWrites, [{ scale: 1.25 }]);
  wait.resolve({ scale: 0.8, alwaysOnTop: false }); await drain();
  assert.equal(f.el('pet-scale').value, '1.25');
  assert.equal(f.el('always-on-top').checked, false);
});

test('appearance writes serialize and coalesce the newest pending values', async () => {
  const wait = deferred(), f = await fixture({ setPreferences: (_v, call) => call === 1 ? wait.promise : undefined });
  f.edit('pet-scale', '1.05', 'change');
  f.edit('pet-scale', '1.1', 'change'); f.edit('pet-scale', '1.2', 'change'); f.edit('always-on-top', false, 'change');
  assert.equal(f.preferenceWrites.length, 1);
  wait.resolve(); await drain();
  assert.deepEqual(f.preferenceWrites, [{ scale: 1.05 }, { scale: 1.2, alwaysOnTop: false }]);
  assert.match(f.el('appearance-status').textContent, /外观已保存/);
});

test('appearance failure remains local, keeps the adjustment and retries once without an automatic loop', async () => {
  let failed = true;
  const f = await fixture({ setPreferences: () => { if (failed) throw new Error('synthetic failure'); } });
  f.open(); await f.save(); const modelMessage = f.el('settings-message').textContent;
  f.edit('pet-scale', '1.2', 'change'); await drain();
  assert.equal(f.el('pet-scale').value, '1.2');
  assert.match(f.el('appearance-status').textContent, /未保存/);
  assert.equal(f.el('settings-message').textContent, modelMessage);
  assert.equal(f.el('retry-appearance').hidden, false);
  await drain(); assert.equal(f.preferenceWrites.length, 1);
  failed = false; f.el('retry-appearance').listeners.get('click')(); f.el('retry-appearance').listeners.get('click')(); await drain();
  assert.equal(f.preferenceWrites.length, 2);
  assert.equal(f.data.preferences.scale, 1.2);
  assert.match(f.el('appearance-status').textContent, /已保存/);
});

test('a subsequent appearance change also retries the previously failed field rather than falsely declaring it saved', async () => {
  let calls = 0;
  const f = await fixture({ setPreferences: () => { if (++calls === 1) throw new Error('synthetic'); } });
  f.edit('pet-scale', '1.15', 'change'); await drain();
  f.edit('always-on-top', false, 'change'); await drain();
  assert.deepEqual(f.preferenceWrites[1], { scale: 1.15, alwaysOnTop: false });
  assert.deepEqual(f.data.preferences, { scale: 1.15, alwaysOnTop: false });
});

test('failed preferences loading cannot turn an already connected backend into a disconnection', async () => {
  const wait = deferred(), f = await fixture({ preferences: () => wait.promise });
  f.host({ state: 'ready' }); await drain(); f.open();
  assert.equal(f.el('save-settings').disabled, false);
  wait.reject(new Error('synthetic preferences read failure')); await drain();
  assert.match(f.el('appearance-status').textContent, /未能读取/);
  assert.equal(f.el('save-settings').disabled, false);
  assert.equal(f.document.body.dataset.chatReady, 'true');
});

test('the persistent settings save button retains native form submission and separate live feedback regions', () => {
  const button = html.match(/<button\b[^>]*id="save-settings"[^>]*>/g);
  assert.equal(button.length, 1);
  assert.match(button[0], /type="submit"/); assert.match(button[0], /form="settings-form"/);
  const formEnd = html.indexOf('</form>', html.indexOf('id="settings-form"'));
  const saveBar = html.indexOf('<div class="settings-savebar"');
  assert.ok(saveBar > formEnd);
  assert.match(html.slice(0, saveBar).slice(-40), /<\/div>\s*$/);
  for (const id of ['settings-message', 'appearance-status', 'memory-config-status', 'persona-status', 'memory-status', 'memory-backup-status']) {
    const nodes = html.match(new RegExp('<p[^>]*id="' + id + '"[^>]*>', 'g'));
    assert.equal(nodes?.length, 1);
    assert.match(nodes[0], /aria-live="polite"/);
  }
});

test('settings text colors meet contrast targets while settings-only rules keep a bounded scroll area', () => {
  const override = css.slice(css.indexOf('/* Settings remain readable')).replace(/#([0-9a-f]{3})\b/gi, (_match, short) => '#' + [...short].map((digit) => digit + digit).join(''));
  function luminance(hex) {
    const rgb = hex.match(/[0-9a-f]{2}/gi).map((x) => parseInt(x, 16) / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
    return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
  }
  function contrast(a, b) { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); }
  for (const [fg, bg] of [['6d5364', 'fffcfd'], ['493646', 'ffffff'], ['796573', 'ffffff'], ['ffffff', '785069'], ['96364a', 'fff7fb'], ['36644e', 'fff7fb']]) {
    assert.ok(override.includes('#' + fg) && override.includes('#' + bg));
    assert.ok(contrast(fg, bg) >= 4.5, fg + '/' + bg);
  }
  assert.match(override, /#settings-panel \.settings-scroll\{[^}]*flex:1;min-height:0/);
  assert.match(override, /#settings-panel \.settings-savebar\{[^}]*flex-shrink:0/);
  assert.match(override, /#settings-panel[^}]+font-size:14px/);
});

test('a late successful settings save cannot clear a newer backend failure notice', async () => {
  const wait = deferred(), f = await fixture({ request: (r) => r.method === 'PUT' ? wait.promise : undefined });
  f.open(); f.edit('model-name', 'saved-before-disconnect'); const saving = f.save();
  await drain(); f.host({ state: 'failed', message: 'synthetic backend unavailable' });
  const notice = f.el('notice-text').textContent;
  assert.equal(notice, 'synthetic backend unavailable');
  wait.resolve({ status: 200, body: { ...f.data.config, model: 'saved-before-disconnect' } }); await saving;
  assert.equal(f.el('save-settings').disabled, true);
  assert.equal(f.el('notice-text').textContent, notice);
  assert.equal(f.el('notice').hidden, false);
  assert.match(f.el('settings-message').textContent, /已保存/);
});

test('a late failed appearance read cannot overwrite feedback from a newer successful save', async () => {
  const wait = deferred(), f = await fixture({ preferences: () => wait.promise });
  f.edit('pet-scale', '1.2', 'change'); await drain();
  const savedMessage = f.el('appearance-status').textContent;
  assert.match(savedMessage, /已保存/);
  wait.reject(new Error('synthetic stale appearance read')); await drain();
  assert.equal(f.el('appearance-status').textContent, savedMessage);
  assert.equal(f.el('appearance-status').classList.contains('is-error'), false);
});
