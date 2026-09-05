"""Workflow contract unit tests. Fixtures are metadata only, NOT application builds."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def exercise(self, existing_public=False):
        # Execute the actual YAML step with GH subprocesses intercepted only in this unit test.
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        tail = workflow.split('      - name: Create draft, verify exact assets, then publish', 1)[1]
        code = textwrap.dedent(tail.split('        run: |\n', 1)[1])
        repo = 'frankhommers/hermes-desktop-builds'
        endpoint = f'https://api.github.com/repos/{repo}/releases/123'
        self.calls = []
        self.remote = None
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            directory = work / 'out/release'
            directory.mkdir(parents=True)
            payload = b'unit metadata fixture only'
            manifest = {'version': '1.2.3.4', 'targets': {'fixture': {
                'archive': 'fixture.zip', 'sha256': hashlib.sha256(payload).hexdigest(),
            }}}
            (directory / 'fixture.zip').write_bytes(payload)
            (directory / 'release-manifest.json').write_text(json.dumps(manifest))
            (directory / 'RELEASE-NOTES.md').write_text('Unit fixture; no actual application.\n')
            (work / 'out/build-receipt.json').write_text(json.dumps({'head_sha': 'a' * 40}))
            remote = {
                'draft': True, 'prerelease': False, 'tag_name': 'v1.2.3.4',
                'target_commitish': 'a' * 40, 'body': 'Unit fixture; no actual application.\n',
                'html_url': f'https://github.com/{repo}/releases/tag/v1.2.3.4',
                'assets': [{'name': p.name, 'size': p.stat().st_size,
                            'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in directory.iterdir() if p.name != 'RELEASE-NOTES.md'],
            }
            if existing_public:
                self.remote = dict(remote, draft=False)

            def apply_flags(args):
                assert self.remote is not None
                for flag in ('draft', 'prerelease', 'latest'):
                    for arg in args:
                        if arg == '--' + flag:
                            self.remote[flag] = True
                        elif arg.startswith('--' + flag + '='):
                            self.remote[flag] = arg.split('=', 1)[1] == 'true'

            def run(args, **kwargs):
                self.calls.append(args)
                if args[:3] == ['gh', 'release', 'view']:
                    return subprocess.CompletedProcess(args, int(self.remote is None), b'', b'')
                if args[:3] == ['gh', 'release', 'create']:
                    self.remote = dict(remote)
                    apply_flags(args)
                    self.remote['name'] = args[args.index('--title') + 1]
                    return subprocess.CompletedProcess(args, 0)
                if args[:3] == ['gh', 'release', 'edit']:
                    apply_flags(args)
                    return subprocess.CompletedProcess(args, 0)
                raise AssertionError('Unexpected unit-test subprocess: ' + repr(args))

            def output(args):
                if args[:3] == ['gh', 'release', 'view']:
                    return json.dumps({'apiUrl': endpoint}).encode()
                if args == ['gh', 'api', endpoint]:
                    return json.dumps(self.remote).encode()
                raise AssertionError('Unexpected unit-test read: ' + repr(args))

            import os
            old = Path.cwd()
            try:
                os.chdir(work)
                with patch('subprocess.run', side_effect=run), patch('subprocess.check_output', side_effect=output), patch('builtins.print'):
                    exec(compile(code, str(ROOT / '.github/workflows/release.yml'), 'exec'), {})
            finally:
                os.chdir(old)

    def test_verified_release_gets_version_title_and_latest(self):
        self.exercise()
        assert self.remote is not None
        self.assertIs(self.remote['draft'], False)
        self.assertIs(self.remote['prerelease'], False)
        self.assertIs(self.remote['latest'], True)
        self.assertEqual(self.remote['name'], '1.2.3.4')

    def test_existing_public_release_is_never_overwritten(self):
        with self.assertRaisesRegex(AssertionError, 'Never overwrite a published release'):
            self.exercise(existing_public=True)
        self.assertFalse(any(c[:3] in (['gh', 'release', 'create'], ['gh', 'release', 'edit']) for c in self.calls))


if __name__ == '__main__':
    unittest.main()
