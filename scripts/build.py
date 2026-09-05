#!/usr/bin/env python3
"""Fetch pinned public source, build unchanged Desktop, verify and natively launch it."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile

from common import ROOT, WORK, OUT, load_pin, release_version, clean_environment, run, gate_test_report
from package import digest, native_inventory, inventory, make_archive, unpack_archive


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Run a new isolated native build')
    parser.add_argument('--expected-platform',choices=['darwin','win32','linux'])
    parser.add_argument('--expected-arch',choices=['arm64','x64'])
    args=parser.parse_args()
    if not args.run:parser.print_help();return
    if sys.version_info < (3,11):raise RuntimeError('Python >=3.11 required (build host only)')
    pin=load_pin(); version=release_version(pin)
    target=sys.platform
    arch={'aarch64':'arm64','arm64':'arm64','x86_64':'x64','AMD64':'x64'}.get(platform.machine())
    if target not in ('darwin','win32','linux') or not arch:raise RuntimeError('Unsupported native build host')
    if args.expected_platform and args.expected_platform!=target:raise RuntimeError('Unexpected runner OS')
    if args.expected_arch and args.expected_arch!=arch:raise RuntimeError('Unexpected runner architecture')
    label=f'{target}-{arch}'
    out=OUT/label;logs=out/'logs';logs.mkdir(parents=True,exist_ok=True)
    env=clean_environment(WORK,pin)
    node=shutil.which('node');git=shutil.which('git');npm_path=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
    if not all((node,git,npm_path)):raise RuntimeError('Node, npm and Git are required')
    npm_file=Path(npm_path).parent/'node_modules/npm/bin/npm-cli.js' if os.name=='nt' else Path(npm_path).resolve()
    if not npm_file.is_file():raise RuntimeError('Cannot locate the installed npm CLI')
    npm=[node,str(npm_file)]
    def cmd(name,command,cwd=ROOT,allow_failure=False):return run(name,command,cwd,env,logs,allow_failure)
    hostnode=subprocess.check_output([node,'-p','process.platform+" "+process.arch+" "+process.versions.node'],env=env,text=True).strip()
    if hostnode!=f'{target} {arch} {pin["node"]}':raise RuntimeError(f'Expected pinned native Node, got {hostnode}')
    print('Native build:',hostnode,flush=True)
    src=WORK/'src'
    if src.exists():raise RuntimeError('Refusing to reuse a source tree: use a new checkout/workspace')
    src.mkdir()
    cmd('git-init',[git,'init','--initial-branch=build-source',str(src)])
    cmd('git-fetch',[git,'-c','core.autocrlf=false','fetch','--depth=1','--no-tags',f'https://github.com/{pin["repository"]}.git',pin['commit']],src)
    cmd('git-checkout',[git,'-c','core.autocrlf=false','checkout','--detach','FETCH_HEAD'],src)
    sha=subprocess.check_output([git,'rev-parse','HEAD'],cwd=src,env=env,text=True).strip()
    if sha!=pin['commit']:raise RuntimeError('Fetched commit does not match pin')
    desktop=src/'apps/desktop';pkg=json.loads((desktop/'package.json').read_text())
    if pkg['version']!=pin['version']:raise RuntimeError('Upstream version does not match pin')
    cmd('npm-ci',npm+['ci','--ignore-scripts','--no-audit','--no-fund','--workspace','apps/desktop','--include-workspace-root'],src)
    installer=subprocess.check_output([node,'-p',"require.resolve('electron/install.js')"],cwd=desktop,env=env,text=True).strip()
    cmd('electron-install',[node,installer],desktop)
    cmd('typecheck',npm+['run','typecheck'],desktop)
    # Full upstream suite on Linux; targeted unmodified safety/packaging tests on every host.
    gate=None
    reporter=['--reporter='+str(ROOT/'scripts/completion-reporter.mjs')]
    targeted=['electron/first-run-setup-main-process.test.ts','electron/first-run-setup-gate.test.ts',
              'electron/primary-backend-startup.test.ts','src/components/desktop-install-overlay.test.tsx',
              'scripts/stage-native-deps.test.mjs','scripts/before-pack.test.mjs']
    env['DESKTOP_TEST_RECEIPT']=str(logs/'targeted-completion.json')
    rc=cmd('targeted-tests',npm+['test','--','--maxWorkers=2','--reporter=default','--reporter=json','--outputFile.json='+str(logs/'targeted.json')]+reporter+targeted,desktop,True)
    data=json.loads((logs/'targeted.json').read_text());data['runCompletion']=json.loads((logs/'targeted-completion.json').read_text())
    targeted_gate=gate_test_report(data,pin,rc,target)
    env.pop('DESKTOP_TEST_RECEIPT')
    (logs/'targeted-gate.json').write_text(json.dumps(targeted_gate,indent=2)+'\n')
    print('TARGETED SUITE:',json.dumps(targeted_gate),flush=True)
    cmd('build',npm+['run','build'],desktop)
    builder=subprocess.check_output([node,'-p',"require.resolve('electron-builder/cli.js')"],cwd=desktop,env=env,text=True).strip()
    pack=WORK/'packed'
    buildargs=[node,builder,{'darwin':'--mac','win32':'--win','linux':'--linux'}[target],'--dir','--'+arch,'--publish','never','-c.forceCodeSigning=false','-c.directories.output='+str(pack)]
    if target=='darwin':buildargs+=['-c.mac.identity=null']
    cmd('electron-builder',buildargs,desktop)
    dirname={'darwin':'mac-arm64' if arch=='arm64' else 'mac','win32':'win-unpacked','linux':'linux-unpacked'}[target]
    original=pack/dirname/('Hermes.app' if target=='darwin' else '')
    bundle=WORK/'bundle'/('Hermes.app' if target=='darwin' else 'Hermes')
    bundle.parent.mkdir(exist_ok=True)
    shutil.move(str(original),str(bundle))
    resources=bundle/'Contents/Resources' if target=='darwin' else bundle/'resources'
    shutil.copyfile(src/'LICENSE',resources/'LICENSE.hermes.txt')
    electron_dist=Path(installer).parent/'dist'
    shutil.copyfile(electron_dist/'LICENSE',resources/'LICENSE.electron.txt')
    shutil.copyfile(electron_dist/'LICENSES.chromium.html',resources/'LICENSES.chromium.html')
    # Preserve available license/notice files for bundled and build-time npm dependencies.
    license_dir=resources/'ThirdPartyLicenses'
    for i,modules in enumerate((src/'node_modules',desktop/'node_modules')):
        for p in modules.rglob('*'):
            if p.is_file() and not p.is_symlink() and p.name.lower().split('.')[0] in ('license','licence','notice','copying'):
                dest=license_dir/str(i)/p.relative_to(modules);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    if target=='darwin':
        info=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        if info['CFBundleExecutable']!='Hermes' or info['CFBundleIdentifier']!='com.nousresearch.hermes':raise RuntimeError('Wrong Mac bundle identity')
        # No Developer ID, keychain discovery or notarization. Retain upstream native binaries.
    natives=native_inventory(bundle,target,arch)
    (logs/'native-binaries.json').write_text(json.dumps(natives,indent=2)+'\n')
    cmd('inspect',[node,str(ROOT/'scripts/inspect.mjs'),str(src),str(resources),target,arch,str(logs)],ROOT)
    suffix='.tar.gz' if target=='linux' else '.zip'
    archive=out/f'Hermes-{version}-{target}-{arch}-unsigned{suffix}'
    make_archive(bundle,archive)
    extracted=WORK/'extracted'
    unpack_archive(archive,extracted)
    if inventory(bundle)!=inventory(extracted/bundle.name):raise RuntimeError('Archive roundtrip changed file bytes/modes/symlinks')
    # Launch the extracted distribution, not the unarchived build tree.
    binary=extracted/bundle.name/('Contents/MacOS/Hermes' if target=='darwin' else 'Hermes.exe' if target=='win32' else 'Hermes')
    smoke_env=clean_environment(WORK/'smoke',pin)
    smoke_env.pop('GITHUB_SHA',None);smoke_env.pop('GITHUB_REF_NAME',None)
    if target=='linux' and os.environ.get('GITHUB_ACTIONS')=='true':
        # Chromium's singleton Unix socket must fit sun_path (108 bytes).
        # GitHub's doubled repo checkout path can exceed it even in a fresh home.
        short_tmp=tempfile.mkdtemp(prefix='hs-',dir=os.environ['RUNNER_TEMP'])
        smoke_env.update({name:short_tmp for name in ('TMPDIR','TMP','TEMP')})
        print('Private short Linux smoke temp:',short_tmp,flush=True)
    command=[node,str(ROOT/'scripts/smoke.mjs'),str(src),str(binary),str(WORK/'smoke/h'),str(logs)]
    if target=='linux' and shutil.which('strace'):
        command=[shutil.which('strace'),'-f','-e','trace=process,network,%file','-s','200','-o',str(logs/'native-strace.log')]+command
    try:
        run('native-smoke',command,ROOT,smoke_env,logs)
    finally:
        runtime_logs=WORK/'smoke/h/.hermes/logs'
        for name in ('desktop.log','gui.log'):
            p=runtime_logs/name
            if p.is_file():shutil.copyfile(p,logs/('runtime-'+name))
    if target=='linux':
        report=logs/'vitest.json'
        env['DESKTOP_TEST_RECEIPT']=str(logs/'full-completion.json')
        rc=cmd('upstream-full-tests',npm+['test','--','--maxWorkers=4','--reporter=default','--reporter=json','--outputFile.json='+str(report)]+reporter,desktop,True)
        data=json.loads(report.read_text());data['runCompletion']=json.loads((logs/'full-completion.json').read_text())
        gate=gate_test_report(data,pin,rc)
        (logs/'test-gate.json').write_text(json.dumps(gate,indent=2)+'\n')
        print('FULL SUITE (exceptions are explicit):',json.dumps(gate),flush=True)
    env.pop('DESKTOP_TEST_RECEIPT',None)
    status=subprocess.check_output([git,'status','--porcelain','--untracked-files=no'],cwd=src,env=env,text=True)
    if status:raise RuntimeError('Tracked upstream files changed: '+status)
    manifest={'version':version,'upstream':pin,'platform':target,'arch':arch,'electron':pkg['build']['electronVersion'],
              'archive':archive.name,'sha256':digest(archive),'bytes':archive.stat().st_size,
              'sourceClean':True,'archiveRoundtrip':True,'nativeSmoke':json.loads((logs/'smoke.json').read_text()),
              'fullSuite':gate,'targetedSuite':targeted_gate,'signing':'No Developer ID/Authenticode signing or Apple notarization',
              'limitations':['No real remote credentials/login/chat tested','No quarantined download/Gatekeeper or SmartScreen acceptance test','No microphone/camera/screen/TCC end-to-end test','Native full UI suite runs on Linux only']}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(manifest['sha256']+'  '+archive.name+'\n')
    print('VERIFIED DISTRIBUTION:',json.dumps(manifest),flush=True)

if __name__=='__main__':main()
