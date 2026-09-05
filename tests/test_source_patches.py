"""Real disposable Git fixtures; these tests never produce a release artifact."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from source_patches import apply_patches, verify_source_state, validate_patch_receipt


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / 'src'
        self.src.mkdir()
        self.patchdir = self.root / 'patches'
        self.patchdir.mkdir()
        self.git('init', '--initial-branch=fixture')
        self.git('config', 'core.autocrlf', 'false')
        self.file = self.src / 'apps/desktop/src/panel.ts'
        self.file.parent.mkdir(parents=True)
        self.file.write_text('export const localInstallVisible = true\n', newline='\n')
        (self.src / '.gitignore').write_text('dist/\nnode_modules/\n', newline='\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
        self.pin = {'commit': self.git('rev-parse', 'HEAD').strip()}
        self.file.write_text('export const localInstallVisible = false\n', newline='\n')
        patch = self.git('diff', '--binary', '--full-index', '--no-ext-diff', 'HEAD').encode()
        (self.patchdir / 'ui.patch').write_bytes(patch)
        self.git('restore', '.')
        self.manifest = {'schema': 1, 'patches': [{'file': 'ui.patch', 'sha256': hashlib.sha256(patch).hexdigest()}]}
        self.write_manifest()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.src), *args], text=True, encoding='utf-8', stderr=subprocess.STDOUT)

    def write_manifest(self):
        (self.patchdir / 'manifest.json').write_text(json.dumps(self.manifest) + '\n')

    def apply(self):
        return apply_patches(self.src, self.pin, self.patchdir)

    def test_patch_is_applied_and_exact_result_remains_verified(self):
        receipt = self.apply()
        self.assertIn('false', self.file.read_text())
        self.assertNotEqual(receipt['upstreamTree'], receipt['patchedTree'])
        self.assertIs(receipt['verified'], True)
        verify_source_state(self.src, self.pin, receipt, self.patchdir)
        validate_patch_receipt(receipt, self.pin, self.patchdir)
        # Normal ignored build output is permitted, unrelated source isn't.
        (self.src / 'dist').mkdir()
        (self.src / 'dist/app.js').write_text('build output')
        verify_source_state(self.src, self.pin, receipt, self.patchdir)

    def test_unknown_tracked_untracked_and_index_changes_are_rejected(self):
        receipt = self.apply()
        self.file.write_text('unexpected source change\n')
        with self.assertRaises(ValueError):
            verify_source_state(self.src, self.pin, receipt, self.patchdir)
        self.git('add', '.')
        with self.assertRaises(ValueError):
            verify_source_state(self.src, self.pin, receipt, self.patchdir)
        self.git('restore', '--source=HEAD', '--staged', '--worktree', '.')
        receipt = self.apply()
        (self.src / 'unexpected.ts').write_text('extra source')
        with self.assertRaises(ValueError):
            verify_source_state(self.src, self.pin, receipt, self.patchdir)

    def test_dirty_initial_tree_and_patch_conflict_stop_without_success_receipt(self):
        (self.src / 'unexpected.ts').write_text('dirty')
        with self.assertRaises(ValueError):
            self.apply()
        (self.src / 'unexpected.ts').unlink()
        self.file.write_text('incompatible upstream\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'changed upstream')
        self.pin['commit'] = self.git('rev-parse', 'HEAD').strip()
        with self.assertRaises(ValueError):
            self.apply()

    def test_hash_corruption_is_rejected_before_source_is_changed(self):
        (self.patchdir / 'ui.patch').write_text('corrupted patch')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertIn('true', self.file.read_text())

    def test_receipt_requires_exact_patch_set_pin_and_literal_verified_flag(self):
        receipt = self.apply()
        variants = []
        for key, value in [('verified', 1), ('schema', True), ('upstreamCommit', 'b' * 40),
                           ('manifestSha256', 'c' * 64), ('patchedTree', 'malformed'),
                           ('upstreamTree', receipt['patchedTree']), ('patches', [])]:
            bad = copy.deepcopy(receipt)
            bad[key] = value
            variants.append(bad)
        for bad in variants:
            with self.subTest(receipt=bad), self.assertRaises(ValueError):
                validate_patch_receipt(bad, self.pin, self.patchdir)

    def test_manifest_rejects_unsafe_names_duplicates_and_nonliteral_schema(self):
        original = copy.deepcopy(self.manifest)
        for name in ('../ui.patch', '/ui.patch', 'ui.patch\n', 'ui.patch;id'):
            self.manifest = copy.deepcopy(original)
            self.manifest['patches'][0]['file'] = name
            self.write_manifest()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.apply()
        for schema in (True, '1', 2):
            self.manifest = copy.deepcopy(original)
            self.manifest['schema'] = schema
            self.write_manifest()
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                self.apply()
        self.manifest = copy.deepcopy(original)
        self.manifest['patches'] *= 2
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.apply()


if __name__ == '__main__':
    unittest.main()
