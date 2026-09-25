'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { clampBounds, validatePreferences, loadPreferences, savePreferences } = require('../lib/preferences.cjs');
const { readTerms, hasConsent, acceptTerms } = require('../lib/consent.cjs');

test('removed monitor positions return the entire pet window to a visible work area', () => {
  const primary = { x: 0, y: 0, width: 1920, height: 1040 };
  const secondary = { x: -1920, y: 0, width: 1920, height: 1080 };
  const saved = { x: -1600, y: 100 };
  assert.equal(clampBounds(saved, [primary, secondary]).x, -1600);
  assert.deepEqual(clampBounds(saved, [primary]), { x: 0, y: 100, width: 800, height: 680 });
  assert.deepEqual(clampBounds(null, [primary]), { x: 1120, y: 360, width: 800, height: 680 });
  assert.deepEqual(clampBounds({ x: 100000, y: 100000 }, [primary]),
    { x: 1120, y: 360, width: 800, height: 680 });
  assert.deepEqual(clampBounds(null, [{ x: 0, y: 0, width: 640, height: 480 }]),
    { x: 0, y: 0, width: 640, height: 480 });
});

test('desktop preferences cannot smuggle paths or nonfinite bounds into window options', () => {
  assert.deepEqual(validatePreferences({ scale: 1.2, alwaysOnTop: false }), { scale: 1.2, alwaysOnTop: false });
  for (const value of [{ scale: NaN }, { scale: 10 }, { alwaysOnTop: 'yes' }, { preload: '/tmp/evil' }]) {
    assert.throws(() => validatePreferences(value));
  }
});

test('preferences survive reopen and reject a redirected existing file', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-prefs-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  savePreferences(root, { scale: 0.8, alwaysOnTop: false, bounds: { x: 10, y: 20 } });
  assert.deepEqual(loadPreferences(root), { scale: 0.8, alwaysOnTop: false, bounds: { x: 10, y: 20 } });
  await fs.link(path.join(root, 'preferences.json'), path.join(root, 'foreign.json'));
  assert.throws(() => savePreferences(root, { scale: 1, alwaysOnTop: true }));
});

test('asset consent is bound to the bundled text hash and must be renewed after change', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-neko-consent-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, 'vendor/licenses'), { recursive: true });
  const file = path.join(root, 'vendor/licenses/Live2D-END-USER.txt');
  await fs.writeFile(file, 'Synthetic terms, not production permission.\n'.repeat(100));
  const terms = await readTerms(root);
  assert.equal(await hasConsent(root, terms.hash), false);
  await acceptTerms(root, terms.hash);
  assert.equal(await hasConsent(root, terms.hash), true);
  await fs.appendFile(file, 'Updated condition.');
  assert.equal(await hasConsent(root, (await readTerms(root)).hash), false);
});
