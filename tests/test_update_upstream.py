"""Metadata fixtures only: these tests never dispatch GitHub or build upstream."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('update_upstream', ROOT / 'scripts/update_upstream.py')
assert SPEC is not None and SPEC.loader is not None
u = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(u)

OLD, NEW, BASE, TAG = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
PIN = dict(repository='NousResearch/hermes-agent', commit=OLD, version='0.17.0', revision=3, node='22.22.2')
RELEASE = dict(id=123, tag_name='v2026.9.6', draft=False, prerelease=False)


class FixtureAPI:
    def __init__(self, status='ahead', version='0.17.0', commit=NEW):
        self.calls = []
        self.data = {
            f'repos/{u.UPSTREAM}/releases/latest': RELEASE,
            f'repos/{u.UPSTREAM}/git/ref/tags/v2026.9.6': {
                'ref': 'refs/tags/v2026.9.6', 'object': {'type': 'tag', 'sha': TAG}},
            f'repos/{u.UPSTREAM}/git/tags/{TAG}': {
                'sha': TAG, 'object': {'type': 'commit', 'sha': commit}},
            f'repos/{u.UPSTREAM}/compare/{OLD}...{commit}': {
                'status': status, 'ahead_by': int(status == 'ahead'),
                'behind_by': int(status == 'behind'), 'base_commit': {'sha': OLD},
                'merge_base_commit': {'sha': OLD if status == 'ahead' else commit}},
            f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=1': [],
            f'repos/{u.DOWNSTREAM}/tags?per_page=100&page=1': [],
            f'repos/{u.DOWNSTREAM}/git/ref/heads/main': {
                'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': BASE}},
            u.runs_endpoint(BASE): {'total_count': 0, 'workflow_runs': []},
        }
        self.package(version, commit)

    def package(self, version, commit=NEW):
        content = json.dumps({'version': version}).encode()
        self.data[f'repos/{u.UPSTREAM}/contents/apps/desktop/package.json?ref={commit}'] = {
            'type': 'file', 'encoding': 'base64', 'size': len(content),
            'content': base64.b64encode(content).decode(),
        }

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        value = self.data[endpoint]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class PlanTests(unittest.TestCase):
    def plan(self, api=None, pin=None):
        return u.plan_update(api or FixtureAPI(), copy.deepcopy(pin or PIN), BASE)

    def test_same_version_revision_increments_and_node_stays_pinned(self):
        candidate, meta = self.plan()
        self.assertEqual(candidate, dict(PIN, commit=NEW, revision=4, releaseTag='v2026.9.6', releaseId=123))
        self.assertTrue(meta['changed'])
        self.assertEqual(meta['action'], 'update')
        self.assertEqual(meta['base_sha'], BASE)

    def test_new_version_starts_at_one(self):
        candidate, _ = self.plan(FixtureAPI(version='0.18.0'))
        self.assertEqual((candidate['version'], candidate['revision']), ('0.18.0', 1))

    def test_numeric_comparison_not_lexicographic(self):
        candidate, _ = self.plan(FixtureAPI(version='0.100.0'))
        self.assertEqual(candidate['revision'], 1)

    def test_all_pages_public_prerelease_draft_and_orphan_tags_reserve_versions(self):
        api = FixtureAPI()
        path = f'repos/{u.DOWNSTREAM}/releases?per_page=100&page='
        api.data[path + '1'] = [dict(id=i + 1, tag_name=f'v0.17.0.{i + 1}', draft=False, prerelease=False) for i in range(100)]
        api.data[path + '2'] = [dict(id=101, tag_name='v0.17.0.110', draft=True, prerelease=False),
                                dict(id=102, tag_name='v0.17.0.120', draft=False, prerelease=True)]
        api.data[f'repos/{u.DOWNSTREAM}/tags?per_page=100&page=1'] = [{'name': 'v0.17.0.121'}]
        candidate, _ = self.plan(api)
        self.assertEqual(candidate['revision'], 122)
        self.assertIn(path + '2', api.calls)

    def test_new_version_also_exceeds_reserved_revisions(self):
        api = FixtureAPI(version='0.18.0')
        api.data[f'repos/{u.DOWNSTREAM}/tags?per_page=100&page=1'] = [{'name': 'v0.18.0.6'}]
        self.assertEqual(self.plan(api)[0]['revision'], 7)

    def test_downgrade_against_pin_and_any_published_version_is_rejected(self):
        with self.assertRaisesRegex(u.UpdateError, 'downgrade'):
            self.plan(FixtureAPI(version='0.16.9'))
        api = FixtureAPI()
        api.data[f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=1'] = [
            dict(id=1, tag_name='v0.18.0.1', draft=False, prerelease=False)]
        with self.assertRaisesRegex(u.UpdateError, 'downgrade'):
            self.plan(api)

    def test_behind_and_identical_skip_without_reading_package_or_versions(self):
        for status, commit in [('behind', NEW), ('identical', OLD)]:
            with self.subTest(status=status):
                api = FixtureAPI(status=status, commit=commit)
                candidate, meta = self.plan(api)
                self.assertEqual(candidate, PIN)
                self.assertEqual(meta['action'], 'skip')
                self.assertFalse(meta['changed'])
                self.assertFalse(any('/contents/' in c or '/releases?' in c for c in api.calls))

    def test_diverged_and_inconsistent_comparison_fail_closed(self):
        for status in ['diverged', 'unknown', 'ahead']:
            api = FixtureAPI(status=status)
            if status == 'ahead':
                api.data[f'repos/{u.UPSTREAM}/compare/{OLD}...{NEW}']['behind_by'] = 1
            with self.subTest(status=status), self.assertRaises(u.UpdateError):
                self.plan(api)

    def test_bad_pin_validated_before_any_api_lookup(self):
        for field, value in [('commit', OLD.upper()), ('commit', OLD + '\n'),
                             ('commit', '0' * 40), ('commit', OLD[:-1]),
                             ('revision', True), ('node', '22.22.2;sh'),
                             ('repository', 'attacker/hermes-agent'), ('version', '00.17.0'),
                             ('releaseTag', 'v2026.9.6'), ('unexpected', 'unsafe')]:
            api = FixtureAPI()
            with self.subTest(field=field, value=value), self.assertRaises(u.UpdateError):
                self.plan(api, dict(PIN, **{field: value}))
            self.assertEqual(api.calls, [])

    def test_bad_release_metadata_rejected_before_tag_lookup(self):
        for field, value in [('tag_name', 'v2026.9.6\n'), ('tag_name', '../main'),
                             ('tag_name', 'v2026.9.6-rc1'), ('id', True),
                             ('draft', True), ('prerelease', True), ('draft', 0)]:
            api = FixtureAPI()
            api.data[f'repos/{u.UPSTREAM}/releases/latest'] = dict(RELEASE, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(u.UpdateError):
                self.plan(api)
            self.assertEqual(len(api.calls), 1)

    def test_lightweight_tag_supported(self):
        api = FixtureAPI()
        api.data[f'repos/{u.UPSTREAM}/git/ref/tags/v2026.9.6']['object'] = {'type': 'commit', 'sha': NEW}
        self.assertEqual(self.plan(api)[0]['commit'], NEW)
        self.assertFalse(any('/git/tags/' in c for c in api.calls))

    def test_bad_tag_sha_cycle_tree_and_depth_fail_before_untrusted_lookup(self):
        for obj in [{'type': 'commit', 'sha': NEW.upper()}, {'type': 'tree', 'sha': NEW},
                    {'type': 'tag', 'sha': TAG}, {'type': 'commit', 'sha': NEW + ' '}]:
            api = FixtureAPI()
            api.data[f'repos/{u.UPSTREAM}/git/tags/{TAG}']['object'] = obj
            with self.subTest(obj=obj), self.assertRaises(u.UpdateError):
                self.plan(api)
            self.assertFalse(any(NEW.upper() in c or ' ' in c for c in api.calls))
        api = FixtureAPI()
        with patch.object(u, 'MAX_TAG_DEPTH', 0), self.assertRaises(u.UpdateError):
            self.plan(api)

    def provenance_api(self, commit=OLD):
        api = FixtureAPI(status='identical', commit=commit)
        api.data[f'repos/{u.UPSTREAM}/releases/123'] = RELEASE
        api.data[f'repos/{u.DOWNSTREAM}/commits/{BASE}'] = {
            'sha': BASE,
            'commit': {'message': 'chore: pin official Hermes v2026.9.6',
                       'committer': {'date': u.datetime.now(u.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}},
            'files': [{'filename': 'upstream.json', 'status': 'modified'}],
        }
        return api

    def test_previously_pinned_tag_is_immutable(self):
        pin = dict(PIN, releaseTag='v2026.9.6', releaseId=123)
        with self.assertRaisesRegex(u.UpdateError, 'moved'):
            self.plan(self.provenance_api(NEW), pin)
        for field, value in [('id', 456), ('tag_name', 'v2026.9.7'), ('draft', True)]:
            api = self.provenance_api()
            api.data[f'repos/{u.UPSTREAM}/releases/123'] = dict(RELEASE, **{field: value})
            with self.subTest(field=field), self.assertRaises(u.UpdateError):
                self.plan(api, pin)

    def test_same_tag_recreated_with_new_release_id_is_rejected(self):
        api = self.provenance_api()
        api.data[f'repos/{u.UPSTREAM}/releases/latest'] = dict(RELEASE, id=456)
        with self.assertRaises(u.UpdateError):
            self.plan(api, dict(PIN, releaseTag='v2026.9.6', releaseId=123))

    def test_missing_dispatch_recovers_only_official_unreserved_pin_without_runs(self):
        pin = dict(PIN, releaseTag='v2026.9.6', releaseId=123)
        candidate, meta = self.plan(self.provenance_api(), pin)
        self.assertEqual(candidate, pin)
        self.assertEqual(meta['action'], 'dispatch')
        self.assertFalse(meta['changed'])
        for record in [dict(id=1, tag_name='v0.17.0.3', draft=False, prerelease=False),
                       dict(id=1, tag_name='v0.17.0.3', draft=True, prerelease=False)]:
            api = self.provenance_api()
            api.data[f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=1'] = [record]
            self.assertEqual(self.plan(api, pin)[1]['action'], 'skip')

    def test_in_progress_or_successful_build_prevents_automatic_retries(self):
        pin = dict(PIN, releaseTag='v2026.9.6', releaseId=123)
        for conclusion in [None, 'success']:
            api = self.provenance_api()
            api.data[u.runs_endpoint(BASE)] = {'total_count': 1, 'workflow_runs': [run_receipt(BASE, conclusion)]}
            self.assertEqual(self.plan(api, pin)[1]['action'], 'skip')

    def test_failed_builds_never_trigger_daily_rebuilds(self):
        pin = dict(PIN, releaseTag='v2026.9.6', releaseId=123)
        for conclusion in ('failure', 'cancelled', 'timed_out', 'startup_failure'):
            api = self.provenance_api()
            api.data[u.runs_endpoint(BASE)] = {'total_count': 1, 'workflow_runs': [run_receipt(BASE, conclusion)]}
            with self.subTest(conclusion=conclusion):
                meta = self.plan(api, pin)[1]
                self.assertEqual(meta['action'], 'skip')
                self.assertEqual(meta['reason'], 'build_failed_manual_review')

    def test_missing_dispatch_recovery_is_time_bounded_and_pin_commit_only(self):
        pin = dict(PIN, releaseTag='v2026.9.6', releaseId=123)
        api = self.provenance_api()
        commit = api.data[f'repos/{u.DOWNSTREAM}/commits/{BASE}']
        commit['commit']['committer']['date'] = '2000-01-01T00:00:00Z'
        self.assertEqual(self.plan(api, pin)[1]['reason'], 'dispatch_recovery_window_expired_manual_review')
        api = self.provenance_api()
        commit = api.data[f'repos/{u.DOWNSTREAM}/commits/{BASE}']
        commit['files'].append({'filename': 'README.md', 'status': 'modified'})
        self.assertEqual(self.plan(api, pin)[1]['reason'], 'not_updater_pin_commit_manual_review')
        api = self.provenance_api()
        api.data[f'repos/{u.DOWNSTREAM}/commits/{BASE}']['commit']['message'] = 'docs: update readme'
        self.assertEqual(self.plan(api, pin)[1]['reason'], 'not_updater_pin_commit_manual_review')

    def test_missing_dispatch_recovery_rejects_wrong_commit_and_timestamp(self):
        pin = dict(PIN, releaseTag='v2026.9.6', releaseId=123)
        for field, value in [('sha', NEW), ('date', '2026-99-99T00:00:00Z'),
                             ('date', '2999-01-01T00:00:00Z'), ('date', 'invalid')]:
            api = self.provenance_api()
            commit = api.data[f'repos/{u.DOWNSTREAM}/commits/{BASE}']
            if field == 'sha':
                commit['sha'] = value
            else:
                commit['commit']['committer']['date'] = value
            with self.subTest(field=field, value=value), self.assertRaises(u.UpdateError):
                self.plan(api, pin)

    def test_api_errors_and_malformed_lists_packages_fail_closed(self):
        for value in [u.UpdateError('HTTP 403'), {}, None]:
            api = FixtureAPI()
            api.data[f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=1'] = value
            with self.subTest(value=value), self.assertRaises(u.UpdateError):
                self.plan(api)
        for version in ['0.17.0-beta', 'v0.17.0', None, '0.17.0\n']:
            with self.subTest(version=version), self.assertRaises(u.UpdateError):
                self.plan(FixtureAPI(version=version))
        api = FixtureAPI()
        api.data[f'repos/{u.UPSTREAM}/contents/apps/desktop/package.json?ref={NEW}']['content'] = '@invalid'
        with self.assertRaises(u.UpdateError):
            self.plan(api)

    def test_pagination_limit_and_duplicate_records_fail_closed(self):
        api = FixtureAPI()
        api.data[f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=1'] = [
            dict(id=i, tag_name=f'v0.17.0.{i}', draft=False, prerelease=False) for i in range(1, 101)]
        with patch.object(u, 'MAX_PAGES', 1), self.assertRaises(u.UpdateError):
            self.plan(api)
        api.data[f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=2'] = [api.data[f'repos/{u.DOWNSTREAM}/releases?per_page=100&page=1'][0]]
        with self.assertRaises(u.UpdateError):
            self.plan(api)


def run_receipt(sha, conclusion=None):
    return dict(id=42, head_sha=sha, head_branch='main', event='workflow_dispatch',
                path='.github/workflows/build.yml', repository={'full_name': u.DOWNSTREAM},
                head_repository={'full_name': u.DOWNSTREAM},
                status='completed' if conclusion else 'queued', conclusion=conclusion,
                run_attempt=1, created_at='2026-01-01T00:00:00Z')


class ApplyTests(unittest.TestCase):
    def exercise(self, mutate=None, remote_changed=False, dispatch_visible=True):
        api = FixtureAPI()
        candidate, metadata = u.plan_update(api, PIN, BASE)
        if mutate:
            mutate(candidate, metadata, api)
        calls = []
        state = {'head': BASE, 'pushed': False, 'dispatched': False}

        def command(args, cwd=None):
            calls.append(args)
            if args[:2] == ['git', 'rev-parse']:
                return state['head']
            if args == ['git', 'status', '--porcelain']:
                return ''
            if args[:3] == ['git', 'diff', '--cached']:
                return 'upstream.json'
            if args[:2] == ['git', 'commit']:
                state['head'] = 'e' * 40
            if args[:2] == ['git', 'push']:
                state['pushed'] = True
                api.data[f'repos/{u.DOWNSTREAM}/git/ref/heads/main']['object']['sha'] = state['head']
                api.data[u.runs_endpoint(state['head'])] = {'total_count': 0, 'workflow_runs': []}
                api.data[f'repos/{u.DOWNSTREAM}/contents/upstream.json?ref={state["head"]}'] = {
                    'encoding': 'base64', 'content': base64.b64encode(json.dumps(candidate).encode()).decode()}
            if args[:3] == ['gh', 'workflow', 'run']:
                state['dispatched'] = True
                if dispatch_visible:
                    api.data[u.runs_endpoint(state['head'])] = {'total_count': 1, 'workflow_runs': [run_receipt(state['head'])]}
            return ''

        if remote_changed:
            api.data[f'repos/{u.DOWNSTREAM}/git/ref/heads/main']['object']['sha'] = 'f' * 40
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'upstream.json').write_text(json.dumps(PIN))
            with patch.object(u, 'command', side_effect=command), patch.object(u.time, 'sleep'):
                self.calls = calls
                self.state = state
                return u.apply_plan(api, root, candidate, metadata, BASE)

    def test_apply_rechecks_plan_commits_only_pin_pushes_ff_and_verifies_dispatch(self):
        self.assertEqual(self.exercise(), 'e' * 40)
        self.assertIn(['git', 'add', '--', 'upstream.json'], self.calls)
        self.assertIn(['git', 'push', 'origin', 'HEAD:refs/heads/main'], self.calls)
        self.assertIn(['gh', 'workflow', 'run', 'build.yml', '--repo', u.DOWNSTREAM,
                       '--ref', 'main', '-f', 'expected_sha=' + 'e' * 40], self.calls)
        self.assertFalse(any('--force' in arg for args in self.calls for arg in args))

    def test_changed_main_aborts_before_write_or_dispatch(self):
        with self.assertRaisesRegex(u.UpdateError, 'main changed'):
            self.exercise(remote_changed=True)
        self.assertFalse(self.state['pushed'] or self.state['dispatched'])
        self.assertFalse(any(c[:2] == ['git', 'add'] for c in self.calls))

    def test_tampered_artifact_and_moved_tag_abort_before_commit(self):
        def move_tag(p, m, api):
            moved = 'f' * 40
            api.data[f'repos/{u.UPSTREAM}/git/tags/{TAG}']['object']['sha'] = moved
            api.data[f'repos/{u.UPSTREAM}/compare/{OLD}...{moved}'] = api.data[f'repos/{u.UPSTREAM}/compare/{OLD}...{NEW}']
            api.package('0.17.0', moved)

        for mutate in [lambda p, m, a: p.update(node='23.0.0'),
                       lambda p, m, a: m.update(base_sha='f' * 40),
                       move_tag]:
            with self.subTest(mutate=mutate), self.assertRaises(u.UpdateError):
                self.exercise(mutate=mutate)
            self.assertFalse(self.state['pushed'] or self.state['dispatched'])

    def test_dispatch_must_be_visible_after_write(self):
        with patch.object(u, 'DISPATCH_POLLS', 2), self.assertRaisesRegex(u.UpdateError, 'not visible'):
            self.exercise(dispatch_visible=False)
        self.assertTrue(self.state['pushed'] and self.state['dispatched'])


class APITests(unittest.TestCase):
    def test_only_explicit_get_fixed_host_and_errors_never_echo_credentials(self):
        with patch.object(u, 'command', return_value='{"ok": true}') as cmd:
            self.assertEqual(u.GitHubAPI()('repos/NousResearch/hermes-agent/releases/latest'), {'ok': True})
        args = cmd.call_args.args[0]
        self.assertIn('GET', args)
        self.assertIn('github.com', args)
        with patch.object(u.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'TOKEN_SECRET')):
            with self.assertRaises(u.UpdateError) as result:
                u.command(['gh', 'api', 'anything'])
        self.assertNotIn('TOKEN_SECRET', str(result.exception))

    def test_duplicate_json_keys_fail_closed(self):
        with patch.object(u, 'command', return_value='{"id":1,"id":2}'):
            with self.assertRaises(u.UpdateError):
                u.GitHubAPI()('repos/NousResearch/hermes-agent/releases/latest')

    def test_run_query_count_must_agree_and_receipt_must_match(self):
        for payload in [{'total_count': 1, 'workflow_runs': []},
                        {'total_count': 0, 'workflow_runs': [run_receipt(BASE)]},
                        {'total_count': 1, 'workflow_runs': [dict(run_receipt(BASE), head_sha=NEW)]}]:
            api = FixtureAPI()
            api.data[u.runs_endpoint(BASE)] = payload
            with self.subTest(payload=payload), self.assertRaises(u.UpdateError):
                u.build_exists(api, BASE)


class WorkflowTests(unittest.TestCase):
    def test_workflow_outputs_are_actual_newline_delimited_records(self):
        text = (ROOT / '.github/workflows/update-upstream.yml').read_text()
        code = textwrap.dedent(text.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'out/update').mkdir(parents=True)
            (root / 'out/update/update.json').write_text(json.dumps({'action': 'update', 'changed': True}))
            output, summary = root / 'output', root / 'summary'
            env = dict(os.environ, GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
            subprocess.run([sys.executable, '-c', code], cwd=root, env=env, check=True, capture_output=True)
            self.assertEqual(output.read_text(), 'action=update\nchanged=true\n')
            self.assertIn('\n\n```json\n', summary.read_text())

    def test_workflow_trust_and_permissions_contract(self):
        text = (ROOT / '.github/workflows/update-upstream.yml').read_text()
        prepare, write = text.split('\n  write:\n')
        self.assertIn('schedule:', prepare)
        self.assertIn('workflow_dispatch:', prepare)
        self.assertIn("cron: '17 3 * * *'", prepare)
        self.assertIn("github.ref == 'refs/heads/main'", prepare)
        self.assertIn('contents: read', prepare)
        self.assertNotIn('contents: write', prepare)
        self.assertIn('contents: write', write)
        self.assertIn('actions: write', write)
        self.assertEqual(text.count('ref: ${{ github.sha }}'), 2)
        self.assertEqual(text.count('persist-credentials: false'), 2)
        self.assertIn('scripts/update_upstream.py apply', write)
        for forbidden in ['pull_request_target', 'npm ', 'pip ', 'scripts/build.py', 'secrets.', 'git pull', '--force']:
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
