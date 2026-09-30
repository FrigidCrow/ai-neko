'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { validateRequest } = require('../lib/security.cjs');

const guide = `guide-${'a'.repeat(32)}`;
const revision = `revision-${'b'.repeat(32)}`;
const requestId = 'c'.repeat(32);
const backup = `guides-${'d'.repeat(32)}.sqlite`;
const allowed = [
  ['GET', '/api/guides'], ['GET', `/api/guides/${guide}`],
  ['GET', `/api/guides/${guide}?revision_id=${revision}`],
  ['PUT', '/api/guide-selection'], ['POST', '/api/guides'],
  ['POST', `/api/guides/${guide}/refresh`], ['DELETE', `/api/guides/${guide}`],
  ['GET', `/api/guide-operations/${requestId}`],
  ['POST', `/api/guide-operations/${requestId}/cancel`],
  ['GET', '/api/guide-backups'], ['POST', '/api/guide-backups'],
  ['POST', `/api/guide-backups/${backup}/restore`],
  ['DELETE', `/api/guide-backups/${backup}`],
];

test('guide bridge opens only explicit method and opaque identifier routes', () => {
  for (const [method, path] of allowed) {
    assert.doesNotThrow(() => validateRequest({ method, path }), `${method} ${path}`);
  }
  const allowedKeys = new Set(allowed.map(([method, path]) => `${method} ${path}`));
  for (const [, path] of allowed) {
    for (const method of ['GET', 'POST', 'PUT', 'DELETE']) {
      if (!allowedKeys.has(`${method} ${path}`)) {
        assert.throws(() => validateRequest({ method, path }), `${method} ${path}`);
      }
    }
  }
});

test('guide detail query must be one canonical encoded revision identifier', () => {
  const base = `/api/guides/${guide}`;
  // encodeURIComponent keeps these unreserved identifiers canonical.
  assert.equal(validateRequest({
    method: 'GET', path: `${base}?revision_id=${encodeURIComponent(revision)}`,
  }).path, `${base}?revision_id=${revision}`);
  for (const tail of [
    '?revision_id=', `?revision_id=${guide}`, `?revision_id=${revision}&scope=other`,
    `?revision_id=${revision}&revision_id=${revision}`, `?revision_id=${revision}&`,
    `?revision_id=${revision}#fragment`, `?revision_id=${revision}/../../config`,
    `?revision%5fid=${revision}`, `?revision_id=%72${revision.slice(1)}`,
    `?revision_id=${revision}%00`, `?revision_id=${revision}%0A`,
    `?revision_id=${revision}%26scope%3Dother`, `?revision_id=${revision}+`,
    `?revision_id=${revision.toUpperCase()}`, `?revision_id=${revision}\n`,
    '?scope=other', '?path=/private/synthetic', '?', '#fragment',
  ]) {
    assert.throws(() => validateRequest({ method: 'GET', path: base + tail }), tail);
  }
});

test('guide routes reject escapes, suffixes, unknown fields and oversized bodies', () => {
  for (const [method, path] of allowed) {
    for (const suffix of ['/extra', '?scope=other', '#fragment', '%00', '\n']) {
      assert.throws(() => validateRequest({ method, path: path + suffix }));
    }
  }
  for (const path of [
    '/api/guides/../config', '/api/guides/%2e%2e/config',
    `/api/guides/${guide.replace('guide-', '%67uide-')}`,
    `/api/guides/${guide.toUpperCase()}`, `/api/guides/${guide.slice(0, -1)}`,
    `/api/guides/${guide}0`, `/api/guides/${revision}`, '/api/guides/all',
    `/api/guide-operations/guide-${requestId}`, '/api/guide-operations/../config',
  ]) assert.throws(() => validateRequest({ method: 'GET', path }));
  for (const path of [
    `/api/guide-backups/memory-${'d'.repeat(32)}.sqlite`,
    `/api/guide-backups/guides-${'d'.repeat(32)}Xsqlite`,
    '/api/guide-backups/%2Fprivate%2Fsynthetic.sqlite',
    `/api/guide-backups/${backup}%2F..%2Fconfig`,
  ]) assert.throws(() => validateRequest({ method: 'DELETE', path }));
  assert.throws(() => validateRequest({ method: 'GET', path: '/api/guides', body: {} }));
  assert.throws(() => validateRequest({ method: 'POST', path: '/api/guides', body: [] }));
  assert.throws(() => validateRequest({ method: 'POST', path: '/api/guides', scope: 'other' }));
  assert.throws(() => validateRequest({
    method: 'POST', path: '/api/guides', body: { url: '猫'.repeat(22000) },
  }));
});
