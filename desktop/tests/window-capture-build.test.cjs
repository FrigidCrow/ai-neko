'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { buildWindowCapture, parseArgs, runBuildCommand } = require('../scripts/build-window-capture.cjs');

const digest = (data) => createHash('sha256').update(data).digest('hex');
const success = (stdout = '') => ({ status: 0, signal: null, stdout, stderr: '' });

function fixture(t, override = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-neko-capture-build-'));
  t.after(() => {
    const resolved = path.resolve(directory);
    assert.ok(resolved.startsWith(`${path.resolve(os.tmpdir())}${path.sep}`));
    assert.ok(path.basename(resolved).startsWith('ai-neko-capture-build-'));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  const sourceFile = path.join(directory, 'window-capture.cs');
  const compiler = path.join(directory, 'fake-csc.exe');
  const outputDir = path.join(directory, 'output');
  fs.writeFileSync(sourceFile, '// synthetic source; never compiled');
  fs.writeFileSync(compiler, 'synthetic compiler; never executed');
  fs.mkdirSync(outputDir);
  const exe = path.join(outputDir, 'window-capture.exe');
  const manifestFile = path.join(outputDir, 'window-capture.build.json');
  fs.writeFileSync(exe, 'old executable');
  fs.writeFileSync(manifestFile, 'old manifest');
  const calls = [];
  const dependencies = {
    platform: 'win32', arch: 'x64', systemRoot: 'C:\\Windows', sourceFile,
    resolveCompiler: () => compiler,
    now: () => new Date('2026-10-09T20:00:00.000Z'),
    run(executable, args, options) {
      calls.push({ executable, args, options });
      if (args[0] === '/help') return success('Microsoft (R) Visual C# Compiler version 4.8.9232.0\n');
      if (executable === compiler) {
        fs.writeFileSync(args.find((arg) => arg.startsWith('/out:')).slice(5), 'fresh synthetic executable');
        return override.compile?.({ sourceFile, compiler, outputDir }) ?? success();
      }
      return override.validate?.({ executable, sourceFile }) ?? success('{"validated":true}\n');
    },
    ...override.dependencies,
  };
  return {
    sourceFile, compiler, exe, manifestFile, calls, outputDir, dependencies,
    build: () => buildWindowCapture({ outputDir }, dependencies),
    unchanged() {
      assert.equal(fs.readFileSync(exe, 'utf8'), 'old executable');
      assert.equal(fs.readFileSync(manifestFile, 'utf8'), 'old manifest');
      assert.deepEqual(fs.readdirSync(outputDir).sort(), ['window-capture.build.json', 'window-capture.exe']);
    },
  };
}

test('explicit build binds source, executable and compiler only after a no-capture validation', (t) => {
  const f = fixture(t);
  const manifest = f.build();
  assert.deepEqual(JSON.parse(fs.readFileSync(f.manifestFile, 'utf8')), manifest);
  assert.equal(manifest.version, 1);
  assert.equal(manifest.source_sha256, digest(fs.readFileSync(f.sourceFile)));
  assert.equal(manifest.executable_sha256, digest('fresh synthetic executable'));
  assert.equal(manifest.compiler_sha256, digest(fs.readFileSync(f.compiler)));
  assert.equal(manifest.compiler, f.compiler);
  assert.equal(manifest.compiler_version, '4.8.9232.0');
  assert.equal(manifest.built_at, '2026-10-09T20:00:00.000Z');
  assert.equal(f.calls.length, 3);
  assert.deepEqual(f.calls[0].args, ['/help']);
  const compile = f.calls[1];
  assert.deepEqual(compile.args.slice(0, 6), [
    '/nologo', '/target:exe', '/platform:x64', '/optimize+',
    '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
  ]);
  assert.equal(compile.args.at(-1), f.sourceFile);
  const temporary = compile.args[6].slice(5);
  assert.equal(path.dirname(temporary), f.outputDir);
  assert.match(path.basename(temporary), /^\.window-capture-build-[a-f0-9-]+\.exe$/);
  assert.equal(f.calls[2].executable, temporary);
  assert.deepEqual(f.calls[2].args, ['--validate-only']);
  for (const call of f.calls) {
    assert.equal(call.options.shell, false);
    assert.equal(call.options.windowsHide, true);
    assert.equal(call.options.maxBuffer, 65536);
    assert.deepEqual(call.options.stdio, ['ignore', 'pipe', 'pipe']);
    assert.ok(call.options.timeout > 0 && call.options.timeout <= 30000);
  }
  assert.deepEqual(fs.readdirSync(f.outputDir).sort(), ['window-capture.build.json', 'window-capture.exe']);
});

for (const [name, result] of [
  ['nonzero compiler exit', { ...success(), status: 1 }],
  ['compiler timeout', { ...success(), error: { code: 'ETIMEDOUT' } }],
  ['compiler signal', { ...success(), signal: 'SIGTERM' }],
  ['excessive compiler output', success('x'.repeat(65537))],
]) test(`${name} cannot replace previously published outputs`, (t) => {
  const f = fixture(t, { compile: () => result });
  assert.throws(f.build, /compilation failed or exceeded/);
  assert.equal(f.calls.length, 2);
  f.unchanged();
});

for (const response of ['invalid JSON', '{}', '{"validated":false}', '{"validated":true,"extra":1}']) {
  test(`invalid validation response ${response} leaves existing artifacts intact`, (t) => {
    const f = fixture(t, { validate: () => success(response) });
    assert.throws(f.build, /Invalid helper validation response/);
    f.unchanged();
  });
}

test('failed validation exits and timeouts never publish the compiled temporary file', (t) => {
  for (const result of [{ ...success(), status: 2 }, { ...success(), error: { code: 'ETIMEDOUT' } }]) {
    const f = fixture(t, { validate: () => result });
    assert.throws(f.build, /Helper validation failed or exceeded/);
    f.unchanged();
  }
});

test('a source changed during compilation cannot get a stale manifest', (t) => {
  const f = fixture(t, { compile({ sourceFile }) { fs.appendFileSync(sourceFile, '\n// changed'); } });
  assert.throws(f.build, /changed during the build/);
  f.unchanged();
});

test('validation cannot modify the executable after its digest was recorded', (t) => {
  const f = fixture(t, {
    validate({ executable }) {
      fs.appendFileSync(executable, ' changed');
      return success('{"validated":true}');
    },
  });
  assert.throws(f.build, /changed during the build/);
  f.unchanged();
});

test('wrong platform and relative system roots fail before executing a compiler', (t) => {
  for (const dependency of [{ platform: 'linux' }, { arch: 'arm64' }, { systemRoot: 'Windows' }]) {
    const f = fixture(t, { dependencies: dependency });
    assert.throws(f.build, /Windows/);
    assert.equal(f.calls.length, 0);
    f.unchanged();
  }
});

test('unrecognized compiler provenance fails before compilation', (t) => {
  const f = fixture(t, { dependencies: { run: () => success('unexpected compiler banner') } });
  assert.throws(f.build, /Cannot identify/);
  f.unchanged();
});

test('localized system compiler banners retain their exact version', (t) => {
  const f = fixture(t);
  const original = f.dependencies.run;
  f.dependencies.run = (executable, args, options) => args[0] === '/help'
    ? success('Microsoft (R) Visual C# 编译器 版本 4.8.9232.0\r\n')
    : original(executable, args, options);
  assert.equal(f.build().compiler_version, '4.8.9232.0');
});

test('build CLI accepts only its explicit output directory option', () => {
  assert.deepEqual(parseArgs([]), {});
  assert.deepEqual(parseArgs(['--if-supported']), { ifSupported: true });
  assert.deepEqual(parseArgs(['--output-dir', 'packaged app/lib']), { outputDir: 'packaged app/lib' });
  for (const args of [['--source', 'other.cs'], ['--output-dir'], ['--output-dir', ' '], ['--skip-validation']]) {
    assert.throws(() => parseArgs(args), /Usage/);
  }
});

test('prestart skips unsupported hosts while an explicitly requested build still fails', () => {
  for (const platform of ['linux', 'darwin', 'win32']) {
    const dependencies = { platform, arch: platform === 'win32' ? 'arm64' : 'x64' };
    assert.deepEqual(runBuildCommand(['--if-supported'], dependencies), {
      skipped: true, reason: 'Window capture helper is available only on Windows x64.',
    });
    assert.throws(() => runBuildCommand([], dependencies), /native Windows x64 build/);
  }
});
