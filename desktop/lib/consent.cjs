'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

async function readTerms(appPath) {
  const text = await fs.readFile(path.join(appPath, 'vendor/licenses/Live2D-END-USER.txt'), 'utf8');
  if (text.length < 1000) throw new Error('Bundled terms are incomplete');
  const hash = crypto.createHash('sha256').update(text).digest('hex');
  return { text, hash };
}

async function hasConsent(directory, hash) {
  try {
    const file = path.join(directory, 'live2d-consent.json');
    const info = await fs.lstat(file);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink > 1) return false;
    const value = JSON.parse(await fs.readFile(file, 'utf8'));
    return value.schema_version === 1 && value.terms_sha256 === hash &&
      typeof value.accepted_at === 'string';
  } catch { return false; }
}

async function acceptTerms(directory, hash) {
  const file = path.join(directory, 'live2d-consent.json');
  try {
    const info = await fs.lstat(file);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink > 1) throw new Error('Unsafe consent file');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    await fs.writeFile(temporary, JSON.stringify({ schema_version: 1, terms_sha256: hash,
      accepted_at: new Date().toISOString() }) + '\n', { flag: 'wx', mode: 0o600 });
    await fs.rename(temporary, file);
  } catch (error) { await fs.unlink(temporary).catch(() => {}); throw error; }
}

module.exports = { readTerms, hasConsent, acceptTerms };
