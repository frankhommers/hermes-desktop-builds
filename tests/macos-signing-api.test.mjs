// Dependency-contract unit test ONLY: subprocesses are intercepted, never signing real code.
// Real codesign/native/UI/Gatekeeper tests run separately in scripts/build.py on Macs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import childProcess from 'node:child_process';
import {signingOptions} from '../scripts/sign-macos.mjs';

const source = process.env.DESKTOP_SIGNING_TEST_SOURCE;
assert(source, 'Point DESKTOP_SIGNING_TEST_SOURCE at the pinned installed source tree');
const require = createRequire(path.join(source, 'apps/desktop/package.json'));
// osx-sign 2.x is ESM (read-only exports, `sign` replaces `signAsync`). Its util.execFileAsync
// calls the shared node:child_process object, so intercept execFile there instead.
const osxSign = await import(pathToFileURL(require.resolve('@electron/osx-sign')).href);
const sign = osxSign.sign ?? osxSign.signAsync;
assert.equal(typeof sign, 'function', 'Pinned osx-sign must export sign()');

test('actual pinned signing API honors the exclusion callback before invoking codesign', async () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'mac-sign-api-test-'));
  const desktop = path.join(base, 'apps/desktop');
  const app = path.join(base, 'Hermes.app');
  const resources = path.join(app, 'Contents/Resources');
  const unpacked = path.join(resources, 'app.asar.unpacked');
  fs.mkdirSync(desktop, {recursive: true});
  fs.mkdirSync(unpacked, {recursive: true});
  fs.writeFileSync(path.join(desktop, 'package.json'), JSON.stringify({devDependencies: {electron: '40.10.2'}}));
  const native = path.join(app, 'Contents/native');
  fs.writeFileSync(native, Buffer.from('cffaedfe00000000', 'hex'));
  const excluded = [path.join(resources, 'app.asar'), path.join(resources, 'icon.png'), path.join(unpacked, 'pty.node')];
  for (const file of excluded) fs.writeFileSync(file, Buffer.from('cffaedfe00000000', 'hex'));
  // app.asar/icon.png are arbitrary binary DATA rather than Mach-O in real builds.
  for (const file of excluded.slice(0, 2)) fs.writeFileSync(file, Buffer.from([0, 255, 128, 0]));
  const calls = [];
  const original = childProcess.execFile;
  childProcess.execFile = (command, args, options, callback) => {
    callback ??= typeof options === 'function' ? options : undefined;
    try {
      assert.equal(command, 'codesign', 'Unit test must never invoke external identity/security tools');
      calls.push(args);
    } catch (error) {
      process.nextTick(callback, error, '', '');
      return;
    }
    process.nextTick(callback, null, '', '');
  };
  try {
    await sign(signingOptions(base, app));
    const signed = calls.filter(args => args.includes('--sign')).map(args => args.at(-1));
    assert.deepEqual(signed.sort(), [native, app].sort(), 'Only unhashed Mach-O and the final bundle may be signed');
    for (const file of excluded) assert(!signed.includes(file), 'Must not sign ' + file);
  } finally {
    childProcess.execFile = original;
    fs.rmSync(base, {recursive: true, force: true});
  }
});
