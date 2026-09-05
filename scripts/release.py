#!/usr/bin/env python3
"""Prepare an immutable release ONLY from all four verified CI distributions."""
import argparse
import json
from pathlib import Path
import re
import shutil

from common import load_pin, release_version
from package import digest

REPO='frankhommers/hermes-desktop-builds'
TARGETS=[('darwin','arm64'),('darwin','x64'),('win32','x64'),('linux','x64')]


def cask_text(version, assets):
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',version):raise ValueError('Bad cask version')
    blocks=[]
    for arch,condition in [('arm64','on_arm'),('x64','on_intel')]:
        asset=assets['darwin-'+arch]
        filename=f'Hermes-{version}-darwin-{arch}-unsigned.zip'
        if asset['archive']!=filename or not re.fullmatch('[0-9a-f]{64}',asset['sha256']):raise ValueError('Bad cask asset')
        blocks.append(f'''  {condition} do
    sha256 "{asset['sha256']}"
    url "https://github.com/{REPO}/releases/download/v#{{version}}/Hermes-#{{version}}-darwin-{arch}-unsigned.zip"
  end''')
    return f'''cask "hermes-desktop" do
  version "{version}"

{chr(10).join(blocks)}

  name "Hermes Desktop"
  desc "Standalone Hermes Electron Desktop for remote backends"
  homepage "https://github.com/{REPO}"

  depends_on macos: ">= :monterey"
  app "Hermes.app"

  caveats <<~EOS
    Unsigned community build; not Apple-notarized. Gatekeeper remains enabled.
    Choose "Connect to existing Hermes", not "Install Hermes locally".
    An existing local Hermes runtime may be discovered and started by upstream.
    Review existing installations before launching if local startup must be avoided.
    No Python agent is installed by this cask. Updates use brew upgrade, not the in-app updater.
  EOS
end
'''


def verify_distribution(directory, pin, target):
    manifest=json.loads((directory/'manifest.json').read_text())
    platform,arch=target
    if (manifest['platform'],manifest['arch'])!=target:raise ValueError('Mismatched artifact target')
    if manifest['upstream']!=pin or manifest['version']!=release_version(pin):raise ValueError('Mismatched source/version pin')
    if not manifest['sourceClean'] or not manifest['archiveRoundtrip']:raise ValueError('Incomplete verification')
    smoke=manifest['nativeSmoke']
    for flag in ('firstRun','remoteForm','unreachableRemoteBlocksApply','noAgentCheckout'):
        if smoke.get(flag) is not True:raise ValueError('Missing smoke gate: '+flag)
    if smoke['platform']!=platform or smoke['arch']!=arch or smoke['errors'] or smoke['localInstallStarted']:
        raise ValueError('Wrong/failed native smoke')
    if smoke['ptyResult']['exitCode']!=0 or 'HERMES_NATIVE_PTY_OK' not in smoke['ptyResult']['output']:raise ValueError('Native PTY failed')
    suffix='.tar.gz' if platform=='linux' else '.zip'
    filename=f'Hermes-{release_version(pin)}-{platform}-{arch}-unsigned{suffix}'
    if manifest['archive']!=filename:raise ValueError('Unexpected archive name')
    archive=directory/filename
    if digest(archive)!=manifest['sha256'] or archive.stat().st_size!=manifest['bytes']:raise ValueError('Artifact hash/size mismatch')
    if platform=='linux' and not manifest['fullSuite']['releaseGatePassed']:raise ValueError('Full-suite gate missing')
    return manifest


def prepare(downloads,destination,run_url):
    if not re.fullmatch(r'https://github.com/frankhommers/hermes-desktop-builds/actions/runs/[0-9]+',run_url):raise ValueError('Invalid run URL')
    if destination.exists():raise ValueError('Refusing to overwrite a release directory')
    pin=load_pin();version=release_version(pin)
    manifests={}
    sources={}
    for target in TARGETS:
        label='-'.join(target)
        candidates=list(downloads.glob(f'desktop-{label}/manifest.json'))
        if len(candidates)!=1:raise ValueError('Expected exactly one verified artifact for '+label)
        sources[label]=candidates[0].parent
        manifests[label]=verify_distribution(candidates[0].parent,pin,target)
    if len(manifests)!=len(TARGETS):raise ValueError('Missing target')
    destination.mkdir(parents=True)
    checks=[]
    for label,m in manifests.items():
        shutil.copyfile(sources[label]/m['archive'],destination/m['archive'])
        checks.append(m['sha256']+'  '+m['archive'])
        shutil.make_archive(str(destination/(label+'-evidence')),'zip',sources[label]/'logs')
    payload={'schema':1,'buildRepository':REPO,'version':version,'upstream':pin,'buildRun':run_url,'targets':manifests}
    (destination/'release-manifest.json').write_text(json.dumps(payload,indent=2)+'\n')
    (destination/'SHA256SUMS').write_text('\n'.join(checks)+'\n')
    (destination/'hermes-desktop.rb').write_text(cask_text(version,manifests))
    full=manifests['linux-x64']['fullSuite']
    notes=f'''# Hermes Desktop {version} — community preview

Real, unmodified upstream Electron Desktop, not the local-agent bootstrap installer.
Source: https://github.com/{pin['repository']}/commit/{pin['commit']}
Build and native starttest evidence: {run_url}

All four distributions were built and launched on matching native runners:
macOS arm64 + x64, Windows x64 and Linux x64. Extracted-app first-run remote UI,
inactive local bootstrap, refused unreachable remote, and real native PTY were exercised.
No Python runtime, agent checkout or credentials are bundled.

**Unsigned preview: no Apple Developer ID/notarization or Windows Authenticode.**
Native CI launch is NOT a quarantined-download/Gatekeeper/SmartScreen acceptance test.
No live remote login/chat, user peripherals or OS permissions were end-to-end tested.
Do not disable system security to install this build.

Full upstream suite (Linux): {full['total']} total, {full['passed']} passed,
{full['failed']} failed, {full['pending']} pending/skipped. Full suite green: {full['suiteGreen']}.
Known exceptions, if any, remain listed in release-manifest.json and raw evidence ZIPs.
Targeted startup/packaging tests and typechecking passed on each native host.

On a clean first start choose **Connect to existing Hermes**. Do not choose the local
installer. This is not a hard-locked remote-only fork: existing local runtimes can be
discovered/started by upstream; review them before launching if that must be avoided.

Installation, rollback and security notes: https://github.com/{REPO}#downloads-and-installation
Verify SHA256SUMS. Immutable version: assets under this tag must never be replaced.
'''
    (destination/'RELEASE-NOTES.md').write_text(notes)
    return payload

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--downloads',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--run-url',required=True)
    a=p.parse_args();result=prepare(a.downloads,a.destination,a.run_url)
    print('Release prepared (not published):',result['version'],list(result['targets']))
