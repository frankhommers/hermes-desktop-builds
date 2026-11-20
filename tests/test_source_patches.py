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
from source_patches import apply_patches, verify_source_state, validate_patch_receipt, _patch_path_is_approved


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
        self.file = self.src / 'apps/desktop/src/components/panel.ts'
        self.file.parent.mkdir(parents=True)
        self.file.write_text('export const localInstallVisible = true\n', newline='\n')
        (self.src / '.gitignore').write_text('dist/\nnode_modules/\n', newline='\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
        self.pin = {'commit': self.git('rev-parse', 'HEAD').strip()}
        self.file.write_text('export const localInstallVisible = false\n', newline='\n')
        patch = self.git('diff', '--binary', '--full-index', '--no-ext-diff', 'HEAD').encode()
        (self.patchdir / 'remote-client-ui.patch').write_bytes(patch)
        self.git('restore', '.')
        self.manifest = {'schema': 1, 'patches': [{'file': 'remote-client-ui.patch', 'sha256': hashlib.sha256(patch).hexdigest()}]}
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
        (self.patchdir / 'remote-client-ui.patch').write_text('corrupted patch')
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
        for name in ('../remote-client-ui.patch', '/remote-client-ui.patch', 'remote-client-ui.patch\n', 'remote-client-ui.patch;id'):
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

    def test_patch_names_have_separate_fail_closed_source_scopes(self):
        self.assertTrue(_patch_path_is_approved(
            'remote-client-ui.patch', 'apps/desktop/src/components/desktop-install-overlay.tsx'))
        self.assertFalse(_patch_path_is_approved(
            'remote-client-ui.patch', 'apps/desktop/electron/main.ts'))
        self.assertTrue(_patch_path_is_approved(
            'homebrew-client-updater.patch', 'apps/desktop/electron/main.ts'))
        self.assertTrue(_patch_path_is_approved(
            'remote-client-ui.patch', 'apps/desktop/src/components/remote-setup/first-run.tsx'))
        self.assertTrue(_patch_path_is_approved(
            'homebrew-client-updater.patch', 'apps/desktop/electron/community-homebrew-update.ts'))
        self.assertFalse(_patch_path_is_approved(
            'homebrew-client-updater.patch', 'apps/desktop/electron/preload.ts'))
        # Retired at v0.23.2 because upstream carries the fix: never re-approved.
        for retired in ('apps/desktop/electron/ssh-connection.ts', 'apps/desktop/src/store/voice-prefs.test.ts'):
            self.assertFalse(_patch_path_is_approved('homebrew-client-updater.patch', retired))
        self.assertFalse(_patch_path_is_approved(
            'upstream-build-fix.patch', 'apps/desktop/src/components/onboarding-chat/cards/setup.tsx'))
        self.assertFalse(_patch_path_is_approved(
            'anything-else.patch', 'apps/desktop/electron/main.ts'))
        # The upstream HUD fixture patch is exactly one test file, nothing else.
        self.assertTrue(_patch_path_is_approved(
            'upstream-hud-fixture-host.patch', 'apps/desktop/scripts/stage-native-deps.test.mjs'))
        for other in ('apps/desktop/scripts/build-hud-modifier-monitor.mjs',
                      'apps/desktop/scripts/stage-native-deps.mjs',
                      'apps/desktop/electron/main.ts'):
            self.assertFalse(_patch_path_is_approved('upstream-hud-fixture-host.patch', other))
        self.assertFalse(_patch_path_is_approved(
            'homebrew-client-updater.patch', 'apps/desktop/scripts/stage-native-deps.test.mjs'))


class CheckedInPatchSetTests(unittest.TestCase):
    """The real pinned patch set: exact names, hashes and per-patch scopes."""

    def test_manifest_hashes_and_order(self):
        from source_patches import PATCH_DIR, patch_set
        rows = patch_set(PATCH_DIR)['patches']
        self.assertEqual([row['file'] for row in rows],
                         ['remote-client-ui.patch', 'homebrew-client-updater.patch', 'upstream-hud-fixture-host.patch'])
        self.assertFalse((PATCH_DIR / 'upstream-build-fix.patch').exists())

    def test_every_checked_in_patch_stays_inside_its_scope(self):
        from source_patches import PATCH_DIR, patch_set
        for row in patch_set(PATCH_DIR)['patches']:
            text = (PATCH_DIR / row['file']).read_text(encoding='utf-8')
            paths = [line.split(' b/', 1)[1] for line in text.splitlines() if line.startswith('diff --git ')]
            self.assertTrue(paths, row['file'])
            for path in paths:
                with self.subTest(patch=row['file'], path=path):
                    self.assertTrue(_patch_path_is_approved(row['file'], path))

    def test_community_identity_never_overwrites_upstream_distribution(self):
        from source_patches import PATCH_DIR
        text = (PATCH_DIR / 'homebrew-client-updater.patch').read_text(encoding='utf-8')
        self.assertIn('communityDistribution: identity', text)
        self.assertNotIn('return { distribution: identity', text)

    def test_windows_sidecar_expectation_is_host_specific_not_relaxed(self):
        from source_patches import PATCH_DIR
        text = (PATCH_DIR / 'homebrew-client-updater.patch').read_text(encoding='utf-8')
        # Windows gets its own exact backslash path; POSIX keeps the exact POSIX path.
        self.assertIn("+      ? '\\\\App\\\\Contents\\\\Resources\\\\install-stamp.json'", text)
        self.assertIn("+      : '/App/Contents/Resources/install-stamp.json'", text)
        self.assertIn('+  assert.deepEqual(seen, [sidecar])', text)
        self.assertNotIn("+  assert.deepEqual(seen, ['/App/Contents/Resources/install-stamp.json'])", text)

    def test_hud_fixture_patch_keeps_both_host_branches_exact(self):
        from source_patches import PATCH_DIR
        text = (PATCH_DIR / 'upstream-hud-fixture-host.patch').read_text(encoding='utf-8')
        added = [line[1:] for line in text.splitlines() if line.startswith('+') and not line.startswith('+++')]
        removed = [line[1:] for line in text.splitlines() if line.startswith('-') and not line.startswith('---')]
        self.assertEqual(removed, ["    assert.match(warnings.join('\\n'), /desktop packaging continues/)"])
        self.assertIn("    if (process.platform === 'linux' && process.arch === 'x64') {", added)
        self.assertIn("      assert.match(warnings.join('\\n'), /desktop packaging continues/)", added)
        self.assertIn("      assert.deepEqual(warnings, ['[hud-modifier] linux-x64 needs a native build; "
                      "modifier tap unavailable for this target'])", added)
        # Test-only: no skip, no try/catch swallowing, and the empty-tree assertion stays.
        self.assertFalse(any('skip' in line or 'catch' in line for line in added))
        self.assertIn("     assert.equal(existsSync(join(distDir, 'native')), false)", text)


if __name__ == '__main__':
    unittest.main()
