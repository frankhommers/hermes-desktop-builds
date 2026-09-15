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
    install = subprocess.run([sys.executable,str(REPO/'mainstream/installer.py'),'--yes','--app',str(app)],env=vanilla, timeout=3600, capture_output=True,text=True)
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
    assert stamp['commit']==old_stamp['commit'], 'Both genuine source builds start at audited revision'
    assert not stamp.get('distribution')
    result={'installerExitCode':0,'originalUserDataBytesPreserved':True,'oldAppRetained':True,
            'realCanonicalClone':True,'updaterEntrypointPresent':True,'stamp':stamp,
            'fixture':'synthetic HTTPS remote, no real account or authenticated VPS chat'}
    (logs/'full-migration.json').write_text(json.dumps(result,indent=2)+'\n')

if __name__ == '__main__': main()
