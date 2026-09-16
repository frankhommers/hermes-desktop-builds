"""Native launcher resolution and rollback proof; disposable macOS CI only.

NSWorkspace is the OS application resolver, not Raycast/Alfred's private caches.
No app is launched and no global LaunchServices database is reset here.
"""
import json
import os
from pathlib import Path
import plistlib
import stat
import sys

LSREGISTER = Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')


def bundle_id(app):
    return plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleIdentifier']


def resolved_app(identifier, command, label):
    script = ('ObjC.import("AppKit"); '
              'const url = $.NSWorkspace.sharedWorkspace.URLForApplicationWithBundleIdentifier('
              + json.dumps(identifier) + '); '
              'if (!url || url.isNil()) throw new Error("No registered application"); '
              'ObjC.unwrap(url.path);')
    return Path(command(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script], label, timeout=60)).resolve()


def register_legacy_fixture(app, command):
    assert sys.platform == 'darwin' and os.environ.get('CI') == 'true'
    command([LSREGISTER, '-f', app], 'launcher-register-legacy')
    assert resolved_app(bundle_id(app), command, 'launcher-resolve-legacy') == app.resolve()


def verify_launcher_backup(app, logs, command, file_hashes, old_app_before, candidate):
    assert sys.platform == 'darwin' and os.environ.get('CI') == 'true'
    backups = list((Path.home()/'.hermes/mainstream-backups').glob('migration-*/old-app.noindex/Hermes.app'))
    assert len(backups) == 1, 'Require exactly one private rollback app'
    old = backups[0]
    assert set(app.parent.glob('*.app')) == {app}, 'Do not leave a launcher-visible duplicate in the app directory'
    assert not list(app.parent.glob('Hermes.pre-mainstream-*')), 'Legacy backup must not be beside installed app'
    assert old.parent.name.endswith('.noindex')
    for folder in (old.parent, old.parent.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, 'Rollback directory must be private'
    assert file_hashes(old) == old_app_before, 'Backup bytes differ'
    candidate.verify_app(old)
    selected = resolved_app(bundle_id(app), command, 'launcher-resolve-installed')
    assert selected == app.resolve(), f'OS launcher selected {selected}, not {app}'
    # Actual signed bundles, actual renames, deliberately failing post-swap check.
    # This fixture remains outside application discovery and never registers apps.
    proof = logs.parent/'rollback-proof.noindex'
    proof.mkdir(mode=0o700)
    original, staged, rollback = (proof/name for name in ('Original.app', 'Staged.app', 'Rollback.app'))
    command(['/usr/bin/ditto', old, original], 'rollback-copy-old')
    command(['/usr/bin/ditto', app, staged], 'rollback-copy-new')
    def reject_installed(path):
        candidate.verify_app(path)
        if path == original:
            raise candidate.Refusal('Native injected post-swap failure')
    try:
        candidate.swap_app(staged, original, rollback, reject_installed)
    except candidate.Refusal as error:
        assert str(error) == 'Native injected post-swap failure', str(error)
    else:
        raise AssertionError('Injected verification failure was ignored')
    assert file_hashes(original) == old_app_before
    assert file_hashes(staged) == file_hashes(app)
    assert not rollback.exists()
    candidate.verify_app(original)
    receipt = {'singleAppInInstallDirectory': True, 'privateNoindexBackup': True,
               'backupBytesAndSignaturePreserved': True, 'launchServicesResolvesInstalledApp': True,
               'nativeFailedSwapRestoresOldApp': True,
               'selectedApp': str(selected), 'rollbackApp': str(old),
               'scope': 'NSWorkspace resolution; third-party launcher and Dock caches not claimed'}
    (logs/'launcher-backup-regression.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return old, receipt
