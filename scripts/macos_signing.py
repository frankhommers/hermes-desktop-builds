"""Native macOS signing and release gates; never changes host security policy."""
import json
from pathlib import Path
import sys
import time

from package import native_inventory


REQUIRED_FLAGS = ('bundleVerified', 'archiveVerified', 'tamperRejected', 'missingSealRejected')


def is_missing_seal_rejection(returncode, output):
    # Apple uses a different diagnostic after deleting a newly created resource seal
    # than for the inherited/stale Electron signature in the original broken ZIP.
    diagnostics = (
        'code has no resources but signature indicates they must be present',
        'invalid resource directory (directory or signature have been modified)',
    )
    return returncode == 1 and any(message in output for message in diagnostics)


def validate_signing_receipt(receipt):
    def fail():
        raise ValueError('Mac signing verification evidence missing or invalid')
    if not isinstance(receipt, dict):
        fail()
    if type(receipt.get('schema')) is not int or receipt['schema'] != 1 or receipt.get('mode') != 'ad-hoc':
        fail()
    if receipt.get('developerID') is not False or receipt.get('notarized') is not False:
        fail()
    if any(receipt.get(key) is not True for key in REQUIRED_FLAGS):
        fail()
    count = receipt.get('stagedNativeCount')
    verified = receipt.get('nativePayloadsVerified')
    if type(count) is not int or count < 1 or type(verified) is not int or verified != count:
        fail()
    gate = receipt.get('gatekeeper', {})
    if not isinstance(gate, dict):
        fail()
    if any(gate.get(key) is not True for key in ('enabled', 'quarantinePresent')):
        fail()
    if gate.get('accepted') is not False or type(gate.get('exitCode')) is not int or gate['exitCode'] != 3:
        fail()
    if gate.get('assessment') != 'unnotarized-ad-hoc':
        fail()
    return receipt


def require_resource_seal(bundle):
    seal = bundle / 'Contents/_CodeSignature/CodeResources'
    if not seal.is_file() or seal.stat().st_size == 0:
        raise ValueError('Mac signing resource seal missing: ' + str(seal))
    return seal


def sign_staged_payloads(desktop, arch, cmd):
    """Sign native bytes BEFORE electron-builder computes ASAR integrity hashes."""
    if sys.platform != 'darwin':
        raise RuntimeError('Mac signing must run on a native Mac')
    stage = desktop / 'dist'
    natives = native_inventory(stage, 'darwin', arch)
    for index, native in enumerate(natives):
        binary = stage / native['path']
        cmd(f'mac-stage-sign-{index}', ['/usr/bin/codesign', '--force', '--sign', '-',
                                      '--timestamp=none', str(binary)])
        cmd(f'mac-stage-verify-{index}', ['/usr/bin/codesign', '--verify', '--strict', str(binary)])
    return len(natives)


def verify_bundle(bundle, arch, cmd, label):
    require_resource_seal(bundle)
    cmd(label, ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(bundle)])
    # --deep covers the standard framework/helper nesting but not every Resources binary.
    payload = bundle / 'Contents/Resources/app.asar.unpacked'
    natives = native_inventory(payload, 'darwin', arch)
    for index, native in enumerate(natives):
        cmd(f'{label}-payload-{index}', ['/usr/bin/codesign', '--verify', '--strict',
                                        '--verbose=2', str(payload / native['path'])])
    return len(natives)


def verify_extracted(bundle, arch, staged_count, cmd, logs):
    """Real positive/negative checks against the extracted distribution after UI smoke."""
    if sys.platform != 'darwin':
        raise RuntimeError('Mac verification must run on a native Mac')
    count = verify_bundle(bundle, arch, cmd, 'mac-archive-verify')
    if count != staged_count:
        raise ValueError('Mac native payload count changed during packaging')
    cmd('mac-signature-details', ['/usr/bin/codesign', '--display', '--verbose=4', str(bundle)])
    if 'Signature=adhoc' not in (logs / 'mac-signature-details.log').read_text():
        raise ValueError('Expected explicit ad-hoc signature')

    # These mutations occur in the extracted test copy, NEVER in the release ZIP.
    resource = bundle / 'Contents/Resources/LICENSE.hermes.txt'
    original = resource.read_bytes()
    try:
        resource.write_bytes(original + b'\nMAC_SIGNING_NEGATIVE_TEST\n')
        rc = cmd('mac-tamper-rejected', ['/usr/bin/codesign', '--verify', '--deep', '--strict',
                                       '--verbose=2', str(bundle)], allow_failure=True)
        if rc != 1 or 'a sealed resource is missing or invalid' not in (logs / 'mac-tamper-rejected.log').read_text():
            raise ValueError('Mac signature failed to detect modified resource')
    finally:
        resource.write_bytes(original)
    verify_bundle(bundle, arch, cmd, 'mac-tamper-restored')

    seal = require_resource_seal(bundle)
    original = seal.read_bytes()
    try:
        seal.unlink()
        rc = cmd('mac-missing-seal-rejected', ['/usr/bin/codesign', '--verify', '--deep', '--strict',
                                             '--verbose=2', str(bundle)], allow_failure=True)
        if not is_missing_seal_rejection(rc, (logs / 'mac-missing-seal-rejected.log').read_text()):
            raise ValueError('Mac signature failed to reject the missing resource seal')
    finally:
        seal.write_bytes(original)
    verify_bundle(bundle, arch, cmd, 'mac-seal-restored')

    # Record the remaining distribution-trust boundary, rather than hiding it.
    cmd('mac-gatekeeper-status', ['/usr/sbin/spctl', '--status'])
    if 'assessments enabled' not in (logs / 'mac-gatekeeper-status.log').read_text():
        raise ValueError('Gatekeeper must remain enabled during validation')
    quarantine = f'0083;{int(time.time()):x};HermesBuildCI;'
    cmd('mac-quarantine-set', ['/usr/bin/xattr', '-w', 'com.apple.quarantine', quarantine, str(bundle)])
    cmd('mac-quarantine-read', ['/usr/bin/xattr', '-p', 'com.apple.quarantine', str(bundle)])
    if quarantine not in (logs / 'mac-quarantine-read.log').read_text():
        raise ValueError('Quarantine attribute not present')
    rc = cmd('mac-gatekeeper-assessment', ['/usr/sbin/spctl', '--assess', '--type', 'execute',
                                         '--verbose=4', str(bundle)], allow_failure=True)
    assessment = (logs / 'mac-gatekeeper-assessment.log').read_text()
    if rc != 3 or 'rejected' not in assessment or 'code has no resources' in assessment:
        raise ValueError('Expected Gatekeeper rejection for unnotarized ad-hoc code, not damage')
    receipt = {
        'schema': 1, 'mode': 'ad-hoc', 'developerID': False, 'notarized': False,
        'stagedNativeCount': staged_count, 'bundleVerified': True, 'archiveVerified': True,
        'nativePayloadsVerified': count, 'tamperRejected': True, 'missingSealRejected': True,
        'gatekeeper': {'enabled': True, 'quarantinePresent': True, 'accepted': False,
                       'exitCode': rc, 'assessment': 'unnotarized-ad-hoc'},
    }
    validate_signing_receipt(receipt)
    (logs / 'mac-signing.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    import argparse
    import os
    from common import ROOT, run
    parser = argparse.ArgumentParser(description='Sign final staged Mac native dependencies')
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--arch', choices=['arm64', 'x64'], required=True)
    parser.add_argument('--logs', type=Path, required=True)
    args = parser.parse_args()
    def command(label, argv):
        return run(label, argv, ROOT, os.environ.copy(), args.logs)
    count = sign_staged_payloads(args.stage, args.arch, command)
    (args.logs / 'mac-staged-native-count.json').write_text(json.dumps({'count': count}) + '\n')
