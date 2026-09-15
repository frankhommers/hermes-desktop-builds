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
    parser.add_argument('--update-cycle', action='store_true', help='Use clean disposable runner home to exercise LaunchServices relaunch')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('This build proof runs only on disposable GitHub Actions macOS runners')
    root = args.root.resolve()
    validate_root(root, Path(os.environ['RUNNER_TEMP']))
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    logs = root/'evidence'; logs.mkdir()
    env = isolated_env(root, os.environ)
    # Intel cryptography has no wheel at this pin. Reuse the runner's actual
    # Rust toolchain binaries, not rustup shims tied to the old HOME. Cargo's
    # cache/config still stays within our isolated HOME; no user credentials.
    rustc = shutil.which('rustc')
    if rustc:
        probe = subprocess.run([rustc, '--print', 'sysroot'], capture_output=True, text=True, timeout=20)
        if probe.returncode == 0:
            rust_bin = Path(probe.stdout.strip())/'bin'
            if (rust_bin/'cargo').is_file():
                env['PATH'] = str(rust_bin) + os.pathsep + env['PATH']
    if args.update_cycle:
        # These are the EPHEMERAL ACTIONS RUNNER's canonical paths, never a
        # person's Mac. A real open/LaunchServices restart must not depend on
        # inherited HERMES_* environment, which launchd does not preserve.
        runner_home = Path(os.environ['HOME']).resolve()
        canonical_state = runner_home/'.hermes'
        canonical_ui = runner_home/'Library/Application Support/Hermes'
        if canonical_state.exists() or canonical_ui.exists():
            raise RuntimeError('Refusing canonical-runner proof over existing Hermes data')
        env['HOME'] = str(runner_home)
        env['HERMES_HOME'] = str(canonical_state)
        env['HERMES_DESKTOP_USER_DATA_DIR'] = str(canonical_ui)
    Path(env['HOME']).mkdir(exist_ok=True)
    Path(env['HERMES_HOME']).mkdir()
    Path(env['HERMES_DESKTOP_USER_DATA_DIR']).mkdir(parents=True)
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
        run([python, HERE/'stage_native.py', source_app, installed, logs], source)
        evidence['migratorStaging'] = json.loads((logs/'migrator-staging.json').read_text())
        stamp = json.loads((installed/'Contents/Resources/install-stamp.json').read_text())
        if stamp['commit'] != SOURCE_COMMIT or stamp.get('distribution') or stamp.get('dirty'):
            raise RuntimeError('Native stamp must prove clean official source and no community provider')
        evidence['installStamp'] = stamp
        if args.update_cycle and platform.machine() == 'arm64':
            # Exercise the real one-time installer, not just its helper calls.
            # Retain bootstrap material in the disposable sandbox for observer
            # imports; the canonical installation target becomes genuinely absent.
            (logs/'stop-watcher').touch()
            watcher.wait(timeout=20)
            if watcher.returncode != 0:
                raise RuntimeError('Bootstrap process observer failed')
            (logs/'process-watch.json').rename(logs/'bootstrap-process-watch.json')
            (logs/'stop-watcher').unlink()
            bootstrap_source = root/'bootstrap-source'
            source.rename(bootstrap_source)
            watcher = subprocess.Popen([str(bootstrap_source/'venv/bin/python'), str(HERE/'watch_processes.py'), str(bootstrap_source), str(logs)], env=env, cwd=bootstrap_source)
            run([sys.executable, HERE/'full_migration.py', source, bootstrap_source, installed, logs], root, timeout=3900)
            evidence['fullMigration'] = json.loads((logs/'full-migration.json').read_text())
        else:
            evidence['fullMigration'] = 'not-run: candidate installer scope is Apple Silicon'
        run([python, HERE/'storage_native.py', source, root, logs], source, timeout=180)
        evidence['nativeStorageAudit'] = json.loads((logs/'storage-native.json').read_text())
        run(['node', HERE/'startup.mjs', source, installed/'Contents/MacOS/Hermes', root, logs], source, timeout=480)
        evidence['nativeStartup'] = json.loads((logs/'startup.json').read_text())
        if args.update_cycle:
            run(['node', HERE/'update.mjs', source, installed/'Contents/MacOS/Hermes', logs], source, timeout=2400)
            evidence['officialUpdateCycle'] = json.loads((logs/'official-update-cycle.json').read_text())
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
