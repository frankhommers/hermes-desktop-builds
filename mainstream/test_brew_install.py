"""One-time Homebrew handoff contracts; no real Homebrew or user state."""
import unittest
from pathlib import Path
from unittest.mock import patch
import brew_install as b


class HandoffTests(unittest.TestCase):
    def test_installed_legacy_cask_is_pinned_before_migration(self):
        calls = []
        def run(args):
            calls.append(args[1:])
            if args[1:] == ['list', '--cask', '--full-name']:
                return 'frankhommers/tap/hermes-desktop'
            if args[1:] == ['pin', '--help']:
                return '--cask'
            if args[1:] == ['list', '--cask', '--pinned']:
                return 'hermes-desktop' if ['pin', '--cask', b.LEGACY] in calls else ''
            return ''
        def install(*args, **kwargs):
            self.assertIn(['pin', '--cask', b.LEGACY], calls)
        with patch.object(b.i, 'run', side_effect=run), patch.object(b.i, 'preflight'), patch.object(b.i, 'install', side_effect=install):
            b.handoff(Path('/opt/homebrew/bin/brew'), Path('/Applications/Hermes.app'))
        self.assertNotIn(['unpin', '--cask', b.LEGACY], calls)

    def test_failed_install_restores_preexisting_pin_state(self):
        for pinned in (False, True):
            calls = []
            def run(args):
                calls.append(args[1:])
                if args[1:] == ['list', '--cask', '--full-name']:
                    return b.LEGACY
                if args[1:] == ['pin', '--help']:
                    return '--cask'
                if args[1:] == ['list', '--cask', '--pinned']:
                    return 'hermes-desktop' if pinned or ['pin', '--cask', b.LEGACY] in calls else ''
                return ''
            with self.subTest(pinned=pinned), patch.object(b.i, 'run', side_effect=run), patch.object(b.i, 'preflight'), patch.object(b.i, 'install', side_effect=b.i.Refusal('build failed')):
                with self.assertRaises(b.i.Refusal):
                    b.handoff(Path('/opt/homebrew/bin/brew'), Path('/Applications/Hermes.app'))
            self.assertEqual(['unpin', '--cask', b.LEGACY] in calls, not pinned)

    def test_other_tap_same_token_is_refused(self):
        with patch.object(b.i, 'run', return_value='someone/else/hermes-desktop'), patch.object(b.i, 'preflight'), patch.object(b.i, 'install') as install:
            with self.assertRaises(b.i.Refusal):
                b.handoff(Path('/opt/homebrew/bin/brew'), Path('/Applications/Hermes.app'))
            install.assert_not_called()

    def test_no_legacy_receipt_needs_no_pin(self):
        with patch.object(b.i, 'run', return_value='') as run, patch.object(b.i, 'preflight'), patch.object(b.i, 'install') as install:
            b.handoff(Path('/opt/homebrew/bin/brew'), Path('/Applications/Hermes.app'))
            self.assertEqual(run.call_count, 1)
            install.assert_called_once()


if __name__ == '__main__':
    unittest.main()
