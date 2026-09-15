"""Pure harness contracts, not macOS acceptance evidence."""
import unittest
from pathlib import Path
import run_native as r

class Contracts(unittest.TestCase):
    def test_environment_does_not_leak_build_identity_or_credentials(self):
        env = r.isolated_env(Path('/tmp/fixture'), {'PATH':'/usr/bin','GITHUB_SHA':'wrong', 'OPENAI_API_KEY':'secret', 'HERMES_HOME':'/real', 'HOME':'/real'})
        self.assertEqual(env['HOME'], '/tmp/fixture/home')
        self.assertEqual(env['HERMES_HOME'], '/tmp/fixture/home/.hermes')
        self.assertNotIn('GITHUB_SHA', env)
        self.assertNotIn('OPENAI_API_KEY', env)
    def test_pending_is_not_release_ready(self):
        result = r.initial_evidence('a'*40, 'arm64')
        self.assertFalse(result['releaseReady'])
        self.assertEqual(result['officialUpdateCycle'], 'not-run')
        self.assertEqual(result['authenticatedVpsChat'], 'not-run')
    def test_workroot_requires_runner_sandbox(self):
        with self.assertRaises(ValueError): r.validate_root(Path('/home/user'), Path('/tmp/runner'))
        with self.assertRaises(ValueError): r.validate_root(Path('/tmp/runner'), Path('/tmp/runner'))
        r.validate_root(Path('/tmp/runner/mainstream-proof'), Path('/tmp/runner'))

if __name__ == '__main__': unittest.main()
