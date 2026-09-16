"""Disposable native regression fixtures, never loaded as services.

Root permission is used ONLY to create/remove a synthetic unreadable plist in a
GitHub-hosted runner. The actual Homebrew installer remains unprivileged.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import plistlib
import subprocess
import sys


def command(args):
    subprocess.run(list(map(str, args)), check=True, capture_output=True, timeout=20)


@contextmanager
def launchd_regression_fixtures(proof_root):
    if sys.platform != 'darwin' or os.environ.get('CI') != 'true' or Path.home() != Path('/Users/runner'):
        raise RuntimeError('Native plist fixtures require a disposable hosted macOS runner')
    from installer import check_closed_and_services, Refusal
    folder = Path.home()/'Library/LaunchAgents'
    folder.mkdir(parents=True, exist_ok=True)
    invalid = folder/'battery.plist'
    known = folder/'ai.hermes.gateway.plist'
    protected = Path('/Library/LaunchAgents/com.maintain.PurgeInactiveMemory.plist')
    source = proof_root/'protected-plist-fixture.source'
    for p in (invalid, known, protected, source):
        if p.exists() or p.is_symlink():
            raise RuntimeError('Refusing to overwrite pre-existing fixture path: '+str(p))
    invalid_bytes = b'Deliberately invalid third-party launchd regression fixture\n'
    source.write_bytes(plistlib.dumps({'Label':'com.maintain.PurgeInactiveMemory', 'ProgramArguments':['/usr/bin/true'], 'RunAtLoad':False}))
    protected_created = False
    invalid.write_bytes(invalid_bytes)
    try:
        command(['/usr/bin/sudo','-n','/usr/bin/install','-o','root','-g','wheel','-m','600',source,protected])
        protected_created = True
        stat_before = protected.stat()
        assert stat_before.st_uid == 0 and stat_before.st_mode & 0o777 == 0o600
        try:
            protected.read_bytes()
        except PermissionError:
            pass
        else:
            raise AssertionError('Protected fixture must reproduce actual PermissionError')
        known.write_bytes(b'not loaded; stopped Hermes registration fixture')
        try:
            try:
                check_closed_and_services(Path.home())
            except Refusal as error:
                assert known.name in str(error), 'Must refuse the known Hermes registration'
            else:
                raise AssertionError('Known Hermes registration was not blocked')
        finally:
            known.unlink()
        receipt = {'knownHermesBlocked':True, 'protectedFileRaisesPermissionError':True}
        yield receipt
        assert invalid.read_bytes() == invalid_bytes, 'Installer modified unrelated invalid plist'
        stat_after = protected.stat()
        for key in ('st_uid', 'st_gid', 'st_mode', 'st_ino', 'st_mtime_ns'):
            assert getattr(stat_after, key) == getattr(stat_before, key), 'Installer changed protected plist metadata'
        command(['/usr/bin/sudo','-n','/usr/bin/cmp','-s',source,protected])
        receipt['unrelatedPlistsPreserved'] = True
    finally:
        invalid.unlink(missing_ok=True)
        if protected_created:
            command(['/usr/bin/sudo','-n','/bin/rm','--',protected])
        source.unlink(missing_ok=True)
