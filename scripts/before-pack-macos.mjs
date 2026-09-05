import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';

export default async function beforePackMac(context) {
  assert.equal(context.electronPlatformName, 'darwin');
  const arch = {1: 'x64', 3: 'arm64'}[context.arch];
  assert(arch, 'Only native single-architecture Mac builds are supported');
  const desktop = context.packager.projectDir;
  // Upstream re-stages natives here: preserve that hook, then sign its FINAL bytes.
  const upstream = await import(pathToFileURL(path.join(desktop, 'scripts/before-pack.mjs')).href);
  await upstream.default(context);
  assert(process.env.DESKTOP_BUILD_PYTHON && process.env.DESKTOP_BUILD_LOGS);
  execFileSync(process.env.DESKTOP_BUILD_PYTHON, [
    path.join(import.meta.dirname, 'macos_signing.py'), '--stage', desktop, '--arch', arch,
    '--logs', process.env.DESKTOP_BUILD_LOGS,
  ], {stdio: 'inherit', env: process.env});
}
