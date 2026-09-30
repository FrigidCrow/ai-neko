'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { validateRequest } = require('../lib/security.cjs');

const session = 'a'.repeat(32);
const match = `match-${'b'.repeat(32)}`;
const base = `/api/sessions/${session}/matches`;
const allowed = [
  ['GET', base], ['GET', `${base}/${match}`], ['POST', base],
  ...['new', 'end', 'update', 'observations', 'close-observation'].map(
    (action) => ['POST', `${base}/${match}/${action}`]),
];

test('match bridge allows only the exact session-owned read and control methods', () => {
  const keys = new Set(allowed.map(([method, path]) => `${method} ${path}`));
  for (const [, path] of allowed) {
    for (const method of ['GET', 'POST', 'PUT', 'DELETE']) {
      const request = { method, path };
      if (keys.has(`${method} ${path}`)) assert.doesNotThrow(() => validateRequest(request));
      else assert.throws(() => validateRequest(request));
    }
  }
});

test('match bridge rejects noncanonical ids and all query or path extensions', () => {
  for (const [method, path] of allowed) {
    for (const suffix of ['?scope=other', '?revision=1', '?', '#fragment', '/extra', '%00', '\n']) {
      assert.throws(() => validateRequest({ method, path: path + suffix }));
    }
    for (const bad of [session.toUpperCase(), session.slice(1), `${session}0`, `x${session.slice(1)}`, '%61' + session.slice(1)]) {
      assert.throws(() => validateRequest({ method, path: path.replace(session, bad) }));
    }
    if (path.includes(match)) {
      for (const bad of [match.toUpperCase(), match.slice(1), `${match}0`, match.replace('match-', 'guide-'), '%6d' + match.slice(1)]) {
        assert.throws(() => validateRequest({ method, path: path.replace(match, bad) }));
      }
    }
  }
  for (const path of ['/api/matches', `${base}/../config`, `${base}/%2e%2e/config`, `${base}/${match}/delete`]) {
    assert.throws(() => validateRequest({ method: 'POST', path }));
  }
});

test('match bridge retains object-only 64KiB envelopes and does not widen capture privileges', () => {
  assert.throws(() => validateRequest({ method: 'GET', path: base, body: {} }));
  assert.throws(() => validateRequest({ method: 'POST', path: base, body: [] }));
  assert.throws(() => validateRequest({ method: 'POST', path: base, scope: 'other' }));
  assert.throws(() => validateRequest({ method: 'POST', path: base, body: { goal: '猫'.repeat(22000) } }));
  for (const action of ['capture', 'frame', 'screen', 'mouse', 'keyboard']) {
    assert.throws(() => validateRequest({ method: 'POST', path: `${base}/${match}/${action}` }));
  }
  const body = { text: '下一步呢', match: { match_id: match, expected_revision: 2 }, input_origin: 'voice' };
  assert.equal(validateRequest({ method: 'POST', path: `/api/sessions/${session}/turns`, body }).encoded, JSON.stringify(body));
});
