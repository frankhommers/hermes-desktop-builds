"""Isolated native build/startup proof. Never installs into a real user home.

This is deliberately not a release gate: update-cycle and real VPS chat remain
separate acceptance tests until actually executed.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

SOURCE_COMMIT = 'f13a87e610611ce6d9fd82bff8c2d2a642312183'
ORIGIN = 'https://github.com/NousResearch/hermes-agent.git'
HERE = Path(__file__).resolve().parent


def validate_root(root, runner_temp):
    root, runner_temp = root.resolve(), runner_temp.resolve()
    if root == runner_temp or runner_temp not in root.parents:
        raise ValueError('Proof directory must be a new child of RUNNER_TEMP')


def isolated_env(root, parent):
    env = {k: parent[k] for k in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'DEVELOPER_DIR', 'SDKROOT') if k in parent}
    env.update(HOME=str(root/'home'), HERMES_HOME=str(root/'home/.hermes'),
               HERMES_DESKTOP_USER_DATA_DIR=str(root/'user-data'),
               CI='true', PYTHONUNBUFFERED='1', CSC_IDENTITY_AUTO_DISCOVERY='false',
               PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1')
    return env


def initial_evidence(sha, arch):
    return dict(sourceCommit=sha, arch=arch, officialSourceUnmodified=True,
                nativeStartup='not-run', officialUpdateCycle='not-run',
                authenticatedVpsChat='not-run', releaseReady=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('This build proof runs only on disposable GitHub Actions macOS runners')
    root = args.root.resolve()
    validate_root(root, Path(os.environ['RUNNER_TEMP']))
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    logs = root/'evidence'; logs.mkdir()
    env = isolated_env(root, os.environ)
    Path(env['HOME']).mkdir()
    Path(env['HERMES_HOME']).mkdir()
    Path(env['HERMES_DESKTOP_USER_DATA_DIR']).mkdir()
    source = Path(env['HERMES_HOME'])/'hermes-agent'
    evidence = initial_evidence(SOURCE_COMMIT, platform.machine())
    counter = 0
    def run(argv, cwd=root, timeout=1800):
        nonlocal counter
        counter += 1
        print('STEP', counter, ' '.join(map(str, argv)), flush=True)
        with (logs/f'{counter:02d}.log').open('w') as log:
            p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=log,
                               stderr=subprocess.STDOUT, timeout=timeout)
        if p.returncode:
            print((logs/f'{counter:02d}.log').read_text()[-18000:], flush=True)
            raise RuntimeError(f'Step {counter} failed: {p.returncode}')
    watcher = None
    try:
        run(['git', 'init', source])
        run(['git', 'remote', 'add', 'origin', ORIGIN], source)
        run(['git', 'fetch', '--depth=2', 'origin', SOURCE_COMMIT], source)
        run(['git', 'checkout', '-b', 'main', SOURCE_COMMIT], source)
        run(['git', 'config', 'branch.main.remote', 'origin'], source)
        run(['git', 'config', 'branch.main.merge', 'refs/heads/main'], source)
        run([sys.executable, '-m', 'venv', source/'venv'])
        python = source/'venv/bin/python'
        env['PATH'] = str(source/'venv/bin') + os.pathsep + env['PATH']
        run([python, '-m', 'pip', 'install', '-e', str(source)], source)
        (source/'.install_method').write_text('git\n')
        # Starts before the real official build, so no-launch is exercised too.
        watcher = subprocess.Popen([str(python), str(HERE/'watch_processes.py'), str(source), str(logs)], env=env, cwd=source)
        run([source/'venv/bin/hermes', 'desktop', '--build-only'], source, timeout=3300)
        apps = list((source/'apps/desktop/release').glob('mac*/Hermes.app'))
        if len(apps) != 1:
            raise RuntimeError(f'Expected one native app, got {len(apps)}')
        source_app = apps[0]
        installed = root/'Applications/Hermes.app'; installed.parent.mkdir()
        run(['/usr/bin/ditto', source_app, installed])
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', installed])
        stamp = json.loads((installed/'Contents/Resources/install-stamp.json').read_text())
        if stamp['commit'] != SOURCE_COMMIT or stamp.get('distribution') or stamp.get('dirty'):
            raise RuntimeError('Native stamp must prove clean official source and no community provider')
        evidence['installStamp'] = stamp
        run(['node', HERE/'startup.mjs', source, installed/'Contents/MacOS/Hermes', root, logs], source, timeout=480)
        evidence['nativeStartup'] = json.loads((logs/'startup.json').read_text())
        tracked = subprocess.check_output(['git', 'status', '--porcelain', '-uno'], cwd=source, env=env, text=True)
        evidence['trackedChangesAfterBuild'] = tracked
        if tracked.strip():
            raise RuntimeError('Official build modified tracked source; review before handoff')
    finally:
        if watcher:
            (logs/'stop-watcher').touch()
            watcher.wait(timeout=20)
            evidence['watcherExitCode'] = watcher.returncode
        (logs/'acceptance.json').write_text(json.dumps(evidence, indent=2)+'\n')
    if watcher and watcher.returncode != 0:
        raise RuntimeError('Process watch failed or detected a local Hermes service')
    print(json.dumps(evidence, indent=2))

if __name__ == '__main__':
    main()
