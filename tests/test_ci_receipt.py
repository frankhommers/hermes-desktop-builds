"""Metadata-only tests for the privilege boundary before release publication."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ci_receipt import validate_build_receipt, validate_build_jobs, validate_release_order

REPO = 'frankhommers/hermes-desktop-builds'


def receipt():
    return {
        'id': 123, 'run_attempt': 1, 'repository': {'full_name': REPO},
        'head_repository': {'full_name': REPO}, 'head_branch': 'main',
        'head_sha': 'a' * 40, 'event': 'workflow_dispatch',
        'path': '.github/workflows/build.yml', 'status': 'completed',
        'conclusion': 'success', 'html_url': f'https://github.com/{REPO}/actions/runs/123',
    }


def jobs():
    names = ['scripts', 'native-darwin-arm64', 'native-darwin-x64', 'native-win32-x64', 'native-linux-x64']
    return {'total_count': len(names), 'jobs': [
        {'name': name, 'status': 'completed', 'conclusion': 'success', 'head_sha': 'a' * 40}
        for name in names
    ]}


class CIReceiptTests(unittest.TestCase):
    def test_delayed_old_build_cannot_replace_a_newer_public_release(self):
        releases = [{'tag_name': 'v0.17.0.10', 'draft': False}]
        validate_release_order('0.17.0.11', releases)
        validate_release_order('0.17.0.10', releases)
        with self.assertRaises(ValueError):
            validate_release_order('0.17.0.9', releases)
        with self.assertRaises(ValueError):
            validate_release_order('0.17.0.11\n', releases)

    def test_only_complete_same_repository_main_builds_are_eligible(self):
        self.assertEqual(validate_build_receipt(receipt(), '123')['head_sha'], 'a' * 40)
        mutations = [
            {'id': 124}, {'id': True}, {'run_attempt': 0}, {'run_attempt': True},
            {'head_branch': 'feature'}, {'event': 'pull_request'},
            {'path': '.github/workflows/untrusted.yml'}, {'status': 'in_progress'},
            {'conclusion': 'failure'}, {'head_sha': 'A' * 40}, {'head_sha': 'main'},
            {'repository': {'full_name': 'other/repo'}},
            {'head_repository': {'full_name': 'fork/repo'}},
            {'html_url': f'https://evil.example/{REPO}/actions/runs/123'},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_build_receipt(dict(receipt(), **mutation), '123')
        for run in ('0', '00123', '123\n', '-1', '123;id', ''):
            with self.subTest(run=run), self.assertRaises(ValueError):
                validate_build_receipt(receipt(), run)

    def test_all_native_jobs_must_succeed_at_the_expected_commit(self):
        validate_build_jobs(jobs(), 'a' * 40)
        for field, value in [('conclusion', 'skipped'), ('conclusion', 'failure'),
                             ('status', 'in_progress'), ('head_sha', 'b' * 40)]:
            data = jobs()
            data['jobs'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_build_jobs(data, 'a' * 40)
        data = jobs()
        data['jobs'].pop()
        with self.assertRaises(ValueError):
            validate_build_jobs(data, 'a' * 40)
        data['total_count'] = len(data['jobs'])
        with self.assertRaises(ValueError):
            validate_build_jobs(data, 'a' * 40)
        data = jobs()
        data['jobs'].append(copy.deepcopy(data['jobs'][1]))
        data['total_count'] += 1
        with self.assertRaises(ValueError):
            validate_build_jobs(data, 'a' * 40)


if __name__ == '__main__':
    unittest.main()
