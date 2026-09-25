'use strict';

// Project-owned build helper. The separately licensed Core remains unmodified.
const fs = require('node:fs/promises');
const path = require('node:path');
const https = require('node:https');
const crypto = require('node:crypto');

const repository = path.resolve(__dirname, '../..');
const sha256 = (data) => crypto.createHash('sha256').update(data).digest('hex');

function download(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { 'User-Agent': 'ai-neko-build' } }, (response) => {
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`Core download returned HTTP ${response.statusCode}; no fallback or redirect is followed.`));
        return;
      }
      let size = 0;
      const chunks = [];
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > 1048576) request.destroy(new Error('Core download exceeded the 1 MiB bound.'));
        else chunks.push(chunk);
      });
      response.on('error', reject);
      response.on('end', () => resolve(Buffer.concat(chunks)));
    });
    request.setTimeout(30000, () => request.destroy(new Error('Core download timed out.')));
    request.on('error', reject);
  });
}

async function verifyAssets(manifest, missingCoreAllowed) {
  for (const file of manifest.files) {
    const absolute = path.resolve(repository, file.path);
    if (!absolute.startsWith(repository + path.sep)) throw new Error('Manifest path escapes this repository.');
    let data;
    try { data = await fs.readFile(absolute); } catch (error) {
      if (missingCoreAllowed && file.component === 'cubism-core' && error.code === 'ENOENT') continue;
      throw error;
    }
    if (data.length !== file.bytes || sha256(data) !== file.sha256) {
      throw new Error(`Asset or license integrity mismatch: ${file.path}`);
    }
  }
}

async function main() {
  if (process.argv.slice(2).some((arg) => arg !== '--verify')) throw new Error('Usage: node desktop/vendor/fetch-core.cjs [--verify]');
  const sources = JSON.parse(await fs.readFile(path.join(__dirname, 'sources.json'), 'utf8'));
  const manifest = JSON.parse(await fs.readFile(path.join(repository, 'docs/mvp1-assets-manifest.json'), 'utf8'));
  await verifyAssets(manifest, !process.argv.includes('--verify'));
  const destination = path.join(__dirname, 'live2dcubismcore.min.js');
  try {
    const existing = await fs.readFile(destination);
    if (sha256(existing) !== sources.core.sha256 || existing.length !== sources.core.bytes) {
      throw new Error('Existing Core does not match the pinned source.');
    }
  } catch (error) {
    if (error.code !== 'ENOENT' || process.argv.includes('--verify')) throw error;
    const downloaded = await download(sources.core.url);
    if (downloaded.length !== sources.core.bytes || sha256(downloaded) !== sources.core.sha256) {
      throw new Error('Downloaded Core does not match the pinned reference source.');
    }
    const temporary = `${destination}.${process.pid}.tmp`;
    try {
      await fs.writeFile(temporary, downloaded, { flag: 'wx', mode: 0o600 });
      await fs.rename(temporary, destination);
    } finally {
      await fs.rm(temporary, { force: true });
    }
  }
  await verifyAssets(manifest, false);
  console.log(`Verified ${manifest.files.length} asset and license files; Core is the pinned N.E.K.O. reference copy.`);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
