"""Publish only a verified main-branch one-time installer workflow artifact."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from package_installer import ASSET, TAG, TOKEN, VERSION, PUBLIC_URL, FILES, cask_text

REPO = 'frankhommers/hermes-desktop-builds'


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def validate_run(run, commit):
    if not (run.get('repository', {}).get('full_name') == REPO
            and run.get('head_repository', {}).get('full_name') == REPO
            and run.get('head_branch') == 'main' and run.get('head_sha') == commit
            and run.get('event') == 'workflow_dispatch' and run.get('conclusion') == 'success'
            and run.get('status') == 'completed'
            and run.get('path') == '.github/workflows/mainstream-client.yml'):
        raise ValueError('Require successful exact main-branch native workflow')


def validate_evidence(evidence, watch, sha):
    migration = evidence.get('fullMigration', {})
    cycle = evidence.get('officialUpdateCycle', {})
    if evidence.get('arch') != 'arm64' or evidence.get('watcherExitCode') != 0 or evidence.get('trackedChangesAfterBuild') != '':
        raise ValueError('Incomplete ARM native proof')
    for flag in ('homebrewInstall', 'legacyCaskPinned', 'bootstrapUninstallPreservedApp',
                 'originalUserDataBytesPreserved', 'oldAppRetained', 'realCanonicalClone', 'updaterEntrypointPresent'):
        if not isinstance(migration, dict) or migration.get(flag) is not True:
            raise ValueError('Missing migration gate: '+flag)
    plist_gate = migration.get('launchdPlistRegression')
    if not isinstance(plist_gate, dict) or any(plist_gate.get(k) is not True for k in ('knownHermesBlocked', 'protectedFileRaisesPermissionError', 'unrelatedPlistsPreserved')):
        raise ValueError('Native third-party/known-Hermes plist regression proof missing')
    launcher_gate = migration.get('launcherBackupRegression')
    if not isinstance(launcher_gate, dict) or any(launcher_gate.get(k) is not True for k in (
            'singleAppInInstallDirectory', 'privateNoindexBackup', 'backupBytesAndSignaturePreserved',
            'launchServicesResolvesInstalledApp', 'nativeFailedSwapRestoresOldApp')):
        raise ValueError('Native launcher selection/private backup/rollback proof missing')
    if migration.get('archiveSha256') != sha or migration.get('publicArchiveUrl') != PUBLIC_URL:
        raise ValueError('Native Homebrew tested a different release payload')
    if not isinstance(cycle, dict) or cycle.get('status') != 'advanced' or cycle.get('automaticRelaunchObserved') is not True or cycle.get('remoteRoutePreserved') is not True:
        raise ValueError('Genuine advancing official update/relaunch missing')
    if cycle.get('before') == cycle.get('after') or cycle.get('stamp', {}).get('commit') != cycle.get('after') or cycle.get('stamp', {}).get('distribution'):
        raise ValueError('Official app/source did not advance together')
    if watch.get('observedUntilStop') is not True or watch.get('observerErrors') != [] or watch.get('violations') != [] or watch.get('samples', 0) < 10:
        raise ValueError('Observer incomplete or local start observed')
    if evidence.get('migratorStaging', {}).get('signaturePreserved') is not True:
        raise ValueError('Staging signature proof missing')
    storage = evidence.get('nativeStorageAudit')
    if not isinstance(storage, list) or {x.get('seed') for x in storage} != {'remote', 'local'}:
        raise ValueError('Native seeded storage proof missing')
    for item in storage:
        if item.get('detected') is not True or item.get('originalBytesPreserved') is not True or item.get('refused') is not (item['seed'] == 'local'):
            raise ValueError('Native restored-route proof failed')
    if not isinstance(evidence.get('nativeStartup'), dict) or evidence['nativeStartup'].get('remoteRoutePersisted') is not True:
        raise ValueError('Native outage startup missing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9]+', args.run):
        parser.error('Numeric run ID required')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    run = json.loads(gh('api', f'repos/{REPO}/actions/runs/{args.run}'))
    validate_run(run, commit)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gh('run', 'download', args.run, '--repo', REPO, '--name', 'mainstream-installer', '--dir', str(root/'package'))
        gh('run', 'download', args.run, '--repo', REPO, '--name', 'mainstream-proof-arm64', '--dir', str(root/'proof'))
        package = root/'package'
        archive = package/ASSET
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        if (package/'SHA256SUMS').read_text() != f'{sha}  {ASSET}\n' or (package/(TOKEN+'.rb')).read_text() != cask_text(sha):
            raise ValueError('CI archive/checksum/cask mismatch')
        with zipfile.ZipFile(archive) as z:
            if set(z.namelist()) != {'Hermes-mainstream/'+f for f in (*FILES, 'LICENSE')}:
                raise ValueError('Unexpected installer contents')
            for name in FILES:
                if z.read('Hermes-mainstream/'+name) != (Path('mainstream')/name).read_bytes():
                    raise ValueError('Archive does not match verified main: '+name)
        evidence = json.loads((root/'proof/acceptance.json').read_text())
        watch = json.loads((root/'proof/process-watch.json').read_text())
        validate_evidence(evidence, watch, sha)
        receipt = {'buildRun':run['html_url'], 'buildCommit':commit, 'archiveSha256':sha,
                   'native':evidence, 'processObserver':watch,
                   'limitations':['synthetic remote fixtures, not real-account VPS authentication',
                                  'ad-hoc signed, not Developer ID signed or Apple notarized',
                                  'no local autostart under saved remote-primary state, not hard OFF',
                                  'NSWorkspace resolution verified; third-party launcher/Dock caches not covered']}
        (package/'native-verification.json').write_text(json.dumps(receipt, indent=2)+'\n')
        notes = root/'notes.md'
        notes.write_text(f'''One-time Homebrew migration to the unmodified official Hermes client/updater.

Apple Silicon, macOS Sequoia or newer; requires an existing closed Hermes.app with a saved remote-primary connection.
Install through `brew install --cask frankhommers/tap/{TOKEN}` after the tap is updated.
Homebrew installs Python/Node and the client once; all subsequent app updates use the official in-app updater. The legacy community cask is pinned to avoid overwriting it.

Verified: actual Homebrew migration, preserved original data/old app/signature, private noindex rollback backup rather than a duplicate in Applications, native failed-swap rollback, OS launcher resolution to the installed app, seeded Chromium restore audit, remote-outage startup, genuine official source/app advance and automatic restart with a healthy process observer. Receipt: {run['html_url']}.

No production Mac/VPS was changed. Synthetic CI does not prove personal-account OAuth/chat, sleep/reconnect, personal-Mac Gatekeeper approval, or third-party launcher/Dock cache behavior. Ad-hoc signing is not notarization. Deliberate local use or changed/lost settings may start a local agent. Keep the source, Python and Node for future updates (which may install `.[all]`).
''')
        assets = sorted(package.iterdir())
        # Never reuse or overwrite a published release; an exact draft can resume.
        prior = subprocess.run(['gh','release','view',TAG,'--repo',REPO,'--json','apiUrl'], capture_output=True, text=True)
        if prior.returncode != 0:
            gh('release','create',TAG,*map(str,assets),'--repo',REPO,'--target',commit,'--title','Mainstream installer '+VERSION,
               '--notes-file',str(notes),'--draft','--prerelease=false','--latest=false')
        endpoint = json.loads(gh('release','view',TAG,'--repo',REPO,'--json','apiUrl'))['apiUrl']
        remote = json.loads(gh('api',endpoint))
        if remote.get('draft') is not True or remote.get('target_commitish') != commit or remote.get('body', '').strip() != notes.read_text().strip():
            raise ValueError('Refusing non-matching or already-published release')
        actual = {a['name']:(a['size'],a.get('digest')) for a in remote['assets']}
        expected = {p.name:(p.stat().st_size, 'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()) for p in assets}
        if actual != expected:
            raise ValueError('Uploaded assets failed exact readback')
        gh('release','edit',TAG,'--repo',REPO,'--draft=false','--prerelease=false','--latest=false')
        final = json.loads(gh('api',endpoint))
        if final.get('draft') is not False or final.get('prerelease') is not False:
            raise ValueError('Publication readback failed')
        print(final['html_url'])


if __name__ == '__main__':
    main()
