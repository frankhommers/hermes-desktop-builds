"""Real install()/rename fixtures; external macOS/build tools are mocked."""
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installer as i


class InstallBackupTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.appdir = self.home/'Applications'
        self.app = self.appdir/'Hermes.app'
        self.app.mkdir(parents=True)
        self.old = self.app/'signed-bundle'
        self.old.write_bytes(b'old signature and contents')
        self.old.chmod(0o755)
        (self.app/'bundle-link').symlink_to('signed-bundle')
        self.old_stat = self.old.stat()
        self.userdata = self.home/'Library/Application Support/Hermes'
        self.userdata.mkdir(parents=True)
        (self.userdata/'Cookies').write_bytes(b'opaque original cookie fixture')
        self.root = self.home/'.hermes/hermes-agent'
        self.artifact = self.root/'apps/desktop/release/mac-arm64/Hermes.app'
        self.artifact.mkdir(parents=True)
        (self.artifact/'signed-bundle').write_bytes(b'new signature and contents')
        (self.home/'.hermes/auth.json').write_bytes(b'opaque original auth fixture')
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.stack.enter_context(patch.object(i, '_command_log', None))
        self.stack.enter_context(patch.object(i, 'preflight', return_value=('saved', 'route')))
        self.stack.enter_context(patch.object(i, 'verify_repo'))
        self.stack.enter_context(patch.object(i, 'audit_storage'))
        self.commands = self.stack.enter_context(patch.object(i, 'run', side_effect=self.run_command))
        self.stage_copy = self.stack.enter_context(patch.object(i, 'stage_app', side_effect=self.stage_app))
        self.verify = self.stack.enter_context(patch.object(i, 'verify_app', side_effect=self.verify_app))
        self.previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.previous_umask)

    def run_command(self, args, **kwargs):
        if args[1:3] == ['-m', 'venv']:
            cli = Path(args[3])/'bin/hermes'
            cli.parent.mkdir(parents=True)
            cli.touch()
        return ''

    def stage_app(self, source, stage):
        shutil.copytree(source, stage, symlinks=True)
        self.verify_app(stage)

    def verify_app(self, app):
        self.assertEqual((app/'signed-bundle').read_bytes(), b'new signature and contents')

    def install(self):
        i.install(self.userdata, self.app, confirmed=True)

    def backup(self):
        paths = list((self.home/'.hermes/mainstream-backups').glob('migration-*'))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def assert_old_bundle(self, app):
        retained = app/'signed-bundle'
        self.assertEqual(retained.read_bytes(), b'old signature and contents')
        self.assertEqual(retained.stat().st_ino, self.old_stat.st_ino)
        self.assertEqual(retained.stat().st_mode, self.old_stat.st_mode)
        self.assertEqual(retained.stat().st_mtime_ns, self.old_stat.st_mtime_ns)
        self.assertTrue((app/'bundle-link').is_symlink())
        self.assertEqual(os.readlink(app/'bundle-link'), 'signed-bundle')

    def test_install_keeps_only_new_app_in_appdir_and_private_old_bundle(self):
        self.install()
        # Regression must exercise the actual install() destination decision,
        # not just supply a corrected backup argument to swap_app().
        self.assertEqual(sorted(p.name for p in self.appdir.glob('*.app')), ['Hermes.app'])
        self.verify_app(self.app)
        backup = self.backup()
        old = backup/'old-app.noindex/Hermes.app'
        self.assert_old_bundle(old)
        for p in (backup, old.parent):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700)
        for source, saved in ((self.userdata/'Cookies', backup/'userData/Cookies'),
                              (self.home/'.hermes/auth.json', backup/'auth.json')):
            self.assertEqual(source.read_bytes(), saved.read_bytes())
        self.assertIn(str(self.app), self.output.getvalue())
        self.assertIn(str(old), self.output.getvalue())
        stage = self.stage_copy.call_args.args[1]
        self.assertTrue(stage.parent.name.startswith('.hermes-migration-'))
        self.assertTrue(stage.parent.name.endswith('.noindex'))

    def assert_rolled_back(self):
        self.assert_old_bundle(self.app)
        self.assertEqual(list(self.appdir.glob('*.app')), [self.app])
        self.assertFalse((self.backup()/'old-app.noindex/Hermes.app').exists())
        self.assertNotIn('Installed without launching:', self.output.getvalue())
        if self.stage_copy.called:
            stage = self.stage_copy.call_args.args[1]
            self.verify_app(stage)
            self.assertTrue(stage.parent.name.startswith('.hermes-migration-'))
            self.assertTrue(stage.parent.name.endswith('.noindex'))

    def record_registration(self, failures=()):
        events = []
        def run(args, **kwargs):
            if args[0] == i.LSREGISTER:
                self.assertEqual(len(args), 3)
                self.assertEqual(args[2], self.app)
                self.assertIn(args[1], ('-u', '-f'))
                events.append((args[1], (self.app/'signed-bundle').read_bytes()))
                if len(events) in failures:
                    raise i.Refusal('injected LaunchServices failure')
            return self.run_command(args, **kwargs)
        self.commands.side_effect = run
        return events

    def test_install_unregisters_old_and_registers_verified_new_exact_path(self):
        events = self.record_registration()
        def verify(path):
            if path == self.app:
                self.assertEqual(events, [('-u', b'old signature and contents')])
            self.verify_app(path)
        self.verify.side_effect = verify
        self.install()
        self.assertEqual(events, [('-u', b'old signature and contents'),
                                  ('-f', b'new signature and contents')])

    def test_post_swap_verification_failure_and_interrupt_restore_old(self):
        # Each subcase needs a fresh source/venv and migration directory.
        for exception in (i.Refusal('post-swap verification failed'), KeyboardInterrupt()):
            with self.subTest(exception=type(exception).__name__):
                events = self.record_registration()
                def verify(path):
                    if path == self.app:
                        raise exception
                    self.verify_app(path)
                self.verify.side_effect = verify
                with self.assertRaises(type(exception)):
                    self.install()
                self.assert_rolled_back()
                self.assertEqual(events, [('-u', b'old signature and contents'),
                                          ('-f', b'old signature and contents')])
                shutil.rmtree(self.root/'venv')
                shutil.rmtree(self.home/'.hermes/mainstream-backups')

    def test_unregister_failure_never_moves_old_or_reports_success(self):
        events = self.record_registration(failures={1})
        with self.assertRaisesRegex(i.Refusal, 'injected LaunchServices failure'):
            self.install()
        self.assert_rolled_back()
        self.assertEqual(events, [('-u', b'old signature and contents'),
                                  ('-f', b'old signature and contents')])

    def test_register_new_failure_restores_old_and_its_registration(self):
        events = self.record_registration(failures={2})
        with self.assertRaisesRegex(i.Refusal, 'injected LaunchServices failure'):
            self.install()
        self.assert_rolled_back()
        self.assertEqual(events, [('-u', b'old signature and contents'),
                                  ('-f', b'new signature and contents'),
                                  ('-u', b'new signature and contents'),
                                  ('-f', b'old signature and contents')])

    def test_registration_cleanup_failures_do_not_prevent_filesystem_rollback(self):
        events = self.record_registration(failures={2, 3, 4})
        with self.assertRaisesRegex(i.Refusal, 'Old app restored.*LaunchServices recovery failed'):
            self.install()
        self.assert_rolled_back()
        self.assertEqual(len(events), 4)
        self.assertEqual(events[-1], ('-f', b'old signature and contents'))

    def test_pre_swap_verification_failure_preserves_old_without_registration(self):
        events = self.record_registration()
        self.verify.side_effect = i.Refusal('stage signature invalid')
        with self.assertRaisesRegex(i.Refusal, 'stage signature invalid'):
            self.install()
        self.assert_rolled_back()
        self.assertEqual(events, [])

    def test_failed_stage_copy_stays_hidden_and_noindex(self):
        def fail(source, stage):
            self.stage_app(source, stage)
            raise i.Refusal('incomplete staging')
        self.stage_copy.side_effect = fail
        with self.assertRaisesRegex(i.Refusal, 'incomplete staging'):
            self.install()
        self.assert_rolled_back()

    def test_stage_rename_error_restores_old(self):
        real_rename = Path.rename
        def rename(path, target):
            if path.parent.name.startswith('.hermes-migration-') and target == self.app:
                raise OSError('injected rename failure')
            return real_rename(path, target)
        with patch.object(Path, 'rename', autospec=True, side_effect=rename):
            with self.assertRaisesRegex(OSError, 'injected rename failure'):
                self.install()
        self.assert_rolled_back()

    def test_cross_device_private_backup_refused_before_build_or_data_copy(self):
        real_stat = Path.stat
        def stat(path, **kwargs):
            result = real_stat(path, **kwargs)
            if path.name == 'old-app.noindex':
                values = list(result)
                values[2] += 1  # st_dev only: simulate a separate home volume.
                return os.stat_result(values)
            return result
        with patch.object(Path, 'stat', autospec=True, side_effect=stat):
            with self.assertRaisesRegex(i.Refusal, 'same filesystem'):
                self.install()
        self.assert_rolled_back()
        self.assertFalse((self.backup()/'userData').exists())
        self.commands.assert_not_called()
        self.stage_copy.assert_not_called()

    def test_symlinked_backup_root_refused_before_build(self):
        target = self.home/'other-backups'
        target.mkdir()
        (self.home/'.hermes/mainstream-backups').symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(i.Refusal, 'Symlink'):
            self.install()
        self.assert_old_bundle(self.app)
        self.assertEqual(list(target.iterdir()), [])
        self.commands.assert_not_called()


class SwapPathTests(unittest.TestCase):
    def test_colliding_and_symlinked_targets_are_refused_without_moves(self):
        for kind in ('backup-dir', 'backup-file', 'backup-link', 'backup-dangling-link',
                     'backup-parent-link', 'stage-link', 'app-link', 'nested-backup', 'same-stage'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                app, stage, backup = root/'Hermes.app', root/'stage/Hermes.app', root/'backup/Hermes.app'
                app.mkdir(); stage.mkdir(parents=True); backup.parent.mkdir()
                (app/'original').write_bytes(b'old'); (stage/'built').write_bytes(b'new')
                if kind == 'backup-dir':
                    backup.mkdir()
                elif kind == 'backup-file':
                    backup.write_bytes(b'occupied')
                elif kind in ('backup-link', 'backup-dangling-link'):
                    backup.symlink_to(app if kind == 'backup-link' else root/'missing')
                elif kind == 'backup-parent-link':
                    backup.parent.rename(root/'real-backup')
                    backup.parent.symlink_to(root/'real-backup', target_is_directory=True)
                elif kind in ('stage-link', 'app-link'):
                    path = stage if kind == 'stage-link' else app
                    path.rename(root/'real-bundle')
                    path.symlink_to(root/'real-bundle', target_is_directory=True)
                elif kind == 'nested-backup':
                    backup = app/'old.app'
                elif kind == 'same-stage':
                    stage = app
                with patch.object(Path, 'rename', autospec=True) as rename, \
                        patch.object(i, 'verify_app') as verify, \
                        patch.object(i, 'update_launch_services') as registration:
                    with self.assertRaises(i.Refusal):
                        i.swap_app(stage, app, backup, verify, registration=registration)
                    rename.assert_not_called()
                    verify.assert_not_called()
                    registration.assert_not_called()
                self.assertEqual((app/'original').read_bytes(), b'old')
                if kind == 'backup-file':
                    self.assertEqual(backup.read_bytes(), b'occupied')

    def test_cross_device_stage_refused_before_verification_or_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, stage, backup = root/'Hermes.app', root/'new.app', root/'old.app'
            app.mkdir(); stage.mkdir()
            real_stat = Path.stat
            def stat(path, **kwargs):
                result = real_stat(path, **kwargs)
                if path == stage:
                    values = list(result); values[2] += 1
                    return os.stat_result(values)
                return result
            with patch.object(Path, 'stat', autospec=True, side_effect=stat), \
                    patch.object(Path, 'rename', autospec=True) as rename, \
                    patch.object(i, 'verify_app') as verify:
                with self.assertRaisesRegex(i.Refusal, 'same filesystem'):
                    i.swap_app(stage, app, backup, verify)
                rename.assert_not_called(); verify.assert_not_called()

    def test_launch_services_nonzero_exit_is_fatal(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)/'Hermes.app'; app.mkdir()
            with patch.object(i, '_command_log', None), \
                    patch.object(i.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'failure')) as run:
                with self.assertRaises(i.Refusal):
                    i.update_launch_services(app)
                self.assertEqual(run.call_args.args[0], [i.LSREGISTER, '-f', str(app)])


if __name__ == '__main__':
    unittest.main()
