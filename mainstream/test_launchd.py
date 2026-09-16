"""Regression cases for third-party launchd files; never use live launchd."""
import contextlib
import io
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import installer as i


class LaunchdPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_malformed_battery_file_warns_without_modifying_it(self):
        p = self.folder/'battery.plist'
        p.write_bytes(b'not a launchd plist; private-contents')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            i.inspect_launchd_plists([self.folder])
        self.assertIn('battery.plist', err.getvalue())
        self.assertIn('InvalidFileException', err.getvalue())
        self.assertNotIn('private-contents', err.getvalue())
        self.assertEqual(p.read_bytes(), b'not a launchd plist; private-contents')

    def test_protected_other_vendor_file_warns_not_chmod(self):
        p = self.folder/'com.maintain.PurgeInactiveMemory.plist'
        p.write_bytes(b'private vendor configuration')
        original_mode = p.stat().st_mode
        err = io.StringIO()
        with patch.object(Path, 'read_bytes', side_effect=PermissionError(13, 'denied')), contextlib.redirect_stderr(err):
            i.inspect_launchd_plists([self.folder])
        self.assertIn(p.name, err.getvalue())
        self.assertIn('PermissionError', err.getvalue())
        self.assertEqual(p.stat().st_mode, original_mode)
        self.assertEqual(p.read_bytes(), b'private vendor configuration')

    def test_known_hermes_filename_blocks_before_read(self):
        p = self.folder/'ai.hermes.gateway.plist'
        p.write_bytes(b'invalid')
        with patch.object(Path, 'read_bytes') as read, self.assertRaisesRegex(i.Refusal, 'ai.hermes.gateway.plist'):
            i.inspect_launchd_plists([self.folder])
        read.assert_not_called()

    def test_hidden_hermes_reference_in_readable_vendor_file_blocks(self):
        p = self.folder/'arbitrary-label.plist'
        p.write_bytes(plistlib.dumps({'Label':'arbitrary-label','ProgramArguments':['python','-m','hermes_cli.main','gateway','run']}))
        with self.assertRaisesRegex(i.Refusal, 'arbitrary-label.plist'):
            i.inspect_launchd_plists([self.folder])

    def test_malformed_file_with_hermes_reference_still_blocks(self):
        p = self.folder/'broken-job.plist'
        p.write_bytes(b'broken XML /somewhere/hermes gateway run')
        with self.assertRaises(i.Refusal):
            i.inspect_launchd_plists([self.folder])

    def test_io_failure_is_not_misreported_as_vendor_permissions(self):
        p = self.folder/'third-party.plist'
        p.touch()
        with patch.object(Path, 'read_bytes', side_effect=OSError(5, 'I/O error')), self.assertRaisesRegex(i.Refusal, 'third-party.plist'):
            i.inspect_launchd_plists([self.folder])

    def test_valid_unrelated_file_is_untouched_and_quiet(self):
        p = self.folder/'third-party.plist'
        data = plistlib.dumps({'Label':'example.job','ProgramArguments':['/usr/bin/true']})
        p.write_bytes(data)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            i.inspect_launchd_plists([self.folder])
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(p.read_bytes(), data)

    def test_real_preflight_still_calls_scanner_and_loaded_service_check(self):
        with patch.object(i, 'inspect_launchd_plists') as scan, patch.object(i, 'run', side_effect=['', 'loaded ai.hermes.gateway']) as run:
            with self.assertRaises(i.Refusal):
                i.check_closed_and_services(Path('/synthetic-home'))
        scan.assert_called_once_with((Path('/synthetic-home/Library/LaunchAgents'),Path('/Library/LaunchAgents'),Path('/Library/LaunchDaemons')))
        self.assertEqual(run.call_count, 2)

    def test_failed_loaded_service_query_is_not_ignored(self):
        with patch.object(i, 'inspect_launchd_plists'), patch.object(i, 'run', side_effect=['', i.Refusal('launchctl failed')]):
            with self.assertRaisesRegex(i.Refusal, 'launchctl failed'):
                i.check_closed_and_services(Path('/synthetic-home'))

if __name__ == '__main__': unittest.main()
