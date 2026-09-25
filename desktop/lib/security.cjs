'use strict';

const path = require('node:path');
const fs = require('node:fs/promises');

const ENTRY_URL = 'ai-neko://app/renderer/index.html';
const CONSENT_URL = 'ai-neko://app/consent/index.html';
const MAX_BODY_BYTES = 65536;
const CSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
  "connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; " +
  "media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

function trustedSender(event, contents, entry = ENTRY_URL) {
  return Boolean(contents && !contents.isDestroyed() && event.sender === contents &&
    event.senderFrame === contents.mainFrame && event.senderFrame?.url === entry);
}

function validateRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some((key) => !['method', 'path', 'body'].includes(key))) {
    throw new Error('Invalid request');
  }
  const { method, path: route, body } = value;
  if (!['GET', 'POST', 'PUT'].includes(method) || typeof route !== 'string' || route.length > 256) {
    throw new Error('Invalid request');
  }
  const id = '[A-Za-z0-9_-]{1,80}';
  const routes = {
    GET: [ /^\/api\/config$/, /^\/api\/sessions$/,
      new RegExp(`^/api/sessions/${id}$`),
      new RegExp(`^/api/sessions/${id}/turns/${id}/events(?:\\?after=[0-9]{1,9})?$`) ],
    PUT: [ /^\/api\/config$/ ],
    POST: [ /^\/api\/sessions$/, new RegExp(`^/api/sessions/${id}/turns$`),
      new RegExp(`^/api/sessions/${id}/turns/${id}/(?:ack|cancel)$`) ],
  };
  if (!routes[method].some((matcher) => matcher.test(route))) throw new Error('Route not allowed');
  if (body !== undefined && (!body || typeof body !== 'object' || Array.isArray(body))) {
    throw new Error('JSON object required');
  }
  if (method === 'GET' && body !== undefined) throw new Error('GET body not allowed');
  const encoded = body === undefined ? undefined : JSON.stringify(body);
  if (encoded && Buffer.byteLength(encoded) > MAX_BODY_BYTES) throw new Error('Request too large');
  return { method, path: route, encoded };
}

function validateDescriptor(value) {
  if (!value || value.event !== 'connection' || value.app_id !== 'ai-neko' ||
      value.protocol_version !== 1 || value.host !== '127.0.0.1' ||
      !Number.isInteger(value.port) || value.port < 1 || value.port > 65535 ||
      !Number.isInteger(value.pid) || value.pid < 1 ||
      typeof value.token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
      typeof value.instance_id !== 'string' || !/^[a-f0-9-]{32,36}$/i.test(value.instance_id) ||
      value.http_url !== `http://127.0.0.1:${value.port}`) throw new Error('Invalid connection');
  return { port: value.port, token: value.token, instanceId: value.instance_id, pid: value.pid };
}

function externalURL(value) {
  if (typeof value !== 'string' || value.length > 8192 || /[\x00-\x20\x7f]/.test(value)) {
    throw new Error('Invalid source link');
  }
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
    throw new Error('Only public web source links are supported');
  }
  return url.href;
}

async function localAsset(root, value) {
  const url = new URL(value);
  if (url.protocol !== 'ai-neko:' || url.hostname !== 'app' || url.username || url.password ||
      url.port || url.search || url.hash) throw new Error('Invalid asset');
  const decoded = decodeURIComponent(url.pathname);
  if (/[\\\0]/.test(decoded) || decoded.split('/').some((part) => part === '..' || part === '.')) {
    throw new Error('Invalid asset');
  }
  const parts = decoded.split('/').filter(Boolean);
  const consent = parts[0] === 'consent' && parts.length === 2 &&
    ['index.html', 'app.js', 'style.css'].includes(parts[1]);
  if ((!['renderer', 'vendor', 'assets'].includes(parts[0]) && !consent) || parts.length < 2) {
    throw new Error('Asset directory not allowed');
  }
  const candidate = path.resolve(root, ...parts);
  const real = await fs.realpath(candidate);
  const permittedRoot = await fs.realpath(path.join(root, parts[0]));
  if (!real.startsWith(permittedRoot + path.sep) || !(await fs.stat(real)).isFile()) {
    throw new Error('Asset outside application');
  }
  return real;
}

module.exports = { ENTRY_URL, CONSENT_URL, CSP, trustedSender, validateRequest, validateDescriptor, externalURL, localAsset };
