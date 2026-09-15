"""Execute the actual installer end-to-end on a disposable runner.

Called after the proof's bootstrap source was moved away. The installed app and
synthetic saved remote are the starting point; no source runtime exists at the
canonical target. This does not claim real VPS authentication.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def file_hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    source, bootstrap, app, logs = map(Path, sys.argv[1:])
    assert os.environ.get('CI') == 'true' and sys.platform == 'darwin'
    assert source == Path.home()/'.hermes/hermes-agent' and not source.exists()
    userdata = Path.home()/'Library/Application Support/Hermes'
    remote = {'url':'https://example.invalid', 'authMode':'oauth'}
    config = {'mode':'remote', 'remote':remote, 'profiles':{}}
    registry = {'version':2,'primary':'remote-proof','launchMode':'primary','lastUsed':'remote-proof','connections':[
        {'id':'local','kind':'local','label':'This device'},
        {'id':'remote-proof','kind':'remote','label':'Synthetic migration fixture',**remote}]}
    for name, data in [('connection.json',config),('connections.json',registry)]:
        (userdata/name).write_text(json.dumps(data))
        (userdata/name).chmod(0o600)
    before = file_hashes(userdata)
    vanilla = {k:v for k,v in os.environ.items() if not k.startswith('HERMES_')}
    vanilla.update(HOMEBREW_NO_AUTO_UPDATE='1', HOMEBREW_NO_ANALYTICS='1', HOMEBREW_NO_ENV_HINTS='1')
    brew = shutil.which('brew')
    assert brew, 'Native Homebrew required'
    def command(args, label, timeout=900):
        result = subprocess.run([str(x) for x in args], env=vanilla, capture_output=True, text=True, timeout=timeout)
        (logs/(label+'.log')).write_text(result.stdout+'\n'+result.stderr)
        assert result.returncode == 0, label+': '+result.stdout+'\n'+result.stderr
        return result.stdout.strip()
    # Start from the genuine published Homebrew client, not a fake receipt.
    app.rename(app.with_name('Hermes.bootstrap.app'))
    command([brew, 'trust', 'frankhommers/tap'], 'brew-trust-legacy')
    command([brew, 'tap', 'frankhommers/tap'], 'brew-tap-legacy')
    command([brew, 'install', '--cask', 'frankhommers/tap/hermes-desktop', '--appdir='+str(app.parent)], 'brew-install-legacy')
    old_app_before = file_hashes(app)
    from package_installer import package, cask_text, TOKEN
    payload = logs.parent/'homebrew-payload'
    archive, sha = package(payload)
    command([brew, 'tap-new', 'hermes-ci/bootstrap'], 'brew-tap-fixture')
    tap = Path(command([brew, '--repository', 'hermes-ci/bootstrap'], 'brew-fixture-path'))
    command(['git', '-C', tap, 'remote', 'add', 'origin', 'https://github.com/hermes-ci/homebrew-bootstrap'], 'brew-fixture-origin')
    command([brew, 'trust', 'hermes-ci/bootstrap'], 'brew-trust-fixture')
    (tap/'Casks').mkdir(exist_ok=True)
    cask = tap/'Casks'/(TOKEN+'.rb')
    cask.write_text(cask_text(sha))
    command([brew, 'style', '--cask', 'hermes-ci/bootstrap/'+TOKEN], 'brew-style')
    command([brew, 'audit', '--cask', 'hermes-ci/bootstrap/'+TOKEN], 'brew-audit')
    cask.write_text(cask_text(sha, archive.as_uri()))
    install = subprocess.run([brew, 'install', '--cask', 'hermes-ci/bootstrap/'+TOKEN, '--appdir='+str(app.parent)],env=vanilla, timeout=3900, capture_output=True,text=True)
    (logs/'full-installer.log').write_text(install.stdout+'\n'+install.stderr)
    for backup in (Path.home()/'.hermes/mainstream-backups').glob('migration-*'):
        log = backup/'commands.log'
        if log.exists():
            shutil.copy2(log, logs/(backup.name+'-commands.log'))
    assert install.returncode == 0, install.stdout+'\n'+install.stderr
    assert file_hashes(userdata) == before, 'Installer modified original userData'
    old_apps=list(app.parent.glob('Hermes.pre-mainstream-*.app'))
    assert len(old_apps)==1, 'Old app must be retained'
    assert (source/'.git').is_dir() and (source/'venv/bin/hermes').exists()
    old_stamp=json.loads((old_apps[0]/'Contents/Resources/install-stamp.json').read_text())
    stamp=json.loads((app/'Contents/Resources/install-stamp.json').read_text())
    from package_installer import PUBLIC_URL
    import importlib.util
    spec = importlib.util.spec_from_file_location('candidate_installer', REPO/'mainstream/installer.py')
    candidate = importlib.util.module_from_spec(spec); spec.loader.exec_module(candidate)
    assert stamp['commit'] == candidate.COMMIT
    assert file_hashes(old_apps[0]) == old_app_before, 'Old app backup must be byte-preserved'
    assert not stamp.get('distribution') and not stamp.get('dirty')
    pinned = command([brew, 'list', '--cask', '--pinned'], 'brew-pins-after').splitlines()
    assert 'hermes-desktop' in pinned, 'Old Homebrew update path must be pinned'
    info = json.loads(command([brew, 'info', '--cask', '--json=v2', 'hermes-ci/bootstrap/'+TOKEN], 'brew-info-after'))
    assert info['casks'][0]['installed'] and info['casks'][0]['auto_updates'] is True
    # Isolate the cask's uninstall artifacts from Homebrew's optional dependency
    # autoremove: Python/Node are required by the handed-over official updater.
    installed_hashes = file_hashes(app)
    vanilla['HOMEBREW_NO_AUTOREMOVE'] = '1'
    command([brew, 'uninstall', '--cask', 'hermes-ci/bootstrap/'+TOKEN], 'brew-uninstall-bootstrap')
    assert file_hashes(app) == installed_hashes and (source/'venv/bin/hermes').is_file()
    result={'installerExitCode':0,'originalUserDataBytesPreserved':True,'oldAppRetained':True,
            'realCanonicalClone':True,'updaterEntrypointPresent':True,'stamp':stamp,
            'homebrewInstall':True,'legacyCaskPinned':True,'bootstrapUninstallPreservedApp':True,
            'archiveSha256':sha,'publicArchiveUrl':PUBLIC_URL,'oldStamp':old_stamp,
            'fixture':'synthetic HTTPS remote, no real account or authenticated VPS chat'}
    (logs/'full-migration.json').write_text(json.dumps(result,indent=2)+'\n')

if __name__ == '__main__': main()
