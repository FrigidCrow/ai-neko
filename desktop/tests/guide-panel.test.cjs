'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// This is a VM test of the production panel, not Electron, HTTP, WebAudio or a
// browser layout test. The coordinator's completed/rejected result is a seam;
// actual transport cancellation remains covered by its separate acceptance.
const source = fs.readFileSync(path.join(__dirname, '../renderer/guide-panel.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../renderer/index.html'), 'utf8');
const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const drain = async () => { for (let n = 0; n < 8; n++) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const DOC = {
  guide_id: 'guide-' + 'a'.repeat(32), revision_id: 'revision-' + 'b'.repeat(32),
  title: '合成攻略', game: '合成雾棋', platform: 'PC', mode: '排位',
  game_version: '1.0', version_basis: '原文明示 1.0', completeness: 'full',
  original_url: 'https://guides.example.test/panel', url: 'https://guides.example.test/panel',
  text: '灯芯负责照亮码头。', last_checked_at: '2026-09-27T00:00:00Z',
};
const NEXT = { ...DOC, revision_id: 'revision-' + 'c'.repeat(32), game_version: '2.0', text: '新正文需明确切换。' };
const SELECTION = Object.fromEntries(['guide_id', 'revision_id', 'game', 'platform', 'mode'].map((key) => [key, DOC[key]]));
const SNAPSHOT = { backup_id: 'guides-' + 'd'.repeat(32) + '.sqlite', created_at: 1790467200, documents: 1, selections: 1 };

class Element {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.listeners = new Map();
    this.dataset = {}; this.attributes = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.open = false; this.isConnected = true; this.valid = true; this._text = ''; this.className = '';
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        const add = force === undefined ? !names.has(name) : force;
        if (add) names.add(name); else names.delete(name);
        this.className = [...names].join(' '); return add;
      },
    };
  }
  get textContent() { return this._text + this.children.map((item) => item.textContent || '').join(''); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  append(...items) { for (const item of items) { item.isConnected = true; this.children.push(item); } }
  replaceChildren(...items) { for (const child of this.children) child.isConnected = false; this.children = []; this._text = ''; this.append(...items); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) {
    const list = this.listeners.get(name) || []; list.push(callback); this.listeners.set(name, list);
  }
  async fire(name) {
    if ((name === 'click' || name === 'submit') && this.disabled) throw new Error('Attempted disabled UI action: ' + this.id);
    for (const callback of this.listeners.get(name) || []) await callback({ target: this, preventDefault() {} });
  }
  reportValidity() { return this.valid; }
  scrollIntoView() {} focus() {}
}

function descendants(element) { return [element, ...element.children.flatMap(descendants)]; }
function classItems(element, name) { return descendants(element).filter((item) => item.classList?.contains(name)); }

function harness({ api, runControl, onReview, catalog } = {}) {
  const ids = new Map(); const commands = [], reads = [], reviews = [], panels = [];
  // Take IDs/tags from the actual HTML so missing production fields cannot be
  // silently supplied by a getElementById stub.
  for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*?)\bid="([^"]+)"([^>]*)>/g)) {
    const element = new Element(match[1], match[3]);
    const attributes = match[2] + match[4];
    element.hidden = /\bhidden\b/.test(attributes);
    element.className = /class="([^"]*)"/.exec(attributes)?.[1] || '';
    ids.set(match[3], element);
  }
  const el = (id) => {
    assert.ok(ids.has(id), 'production HTML must declare #' + id); return ids.get(id);
  };
  const window = { addEventListener() {} };
  const document = {
    visibilityState: 'visible', getElementById: el, createElement: (tag) => new Element(tag),
    querySelectorAll(selector) {
      assert.equal(selector, '#guide-panel button, .source-adopt');
      return [...new Set([...ids.values()].flatMap(descendants))].filter((item) => item.tagName === 'BUTTON' || item.classList.contains('source-adopt'));
    },
  };
  vm.runInNewContext(source, { window, document, URL, DOMException, setInterval: () => 1, clearInterval() {} }, { filename: 'desktop/renderer/guide-panel.js' });
  let panel;
  panel = window.aiNekoGuidePanel.create({
    api: async (route) => { reads.push(route); return api ? api(route) : { guide: clone(DOC), revision: 4 }; },
    runControl: async (command) => { commands.push(clone(command)); return runControl ? runControl(command, panel) : { ...clone(DOC) }; },
    onReview: async (id, question) => { reviews.push({ id, question }); return onReview?.(id, question, panel); },
    showPanel: (name) => { panels.push(name); el('guide-panel').hidden = name !== 'guides'; },
    friendlyError: (error) => error.publicMessage || (error.name === 'AbortError' ? '操作已取消。' : error.message || '合成失败'),
  });
  panel.render({ connected: true, sessionId: 'e'.repeat(32),
    guideCatalog: catalog || { revision: 3, guides: [clone(DOC)], selections: [] },
    matchCatalog: { revision: 0, current: null, matches: [] } });
  const setSource = (values = {}) => {
    for (const [name, value] of Object.entries({ url: DOC.url, game: DOC.game, platform: DOC.platform, mode: DOC.mode, version: '', 'version-basis': '', ...values })) el('guide-' + name).value = value;
  };
  return { panel, el, commands, reads, reviews, panels, setSource,
    buttons: (id, name) => classItems(el(id), name),
    async click(id) { await el(id).fire('click'); await drain(); },
    async submit(id) { await el(id).fire('submit'); await drain(); },
    async backups(open = true) { el('guide-backups').open = open; await el('guide-backups').fire('toggle'); await drain(); },
  };
}

test('public URL save commits only fetch and leaves adoption explicit', async () => {
  const app = harness(); app.setSource({ game: '', platform: '', mode: '' });
  await app.submit('guide-save-form');
  assert.deepEqual(app.commands, [{ kind: 'guide-fetch', fields: { url: DOC.url, game: '', platform: '', mode: '' }, expectedRevision: 3 }]);
  assert.match(app.el('guide-status').textContent, /尚未采用/);
  assert.equal(app.panel.selectedSource(), null);
  for (const result of [{ status: 'snippet' }, { status: 'error' }, { guide_id: DOC.guide_id }]) {
    const app = harness({ runControl: () => result }); app.setSource(); await app.submit('guide-save-form');
    assert.match(app.el('guide-status').textContent, /尚未保存成功/);
    assert.equal(app.el('guide-status').classList.contains('is-error'), true);
    assert.deepEqual(app.commands.map((item) => item.kind), ['guide-fetch']);
  }
});

test('save-and-adopt waits for committed IDs and exact nonempty body, surviving its own catalog refresh', async () => {
  const fetched = deferred(), body = deferred();
  const app = harness({
    api: (route) => { assert.equal(route, `/api/guides/${DOC.guide_id}?revision_id=${DOC.revision_id}`); return body.promise; },
    runControl: async (command, panel) => {
      if (command.kind === 'guide-fetch') {
        await fetched.promise;
        panel.render({ guideCatalog: { revision: 4, guides: [clone(DOC)], selections: [] } });
        return { result: clone(DOC) };
      }
      assert.equal(command.kind, 'guide-select'); return { selection: clone(SELECTION) };
    },
  });
  app.setSource({ version: '1.0', 'version-basis': '用户明确确认版本' });
  await app.click('guide-save-adopt');
  assert.equal(app.commands.length, 0, 'opening adoption form cannot fetch or adopt');
  const submitting = app.el('guide-selection-form').fire('submit'); await drain();
  assert.deepEqual(app.commands.map((item) => item.kind), ['guide-fetch']);
  assert.equal(app.el('guide-panel').getAttribute('aria-busy'), 'true');
  assert.equal(app.el('guide-save').disabled, true);
  assert.equal(app.el('cancel-guide-operation').disabled, false, 'coordinator cancel stays operable while waiting');
  fetched.resolve(); await drain();
  assert.equal(app.commands.length, 1, 'saved IDs alone cannot justify adoption');
  body.resolve({ revision: 9, guide: clone(DOC) }); await submitting; await drain();
  assert.deepEqual(app.commands[1], { kind: 'guide-select', fields: clone(SELECTION), expectedRevision: 9 });
  assert.deepEqual(clone(app.panel.selectedSource()), clone(SELECTION));
  assert.equal(app.el('guide-adoption').hidden, true);
  assert.match(app.el('guide-status').textContent, /已采用/);
});

test('save-and-adopt rejects failed fetch, incomplete IDs, empty body and unavailable body', async () => {
  for (const failure of ['fetch-failed', 'missing-IDs', 'empty-body', 'body-unavailable']) {
    const app = harness({
      runControl: () => {
        if (failure === 'fetch-failed') throw new Error('合成正文读取失败');
        return failure === 'missing-IDs' ? { status: 'snippet' } : clone(DOC);
      },
      api: () => {
        if (failure === 'body-unavailable') throw Object.assign(new Error('missing'), { status: 404 });
        return { revision: 4, guide: { ...clone(DOC), text: '  ' } };
      },
    });
    app.setSource(); await app.click('guide-save-adopt'); await app.submit('guide-selection-form');
    assert.deepEqual(app.commands.map((item) => item.kind), ['guide-fetch']);
    assert.equal(app.el('guide-adoption').hidden, false); assert.equal(app.panel.selectedSource(), null);
    assert.equal(app.el('guide-status').classList.contains('is-error'), true);
    assert.equal(app.el('guide-panel').getAttribute('aria-busy'), 'false');
  }
});

test('changed, unchanged and failed refresh never switch the pinned adoption', async () => {
  for (const outcome of ['new-body', 'not-modified', 'failed']) {
    const original = { revision: 4, guides: [clone(DOC)], selections: [clone(SELECTION)] };
    const app = harness({ catalog: original, api: () => ({ revision: 4, guide: clone(DOC) }), runControl: (command, panel) => {
      assert.equal(command.kind, 'guide-refresh');
      if (outcome === 'failed') throw new Error('合成核查失败');
      if (outcome === 'new-body') panel.render({ guideCatalog: { ...original, guides: [clone(NEXT)] } });
      return { not_modified: outcome === 'not-modified' };
    } });
    await app.buttons('guide-library-list', 'guide-refresh')[0].fire('click'); await drain();
    assert.deepEqual(app.commands, [{ kind: 'guide-refresh', fields: {}, expectedRevision: 4, targetId: DOC.guide_id }]);
    assert.match(app.el('current-guide-label').textContent, /游戏版本 1\.0/);
    assert.doesNotMatch(app.el('current-guide-label').textContent, /2\.0/);
    assert.equal(app.buttons('guide-selection-list', 'guide-unadopt').length, 1);
    if (outcome === 'new-body') {
      assert.equal(app.buttons('guide-library-list', 'guide-switch').length, 1);
      assert.match(app.el('guide-library-list').textContent, /当前采用版本保持不变/);
    } else if (outcome === 'not-modified') assert.match(app.el('guide-status').textContent, /原文未变/);
    else assert.match(app.el('guide-status').textContent, /合成核查失败/);
  }
});

test('catalog revision immediately removes old snapshot buttons and rejects stale pending list response', async () => {
  let reads = 0; const stale = deferred(), fresh = deferred();
  const app = harness({ api: (route) => {
    assert.equal(route, '/api/guide-backups'); reads++;
    return reads === 1 ? { backups: [clone(SNAPSHOT)] } : reads === 2 ? stale.promise : fresh.promise;
  } });
  await app.backups(); assert.equal(app.buttons('guide-backup-list', 'guide-backup-restore').length, 1);
  await app.click('refresh-guide-backups');
  app.panel.render({ guideCatalog: { revision: 5, guides: [], selections: [] } });
  assert.equal(app.buttons('guide-backup-list', 'guide-backup-restore').length, 0);
  stale.resolve({ backups: [clone(SNAPSHOT)] }); await drain();
  assert.equal(app.buttons('guide-backup-list', 'guide-backup-restore').length, 0, 'obsolete list cannot restore deleted target');
  fresh.resolve({ backups: [] }); await drain();
  assert.match(app.el('guide-backup-list').textContent, /还没有/); assert.equal(reads, 3);
  let closedReads = 0;
  const closed = harness({ api: () => ({ backups: ++closedReads === 1 ? [clone(SNAPSHOT)] : [] }) });
  await closed.backups(); await closed.backups(false);
  closed.panel.render({ guideCatalog: { revision: 6, guides: [], selections: [] } });
  assert.equal(closed.el('guide-backup-list').children.length, 0); assert.equal(closedReads, 1);
  await closed.backups(); assert.equal(closedReads, 2); assert.match(closed.el('guide-backup-list').textContent, /还没有/);
});

test('successful task without default message clears busy status and preserves a supplied status', async () => {
  for (const customMessage of [null, '复盘已交给对话窗口。']) {
    const completed = deferred();
    const app = harness({ onReview: async (_id, _question, panel) => { await completed.promise; if (customMessage) panel.status(customMessage); } });
    const match = { match_id: 'match-' + 'f'.repeat(32), game: '合成雾棋', platform: 'PC', mode: '排位', status: 'ended' };
    app.panel.render({ matchCatalog: { revision: 7, current: null, matches: [match] } });
    await app.buttons('match-list', 'match-review')[0].fire('click'); await drain();
    assert.equal(app.el('guide-status').textContent, '正在处理…');
    completed.resolve(); await drain();
    assert.equal(app.el('guide-status').textContent, customMessage || '');
    assert.equal(app.el('guide-panel').getAttribute('aria-busy'), 'false');
    assert.equal(app.reviews[0].id, match.match_id); assert.equal(app.commands.length, 0);
  }
});
