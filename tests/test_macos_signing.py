"""Release-policy regression tests; fixture bytes are never application payloads."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import load_pin, release_version
from release import verify_distribution
from macos_signing import validate_signing_receipt, require_resource_seal, is_missing_seal_rejection
from source_patches import patch_set


def valid_receipt():
    return {
        'schema': 1, 'mode': 'ad-hoc', 'developerID': False, 'notarized': False,
        'stagedNativeCount': 3, 'bundleVerified': True, 'archiveVerified': True,
        'nativePayloadsVerified': 3, 'tamperRejected': True, 'missingSealRejected': True,
        'gatekeeper': {'enabled': True, 'quarantinePresent': True, 'accepted': False,
                       'exitCode': 3, 'assessment': 'unnotarized-ad-hoc'},
    }


def fixture(directory):
    pin = load_pin()
    name = f'Hermes-{release_version(pin)}-darwin-arm64-adhoc.zip'
    archive = directory / name
    archive.write_bytes(b'unit-test-only: not an installable archive')
    manifest = {
        'platform': 'darwin', 'arch': 'arm64', 'upstream': pin,
        'version': release_version(pin), 'sourceClean': False, 'sourceVerified': True, 'archiveRoundtrip': True,
        'sourcePatch': {'schema': 1, 'verified': True, 'upstreamCommit': pin['commit'],
                        'upstreamTree': '1' * 40, 'patchedTree': '2' * 40, **patch_set()},
        'archive': name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'bytes': archive.stat().st_size, 'targetedSuite': {'releaseGatePassed': True},
        'nativeSmoke': {
            'platform': 'darwin', 'arch': 'arm64', 'firstRun': True, 'remoteForm': True,
            'remoteSetupDirect': True, 'localInstallOfferAbsent': True,
            'unreachableRemoteBlocksApply': True, 'noAgentCheckout': True,
            'errors': [], 'localInstallStarted': False,
            'ptyResult': {'exitCode': 0, 'output': 'HERMES_NATIVE_PTY_OK'},
        },
    }
    return pin, manifest


class MacReleaseRegressionTests(unittest.TestCase):
    def test_publication_requires_verified_patch_and_remote_ui_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pin, valid = fixture(directory)
            valid['macSigning'] = valid_receipt()
            changes = [
                lambda m: m.update(sourceClean=True),
                lambda m: m.update(sourceVerified=1),
                lambda m: m.pop('sourcePatch'),
                lambda m: m['sourcePatch'].update(manifestSha256='0' * 64),
                lambda m: m['nativeSmoke'].update(remoteSetupDirect=False),
                lambda m: m['nativeSmoke'].update(localInstallOfferAbsent=1),
            ]
            for change in changes:
                manifest = copy.deepcopy(valid)
                change(manifest)
                (directory / 'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    verify_distribution(directory, pin, ('darwin', 'arm64'))

    def test_missing_seal_rejection_accepts_actual_macos_diagnostics_not_tool_failures(self):
        for diagnostic in (
            'code has no resources but signature indicates they must be present',
            'invalid resource directory (directory or signature have been modified)',
        ):
            self.assertTrue(is_missing_seal_rejection(1, 'Hermes.app: ' + diagnostic))
            for wrong_exit in (0, 2, 3, 127, -9):
                self.assertFalse(is_missing_seal_rejection(wrong_exit, diagnostic))
        for other in ('codesign: command not found', 'No such file or directory', 'valid on disk', ''):
            self.assertFalse(is_missing_seal_rejection(1, other))

    def test_valid_receipt_is_explicitly_not_gatekeeper_acceptance(self):
        receipt = validate_signing_receipt(valid_receipt())
        self.assertFalse(receipt['gatekeeper']['accepted'])
        self.assertFalse(receipt['notarized'])

    def test_missing_or_truthy_proofs_never_pass(self):
        valid = valid_receipt()
        for key in valid:
            for bad in (None, True, False, 'true', 0, 1, []):
                if type(bad) is type(valid[key]) and bad == valid[key]:
                    continue
                receipt = copy.deepcopy(valid)
                receipt[key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    validate_signing_receipt(receipt)
        for key in valid['gatekeeper']:
            receipt = copy.deepcopy(valid)
            del receipt['gatekeeper'][key]
            with self.subTest(gatekeeper=key), self.assertRaises(ValueError):
                validate_signing_receipt(receipt)

    def test_missing_resource_seal_is_detected_before_invoking_codesign(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'Hermes.app'
            seal = app / 'Contents/_CodeSignature/CodeResources'
            with self.assertRaisesRegex(ValueError, 'resource seal missing'):
                require_resource_seal(app)
            seal.parent.mkdir(parents=True)
            seal.write_bytes(b'')
            with self.assertRaises(ValueError):
                require_resource_seal(app)
            seal.write_bytes(b'unit-test existence fixture, not a signature')
            self.assertEqual(require_resource_seal(app), seal)

    def test_macos_manifest_with_all_gates_can_proceed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pin, manifest = fixture(directory)
            manifest['macSigning'] = valid_receipt()
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(verify_distribution(directory, pin, ('darwin', 'arm64')), manifest)

    def test_macos_release_without_signature_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pin, manifest = fixture(directory)
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'Mac signing'):
                verify_distribution(directory, pin, ('darwin', 'arm64'))


if __name__ == '__main__':
    unittest.main()
