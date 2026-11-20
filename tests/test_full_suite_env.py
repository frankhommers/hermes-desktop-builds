"""Linux full-suite environment: short private roots, no packaging inputs, upstream test runtime."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import common
from common import (PACKAGING_ONLY_ENV, SOCKET_BUDGETS, apply_github_file_commands, clean_environment,
                    full_suite_environment, hermes_test_runtime, private_short_root)

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT/'.work/test-tmp'
TEST_TMP.mkdir(parents=True, exist_ok=True)
PIN = {'repository': 'NousResearch/hermes-agent', 'commit': 'ae2a81ccc00e9b7c06301523e5bb09608397149c',
       'version': '0.23.2', 'revision': 1, 'node': '26.12.1'}
# The failing GitHub-hosted layout from run 44648619602.
CI_WORK = Path('/home/runner/work/hermes-desktop-builds/hermes-desktop-builds/.work')


@unittest.skipIf(sys.platform == 'win32', 'POSIX AF_UNIX/ownership contract; full suite runs on Linux only')
class FullSuiteEnvironmentTests(unittest.TestCase):
    def test_ci_work_tmp_is_what_broke_the_socket_fixtures(self):
        probe = SOCKET_BUDGETS[0][0].removeprefix('t/')
        self.assertGreater(len(str(CI_WORK/'tmp'/probe)), SOCKET_BUDGETS[0][1])

    def test_private_short_root_is_fresh_owner_only_and_fits_sun_path(self):
        a, b = private_short_root(), private_short_root()
        try:
            self.assertNotEqual(a, b)
            st = os.lstat(a)
            self.assertTrue(stat.S_ISDIR(st.st_mode))
            self.assertEqual(st.st_uid, os.getuid())
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o700)
            for probe, limit in SOCKET_BUDGETS:
                self.assertLessEqual(len(os.fsencode(str(a/probe))), limit)
        finally:
            os.rmdir(a); os.rmdir(b)

    def test_private_short_root_refuses_a_deep_parent(self):
        deep = Path(tempfile.mkdtemp(dir=TEST_TMP))/('d'*90)
        deep.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'too long'):
            private_short_root(deep)
        self.assertEqual(list(deep.iterdir()), [])

    def test_full_suite_env_drops_packaging_inputs_and_shortens_home_and_tmp(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as work:
            build = clean_environment(Path(work), PIN, {'PATH': '/usr/bin'})
            for key in PACKAGING_ONLY_ENV:
                build[key] = 'x'
            root = private_short_root()
            try:
                env = full_suite_environment(build, root)
                for key in PACKAGING_ONLY_ENV:
                    self.assertNotIn(key, env)
                self.assertIn('HERMES_PAYLOAD_VERSION', build, 'build env itself is left intact')
                for key in ('HOME', 'USERPROFILE', 'TMPDIR', 'TMP', 'TEMP', 'HERMES_HOME',
                            'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
                    self.assertTrue(env[key].startswith(str(root)+os.sep), key)
                    self.assertNotIn(work, env[key])
                self.assertTrue(Path(env['TMPDIR']).is_dir() and Path(env['HOME']).is_dir())
                # Toolchain isolation from clean_environment is kept.
                for key in ('PATH', 'GIT_CEILING_DIRECTORIES', 'npm_config_cache', 'PYTHONNOUSERSITE', 'CI'):
                    self.assertEqual(env[key], build[key])
            finally:
                import shutil; shutil.rmtree(root)

    def test_github_file_commands_apply_like_the_runner(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            e, p = Path(tmp)/'env', Path(tmp)/'path'
            e.write_text('HERMES_PYTHON=/pm/py\nVIRTUAL_ENV=/pm/venv\nHERMES_PYTHON=/pm/venv/bin/python\n')
            p.write_text('/b\n/a\n/c\n')
            env = apply_github_file_commands({'PATH': '/usr/bin', 'X': '1'}, e, p)
            self.assertEqual(env['HERMES_PYTHON'], '/pm/venv/bin/python')
            self.assertEqual(env['PATH'], os.pathsep.join(['/c', '/a', '/b', '/usr/bin']))
            e.write_text('EVIL<<EOF\n')
            with self.assertRaises(ValueError):
                apply_github_file_commands({'PATH': '/usr/bin'}, e, p)

    def test_test_runtime_uses_upstream_setup_pm_phases_and_its_interpreter(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root, src = Path(tmp), Path(tmp)/'src'
            venv = root/'pm/testenv'; (venv/'bin').mkdir(parents=True)
            (venv/'bin/python').write_text('')
            calls = []
            def fake(label, command, cwd, env, logs):
                calls.append((label, command, env.get('HERMES_PYTHON'), env.get('HERMES_PAYLOAD_VERSION')))
                phase = command[3]
                with open(env['GITHUB_ENV'], 'a') as f:
                    if phase == 'install':
                        f.write(f'HERMES_PYTHON={root}/pm/cmd/bin/python\n')
                    if phase == 'dependencies':
                        f.write(f'HERMES_PYTHON={venv}/bin/python\nVIRTUAL_ENV={venv}\nPYTHONPATH={src}\n')
                with open(env['GITHUB_PATH'], 'a') as f:
                    if phase == 'install':
                        f.write('/pm/uv\n')
                    if phase == 'dependencies':
                        f.write(f'{venv}/bin\n')
            env = hermes_test_runtime(src, {'PATH': '/usr/bin', 'HERMES_PYTHON': '/usr/bin/python3'},
                                      root, root/'logs', '/boot/python3', runner=fake)
            self.assertEqual([c[1][3] for c in calls], ['prepare', 'install', 'dependencies'])
            self.assertEqual(calls[0][1][:3], ['/boot/python3', '-S', str(src/'scripts/ci/setup_toolchain.py')])
            self.assertIsNone(calls[0][2], 'host HERMES_PYTHON must not leak into setup')
            self.assertEqual(calls[2][1][0], f'{root}/pm/cmd/bin/python')
            self.assertIn('--test-environment', calls[2][1])
            self.assertEqual(calls[2][1][calls[2][1].index('--extras')+1], '[]')
            self.assertEqual(env['HERMES_PYTHON'], f'{venv}/bin/python')
            self.assertEqual(env['PATH'].split(os.pathsep)[:3], [f'{venv}/bin', '/pm/uv', '/usr/bin'])
            for key in ('GITHUB_ENV', 'GITHUB_PATH', 'GITHUB_OUTPUT'):
                self.assertNotIn(key, env)

    def test_test_runtime_refuses_missing_interpreter_export(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            with self.assertRaisesRegex(RuntimeError, 'did not export'):
                hermes_test_runtime(Path(tmp), {'PATH': '/usr/bin'}, Path(tmp), Path(tmp)/'logs',
                                    '/boot/python3', runner=lambda *a: None)


class FullSuiteWiringTests(unittest.TestCase):
    def test_build_runs_full_suite_in_isolated_test_env(self):
        build = (ROOT/'scripts/build.py').read_text()
        block = build[build.index("report=logs/'vitest.json'"):build.index('gate=gate_test_report(data,pin,rc)')]
        self.assertIn('suite_root=private_short_root()', block)
        self.assertIn('test_env=full_suite_environment(env,suite_root)', block)
        self.assertIn('hermes_test_runtime(src,test_env,suite_root,logs,sys.executable)', block)
        self.assertIn("desktop,True,test_env)", block)
        self.assertIn('shutil.rmtree(suite_root', block)
        self.assertNotRegex(block, r"(?<![_a-z])env\['DESKTOP_TEST_RECEIPT'\]")
        self.assertIn("test_env['DESKTOP_TEST_RECEIPT']=", block)
        self.assertLess(block.index('full_suite_environment'), block.index('hermes_test_runtime'))
        # Gate policy unchanged: no platform passed, so no fixture exception applies to Linux.
        self.assertIn('gate=gate_test_report(data,pin,rc)\n', build)

    def test_packaging_inputs_are_all_set_by_build(self):
        build = (ROOT/'scripts/build.py').read_text()
        for key in PACKAGING_ONLY_ENV:
            self.assertIn(f"env['{key}']", build)


if __name__ == '__main__':
    unittest.main()
