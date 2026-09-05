"""Apply a hash-pinned UI patch before building, then verify the exact patched tree.

The upstream checkout is intentionally dirty after application: sourceClean must be
false. A separate receipt records both Git trees and the checked-in patch hashes.
No fuzzy fallback, auto-repair, upstream execution or credentials are involved here.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

PATCH_DIR = Path(__file__).resolve().parents[1] / 'patches'


def patch_set(patch_dir=PATCH_DIR):
    patch_dir = Path(patch_dir)
    raw = (patch_dir / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or type(manifest.get('schema')) is not int
            or manifest['schema'] != 1 or set(manifest) != {'schema', 'patches'}):
        raise ValueError('Unsupported patch manifest')
    rows = manifest['patches']
    if not isinstance(rows, list) or not rows:
        raise ValueError('An explicit nonempty patch set is required')
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'file', 'sha256'}:
            raise ValueError('Malformed patch entry')
        name = row['file']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.patch', name) or name in names:
            raise ValueError('Unsafe or duplicate patch filename')
        names.add(name)
        sha = row['sha256']
        path = patch_dir / name
        if (not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)
                or path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sha):
            raise ValueError('Patch SHA256 verification failed: ' + name)
    return {'manifestSha256': hashlib.sha256(raw).hexdigest(), 'patches': rows}


def _git(src, *args, env=None):
    result = subprocess.run(['git', '-C', str(src), *args], env=env, capture_output=True)
    if result.returncode:
        raise ValueError('Source verification failed: git ' + ' '.join(args) + '\n'
                         + result.stderr.decode('utf-8', errors='replace'))
    return result.stdout.decode('utf-8').strip()


def validate_patch_receipt(receipt, pin, patch_dir=PATCH_DIR):
    expected = patch_set(patch_dir)
    if not isinstance(receipt, dict) or type(receipt.get('schema')) is not int or receipt['schema'] != 1:
        raise ValueError('Missing source patch receipt')
    if receipt.get('verified') is not True or receipt.get('upstreamCommit') != pin['commit']:
        raise ValueError('Unverified or mismatched source patch receipt')
    for name, value in expected.items():
        if receipt.get(name) != value:
            raise ValueError('Wrong source patch set: ' + name)
    for key in ('upstreamCommit', 'upstreamTree', 'patchedTree'):
        value = receipt.get(key)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value) or value == '0' * 40:
            raise ValueError('Invalid source tree identity: ' + key)
    if receipt['upstreamTree'] == receipt['patchedTree']:
        raise ValueError('UI patch must change the source tree')
    return receipt


def verify_source_state(src, pin, receipt, patch_dir=PATCH_DIR, env=None):
    validate_patch_receipt(receipt, pin, patch_dir)
    if _git(src, 'rev-parse', 'HEAD', env=env) != pin['commit']:
        raise ValueError('Source HEAD changed')
    if _git(src, 'rev-parse', 'HEAD^{tree}', env=env) != receipt['upstreamTree']:
        raise ValueError('Upstream source tree changed')
    if _git(src, 'write-tree', env=env) != receipt['patchedTree']:
        raise ValueError('Unexpected staged source changes')
    _git(src, 'diff', '--quiet', '--no-ext-diff', env=env)
    if _git(src, 'ls-files', '--others', '--exclude-standard', env=env):
        raise ValueError('Unexpected untracked source files')
    return receipt


def apply_patches(src, pin, patch_dir=PATCH_DIR, env=None):
    expected = patch_set(patch_dir)
    if _git(src, 'rev-parse', 'HEAD', env=env) != pin['commit']:
        raise ValueError('Source HEAD does not match the exact pin')
    if _git(src, 'status', '--porcelain', '--untracked-files=normal', env=env):
        raise ValueError('Refusing to patch an already-modified source checkout')
    upstream_tree = _git(src, 'rev-parse', 'HEAD^{tree}', env=env)
    for row in expected['patches']:
        path = str((Path(patch_dir) / row['file']).resolve())
        # Restrict the presentation patch to Desktop frontend sources/tests.
        # git apply itself also rejects unsafe repository-relative paths.
        numstat = _git(src, 'apply', '--numstat', path, env=env)
        if not numstat:
            raise ValueError('Empty patch')
        for line in numstat.splitlines():
            fields = line.split('\t')
            if (len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit()
                    or not re.fullmatch(r'apps/desktop/src/[A-Za-z0-9_./-]+\.tsx?', fields[2])
                    or '..' in fields[2].split('/')):
                raise ValueError('Patch is outside the approved frontend/test scope')
        _git(src, 'apply', '--index', '--check', '--whitespace=error-all', path, env=env)
        _git(src, 'apply', '--index', '--whitespace=error-all', path, env=env)
    receipt = {'schema': 1, 'upstreamCommit': pin['commit'], 'upstreamTree': upstream_tree,
               'patchedTree': _git(src, 'write-tree', env=env), 'verified': True, **expected}
    return verify_source_state(src, pin, receipt, patch_dir, env)
