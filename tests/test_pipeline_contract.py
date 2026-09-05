"""Wiring checks complement real Git patch tests and native CI receipts."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PipelineContractTests(unittest.TestCase):
    def test_patch_is_applied_before_dependency_code_and_verified_after_tests(self):
        source = (ROOT / 'scripts/build.py').read_text()
        self.assertLess(source.index('source_patch=apply_patches('), source.index("cmd('npm-ci'"))
        self.assertLess(source.index("cmd('upstream-full-tests'"), source.index('verify_source_state('))
        self.assertIn("'sourceClean':False,'sourceVerified':True,'sourcePatch':source_patch", source)
        for test in ('fleet-rail.test.ts', 'profile-rail-fleet.test.tsx',
                     'desktop-install-overlay.test.tsx', 'connections-registry.test.tsx'):
            self.assertIn(test, source)

    def test_native_build_is_read_only_and_release_requires_verified_main(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        publish = (ROOT / '.github/workflows/release.yml').read_text()
        self.assertIn('contents: read', build)
        self.assertNotIn('contents: write', build)
        self.assertIn('persist-credentials: false', build)
        self.assertIn('EXPECTED_SHA: ${{ inputs.expected_sha }}', build)
        self.assertIn('workflow_run:', publish)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", publish)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", publish)
        self.assertIn('github.event.workflow_run.head_repository.full_name == github.repository', publish)
        self.assertIn('scripts/ci_receipt.py --run "$BUILD_RUN"', publish)
        for text in (build, publish):
            self.assertNotIn('pull_request_target:', text)
        self.assertIn('*.patch -text', (ROOT / '.gitattributes').read_text())


if __name__ == '__main__':
    unittest.main()
