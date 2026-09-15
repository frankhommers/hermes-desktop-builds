#!/usr/bin/env python3
"""One-time macOS handoff to the unmodified official source updater.
No launch, service registration, credentials rewrite, brew, or update feed.
"""
import argparse
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

COMMIT = 'f13a87e610611ce6d9fd82bff8c2d2a642312183'
ORIGIN = 'https://github.com/NousResearch/hermes-agent.git'
TILE_KEYS = ('hermes.desktop.sessionTiles.v1', 'hermes.desktop.sessionTiles.v2')


class Refusal(Exception):
    pass


def no_symlinks(path):
    for p in (path, *path.parents):
        if p.is_symlink():
            raise Refusal('Symlink in a managed path; choose a direct path.')


def read_json(path):
    no_symlinks(path)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise Refusal('Required saved JSON is missing or invalid; repair it in the existing app.') from None


def validate_saved_route(config, registry):
    if not isinstance(config, dict) or not isinstance(registry, dict):
        raise Refusal('Saved routing must be an object.')
    if config.get('mode') != 'remote' or config.get('profiles'):
        raise Refusal('Save a machine-global remote and remove legacy profile overrides in Settings first.')
    if registry.get('version') != 2 or registry.get('launchMode') != 'primary':
        raise Refusal('Require registry v2 and Primary gateway startup.')
    entries = registry.get('connections')
    if not isinstance(entries, list) or not all(isinstance(x, dict) for x in entries):
        raise Refusal('Invalid registry entries.')
    ids = [e.get('id') for e in entries]
    if not all(isinstance(x, str) and x for x in ids) or len(ids) != len(set(ids)):
        raise Refusal('Invalid or duplicate registry IDs.')
    local = [e for e in entries if e.get('kind') == 'local']
    primary = next((e for e in entries if e.get('id') == registry.get('primary')), {})
    if len(local) != 1 or local[0].get('id') != 'local' or primary.get('kind') != 'remote':
        raise Refusal('Require the standard local entry and a remote primary.')
    remote = config.get('remote', {})
    if not isinstance(remote, dict):
        raise Refusal('Missing saved remote.')
    try:
        url = urlsplit(remote.get('url', ''))
        valid = url.scheme == 'https' and url.hostname and not url.username and not url.password and not url.fragment and not url.query
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise Refusal('Require a saved HTTPS remote without embedded credentials/query/fragment.')
    # Route identity is not ciphertext equality: safeStorage can encrypt the
    # same credential independently. Leave authentication to the native client.
    for key in ('url', 'authMode'):
        if remote.get(key) != primary.get(key):
            raise Refusal('Global remote and registry primary differ; save the same connection in Settings.')
    if remote.get('authMode') not in ('oauth', 'token'):
        raise Refusal('Unsupported saved auth mode.')
    if remote.get('authMode') == 'token' and not (remote.get('token') and primary.get('token')):
        raise Refusal('Saved token missing; authenticate in the existing app.')
    return primary['id']


def validate_restore_storage(userdata, snapshot=None, allowed_ids=None):
    """Inspect actual sessionTiles scopes, not unrelated Chromium databases.
    Snapshot is read by storage-audit.cjs against a PRIVATE COPY of userData.
    No original storage is ever opened by that helper or cleared by this tool.
    """
    if snapshot is None:
        return  # Filesystem presence alone says nothing about restored routing.
    if not isinstance(snapshot, dict):
        raise Refusal('Incomplete restore-scope audit.')
    for key in TILE_KEYS:
        raw = snapshot.get(key)
        if raw is None:
            continue
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            raise Refusal('Malformed restored tile state; close saved tiles in the old app.') from None
        if key.endswith('.v2') and not isinstance(value, dict):
            raise Refusal('Unknown restored tile scope format.')
        lists = [value] if key.endswith('.v1') else list(value.values())
        for tiles in lists:
            if not isinstance(tiles, list):
                raise Refusal('Unknown restored tile format.')
            for tile in tiles:
                if not isinstance(tile, dict) or not isinstance(tile.get('storedSessionId'), str):
                    continue  # Official parseTileList also discards these.
                route = tile.get('ownerRoute') or {}
                if not isinstance(route, dict) or route.get('mode') == 'local' or route.get('connectionId') == 'local':
                    raise Refusal('Restored local tile found; close it in the old app, quit, then retry. Nothing was cleared.')
                if not route.get('connectionId') or (allowed_ids is not None and route.get('connectionId') not in allowed_ids):
                    raise Refusal('Restored tile ownership is ambiguous; close it in the old app before migrating.')


def validate_node(node, npm):
    def version(raw):
        m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', raw.strip())
        if not m:
            raise Refusal('Cannot validate Node/npm version.')
        return tuple(map(int, m.groups()))
    n, p = version(node), version(npm)
    if not ((n[0] == 22 and n >= (22, 22, 0)) or (n[0] == 24 and n >= (24, 11, 0)) or n >= (26, 0, 0)):
        raise Refusal('Install Node 22.22+, 24.11+, or 26+ before migrating (no Homebrew actions are run).')
    if (11, 10, 0) <= p < (11, 17, 0):
        raise Refusal('npm 11.10–11.16 is unsupported; use npm <11.10 or >=11.17.')


def run(args, cwd=None, env=None):
    # Never echo subprocess output: package/git hooks can contain secrets.
    try:
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        raise Refusal('Required executable unavailable.') from None
    if result.returncode:
        raise Refusal('A prerequisite/build/verification command failed (output withheld to protect credentials).')
    return result.stdout.strip()


def check_closed_and_services(home):
    processes = run(['/bin/ps', '-axo', 'command='])
    for line in processes.splitlines():
        low = line.lower()
        if ('hermes.app/contents/' in low or re.search(r'(?:hermes|hermes_cli\.main)\s+(?:serve|dashboard|gateway\s+run|chat)(?:\s|$)', low)):
            raise Refusal('Quit Hermes Desktop and local Hermes services before migration.')
    for folder in (home/'Library/LaunchAgents', Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons')):
        if not folder.exists():
            continue
        for p in folder.glob('*.plist'):
            try:
                data = plistlib.loads(p.read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                raise Refusal('Cannot inspect a launchd plist; resolve permissions/format first.') from None
            if 'hermes' in p.name.lower() or 'hermes' in str(data).lower():
                raise Refusal('Hermes launchd registration exists, even if stopped. Review/uninstall it yourself; no services were removed.')
    for domain in (f'gui/{os.getuid()}', 'system'):
        if 'hermes' in run(['/bin/launchctl', 'print', domain]).lower():
            raise Refusal('Loaded Hermes launchd registration found; no services were removed.')


def preflight(userdata, app):
    if platform.system() != 'Darwin':
        raise Refusal('This migrator runs only on macOS; Linux is for unit tests only.')
    if platform.machine() != 'arm64':
        raise Refusal('Only Apple Silicon is in the audited supported macOS scope.')
    home = Path.home()
    if userdata != home/'Library/Application Support/Hermes':
        raise Refusal('Unmodified Finder launch uses canonical Hermes userData. Custom/exported storage cannot establish its saved startup route.')
    if not app.is_absolute():
        raise Refusal('Use an absolute app path.')
    if os.geteuid() == 0:
        raise Refusal('Run as your normal Mac user, not sudo/root.')
    for key in os.environ:
        if key.startswith('HERMES_') and os.environ[key]:
            raise Refusal('Unset HERMES_* environment overrides before migration; use the saved machine-global route.')
    for p in (userdata, app, home/'.hermes', home/'.hermes/hermes-agent'):
        no_symlinks(p)
    if not app.is_dir() or not userdata.is_dir():
        raise Refusal('Require an existing Hermes.app and saved Desktop userData.')
    if not os.access(app, os.W_OK) or not os.access(app.parent, os.W_OK):
        raise Refusal('App and parent must be writable for the official updater; do not use sudo.')
    check_closed_and_services(home)
    config, registry = read_json(userdata/'connection.json'), read_json(userdata/'connections.json')
    validate_saved_route(config, registry)
    for name in ('git', 'node', 'npm', 'codesign', 'ditto', 'xattr', 'open', 'clang'):
        if not shutil.which(name):
            raise Refusal('Missing required tool: '+name+' (install prerequisites first).')
    run(['/usr/bin/xcode-select', '-p'])
    validate_node(run(['node', '--version']), run(['npm', '--version']))
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise Refusal('Run this installer with Python >=3.11,<3.14.')
    return config, registry


def verify_app(app):
    if not (app/'Contents/MacOS/Hermes').is_file():
        raise Refusal('Build did not produce a Hermes executable.')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])


def swap_app(stage, app, backup, verify):
    for p in (stage, app, backup):
        no_symlinks(p)
    if backup.exists():
        raise Refusal('Backup destination already exists.')
    verify(stage)
    app.rename(backup)
    try:
        stage.rename(app)
        verify(app)
    except BaseException:
        if app.exists():
            app.rename(stage)
        backup.rename(app)
        raise


def verify_repo(root):
    if not (root/'.git').is_dir():
        raise Refusal('Official updater requires a real clone, not a linked worktree.')
    checks = [('remote', 'get-url', 'origin'), ('rev-parse', 'HEAD'), ('branch', '--show-current'), ('status', '--porcelain')]
    values = [run(['git', '-C', root, *args]) for args in checks]
    if values != [ORIGIN, COMMIT, 'main', '']:
        raise Refusal('Canonical source must be clean official origin/main at the pinned initial commit. Existing source was not overwritten.')
    if run(['git', '-C', root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']) != 'origin/main':
        raise Refusal('Source main must track origin/main.')


def audit_storage(root, userdata, backup):
    clone = backup/'storage-inspection'
    shutil.copytree(userdata, clone, symlinks=True)
    # Never follow externally linked user storage when inspecting the copy.
    for p in clone.rglob('*'):
        if p.is_symlink():
            p.unlink()
    output = backup/'restore-scopes.json'
    electron = root/'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'
    if not electron.exists():
        electron = root/'apps/desktop/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'
    run([electron, Path(__file__).with_name('storage-audit.cjs'), clone, output])
    snapshot = read_json(output)
    if not isinstance(snapshot, dict) or snapshot.get('auditVersion') != 1 or snapshot.get('origin') != 'file://':
        raise Refusal('Storage audit could not verify the packaged file:// origin.')
    keys = snapshot.get('keys')
    if not isinstance(keys, dict) or not all(key in keys for key in TILE_KEYS):
        raise Refusal('Incomplete restore-scope audit.')
    registry = read_json(userdata/'connections.json')
    allowed = {x['id'] for x in registry['connections'] if x.get('kind') in ('remote', 'cloud')}
    validate_restore_storage(userdata, keys, allowed)


def install(userdata, app, confirmed=False):
    initial = preflight(userdata, app)
    root = Path.home()/'.hermes/hermes-agent'
    if root.exists():
        verify_repo(root)
    if not confirmed:
        answer = input('Install official source/venv and build app, retaining backups; never launch? Type INSTALL: ')
        if answer != 'INSTALL':
            raise Refusal('Cancelled; no installation changes made.')
    os.umask(0o077)
    backups = Path.home()/'.hermes/mainstream-backups'
    no_symlinks(backups)
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix='migration-', dir=backups))
    shutil.copytree(userdata, backup/'userData', symlinks=True)
    # Config and OAuth remain untouched; full backup covers .env/auth/profile files too.
    for name in ('config.yaml', '.env', 'auth.json', 'profiles'):
        source = Path.home()/'.hermes'/name
        if source.is_symlink():
            raise Refusal('Linked config requires manual backup; nothing was overwritten.')
        if source.is_dir():
            shutil.copytree(source, backup/name, symlinks=True)
        elif source.exists():
            shutil.copy2(source, backup/name)
    if not root.exists():
        print('Cloning the official repository at the audited initial revision…', flush=True)
        run(['git', 'clone', '--origin', 'origin', '--branch', 'main', ORIGIN, root])
        run(['git', '-C', root, 'checkout', '-B', 'main', COMMIT])
        run(['git', '-C', root, 'branch', '--set-upstream-to=origin/main', 'main'])
    verify_repo(root)
    venv = root/'venv'
    if venv.exists():
        raise Refusal('Existing venv requires manual review; source/app/config preserved. Do not overlay an unknown environment.')
    print('Creating base Python environment; future official updates may install .[all]…', flush=True)
    run([sys.executable, '-m', 'venv', venv])
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'PYTHON', 'ELECTRON_'))}
    env['PATH'] = str(venv/'bin') + os.pathsep + os.environ.get('PATH', '')
    env['HERMES_HOME'] = str(Path.home()/'.hermes')
    run([venv/'bin/python', '-m', 'pip', 'install', '-e', root], cwd=root, env=env)
    run([venv/'bin/hermes', 'desktop', '--force-build', '--build-only'], cwd=root, env=env)
    verify_repo(root)
    if not (venv/'bin/hermes').is_file():
        raise Refusal('Official updater CLI entrypoint missing.')
    artifacts = list((root/'apps/desktop/release').glob('mac*/Hermes.app'))
    if len(artifacts) != 1:
        raise Refusal('Expected exactly one native app build.')
    audit_storage(root, userdata, backup)
    if preflight(userdata, app) != initial:
        raise Refusal('Saved routing changed during build; refusing swap.')
    stage_dir = Path(tempfile.mkdtemp(prefix='.hermes-migration-', dir=app.parent))
    stage = stage_dir/'Hermes.app'
    run(['/usr/bin/ditto', artifacts[0], stage])
    run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', stage])
    old = app.parent/('Hermes.pre-mainstream-'+backup.name+'.app')
    swap_app(stage, app, old, verify_app)
    print('Installed without launching. Old app retained beside the new app; private backup: '+str(backup))
    print('Use the official in-app updater thereafter. Native startup/outage/update acceptance is still required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=Path('/Applications/Hermes.app'))
    parser.add_argument('--user-data', type=Path, default=Path.home()/'Library/Application Support/Hermes')
    parser.add_argument('--preflight-only', action='store_true', help='Read-only checks; storage audit runs after build during install')
    parser.add_argument('--yes', action='store_true', help='Explicitly consent to installation and private backups (never data clearing)')
    args = parser.parse_args()
    try:
        if args.preflight_only:
            preflight(args.user_data, args.app)
            print('Preflight passed. Restore-scope inspection and native lifecycle checks remain.')
        else:
            install(args.user_data, args.app, args.yes)
    except (Refusal, OSError, KeyboardInterrupt) as error:
        print('Stopped: '+(str(error) if isinstance(error, Refusal) else 'Operation interrupted or filesystem unavailable; preserve backups for review.'), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
