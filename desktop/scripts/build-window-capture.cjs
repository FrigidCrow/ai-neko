'use strict';

// Explicit build step only. The capture runtime never compiles or installs code.
const fs = require('node:fs');
const path = require('node:path');
const { createHash, randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');

const SOURCE_FILE = path.resolve(__dirname, '../lib/window-capture.cs');
const EXE_NAME = 'window-capture.exe';
const MANIFEST_NAME = 'window-capture.build.json';
const MAX_OUTPUT = 64 * 1024;

function hashFile(file) {
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error('Build inputs must be regular files.');
  return createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function runChecked(run, executable, args, timeout, label) {
  const result = run(executable, args, {
    shell: false, windowsHide: true, encoding: 'utf8',
    timeout, maxBuffer: MAX_OUTPUT, stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error || result.status !== 0 || result.signal
      || Buffer.byteLength(result.stdout || '') > MAX_OUTPUT
      || Buffer.byteLength(result.stderr || '') > MAX_OUTPUT) {
    throw new Error(`${label} failed or exceeded its time/output limit.`);
  }
  return result.stdout || '';
}

function buildWindowCapture({ outputDir = path.dirname(SOURCE_FILE) } = {}, dependencies = {}) {
  const {
    platform = process.platform, arch = process.arch,
    systemRoot = process.env.SystemRoot, sourceFile = SOURCE_FILE,
    run = spawnSync, now = () => new Date(),
    resolveCompiler = (root) => path.win32.join(root, 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe'),
  } = dependencies;
  if (platform !== 'win32' || arch !== 'x64') {
    throw new Error('The window capture helper requires a native Windows x64 build.');
  }
  if (typeof systemRoot !== 'string' || !/^[A-Za-z]:[\\/]/.test(systemRoot)) {
    throw new Error('An absolute Windows SystemRoot is required.');
  }
  if (typeof outputDir !== 'string' || !outputDir.trim()) throw new Error('Invalid output directory.');
  const compiler = resolveCompiler(systemRoot);
  const compilerHash = hashFile(compiler);
  const sourceHash = hashFile(sourceFile);
  const banner = runChecked(run, compiler, ['/help'], 10000, 'Compiler version check');
  // The system compiler localizes the surrounding words, but its first banner
  // line contains the same four numeric version components in every locale.
  const firstLine = banner.split(/\r?\n/).find((line) => line.trim()) || '';
  const compilerVersion = /\b([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\b/.exec(firstLine)?.[1];
  if (!compilerVersion) throw new Error('Cannot identify the system C# compiler version.');

  const directory = path.resolve(outputDir);
  fs.mkdirSync(directory, { recursive: true });
  const executable = path.join(directory, EXE_NAME);
  const manifestFile = path.join(directory, MANIFEST_NAME);
  for (const existing of [executable, manifestFile]) {
    try {
      const info = fs.lstatSync(existing);
      if (!info.isFile() || info.isSymbolicLink()) throw new Error('Refusing a redirected build output.');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  const temporaryBase = path.join(directory, `.window-capture-build-${randomUUID()}`);
  const temporaryExe = `${temporaryBase}.exe`;
  const temporaryManifest = `${temporaryBase}.json`;
  try {
    runChecked(run, compiler, [
      '/nologo', '/target:exe', '/platform:x64', '/optimize+',
      '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
      `/out:${temporaryExe}`, sourceFile,
    ], 30000, 'Window capture helper compilation');
    const executableHash = hashFile(temporaryExe);
    const validation = runChecked(run, temporaryExe, ['--validate-only'], 10000, 'Helper validation');
    let parsed;
    try { parsed = JSON.parse(validation); } catch { throw new Error('Invalid helper validation response.'); }
    if (!parsed || parsed.validated !== true || Object.keys(parsed).length !== 1) {
      throw new Error('Invalid helper validation response.');
    }
    if (hashFile(sourceFile) !== sourceHash || hashFile(compiler) !== compilerHash
        || hashFile(temporaryExe) !== executableHash) {
      throw new Error('Build inputs or executable changed during the build.');
    }
    const manifest = {
      version: 1,
      source_sha256: sourceHash,
      executable_sha256: executableHash,
      compiler,
      compiler_version: compilerVersion,
      compiler_sha256: compilerHash,
      built_at: now().toISOString(),
    };
    fs.writeFileSync(temporaryManifest, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
    // Both artifacts are fully checked before publication. Each rename is atomic;
    // a partial publication fails the runtime's adjacent manifest/hash validation.
    fs.renameSync(temporaryExe, executable);
    fs.renameSync(temporaryManifest, manifestFile);
    return manifest;
  } finally {
    for (const ownFile of [temporaryExe, temporaryManifest]) {
      try { fs.unlinkSync(ownFile); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
}

function parseArgs(argv) {
  if (argv.length === 0) return {};
  if (argv.length === 1 && argv[0] === '--if-supported') return { ifSupported: true };
  if (argv.length === 2 && argv[0] === '--output-dir' && argv[1].trim()) {
    return { outputDir: argv[1] };
  }
  throw new Error('Usage: node scripts/build-window-capture.cjs [--output-dir DIRECTORY | --if-supported]');
}

function runBuildCommand(argv, dependencies = {}) {
  const { ifSupported, ...options } = parseArgs(argv);
  const platform = dependencies.platform ?? process.platform;
  const arch = dependencies.arch ?? process.arch;
  if (ifSupported && (platform !== 'win32' || arch !== 'x64')) {
    return { skipped: true, reason: 'Window capture helper is available only on Windows x64.' };
  }
  return buildWindowCapture(options, dependencies);
}

if (require.main === module) {
  try {
    process.stdout.write(`${JSON.stringify(runBuildCommand(process.argv.slice(2)))}\n`);
  } catch (error) {
    process.stderr.write(`window-capture build: ${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = { buildWindowCapture, parseArgs, runBuildCommand };
