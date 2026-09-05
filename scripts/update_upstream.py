#!/usr/bin/env python3
"""Discover official releases using metadata only; never execute upstream code.

Read-only: python3 scripts/update_upstream.py plan --output out/upstream.json \
    --metadata out/update.json
Privileged (CI only): ... apply --candidate out/upstream.json \
    --metadata out/update.json --expected-base "$GITHUB_SHA"

The write job re-plans with the SAME trusted script/checkout before accepting the
artifact. Only upstream.json is committed, fast-forward, then build.yml receives
expected_sha. The separate build/publication gates retain all their checks: new
upstream failures are NOT automatically allowlisted. Node stays at the reviewed
pin. Public releases, prereleases, drafts and orphan tags reserve app versions.

Recovery is deliberately bounded: only an official unpublished pin with NO build
run can recover an interrupted/failed dispatch, within 48 hours of its pin-only
updater commit, while that commit is still main. A recorded run, even a failed or
cancelled one, is never automatically rebuilt. Failed builds leave the pin on
main and the previous public release intact. Expired recovery, failed builds or
failed publication need maintainer review; no automatic source fixes, gate
relaxation, rollback, PAT, dependency install or upstream execution.
"""
import argparse
import base64
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time

UPSTREAM = 'NousResearch/hermes-agent'
DOWNSTREAM = 'frankhommers/hermes-desktop-builds'
ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
RELEASE_TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+')
APP_TAG = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.([1-9][0-9]*)')
MAX_TAG_DEPTH = 8
MAX_PAGES = 100
DISPATCH_POLLS = 12
RECOVERY_HOURS = 48


class UpdateError(RuntimeError):
    """Fail closed; messages must not contain command output or credentials."""


def require(condition, message):
    if not condition:
        raise UpdateError(message)


def exact_sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value) and value != '0' * 40,
            'Expected exact full lowercase nonzero SHA; no normalization')
    return value


def version_tuple(value):
    require(isinstance(value, str) and len(value) <= 64 and VERSION.fullmatch(value),
            'Expected canonical three-component version')
    return tuple(map(int, value.split('.')))


def exact_tag(value):
    require(isinstance(value, str) and len(value) <= 80 and RELEASE_TAG.fullmatch(value),
            'Expected exact stable upstream release tag vN.N.N')
    return value


def positive_int(value):
    require(type(value) is int and 0 < value < 2**63, 'Expected positive integer')
    return value


def validate_pin(pin):
    require(isinstance(pin, dict), 'Pin must be an object')
    required = {'repository', 'commit', 'version', 'revision', 'node'}
    optional = {'releaseTag', 'releaseId'}
    require(required <= pin.keys() and not pin.keys() - required - optional,
            'Unexpected or missing pin fields')
    require(pin['repository'] == UPSTREAM, 'Unexpected upstream repository')
    exact_sha(pin['commit'])
    version_tuple(pin['version'])
    version_tuple(pin['node'])
    positive_int(pin['revision'])
    require(('releaseTag' in pin) == ('releaseId' in pin), 'Incomplete release provenance')
    if 'releaseTag' in pin:
        exact_tag(pin['releaseTag'])
        positive_int(pin['releaseId'])
    return pin


def json_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, 'Duplicate JSON object key')
        obj[key] = value
    return obj


def parse_json(text):
    try:
        return json.loads(text, object_pairs_hook=json_object)
    except (ValueError, UnicodeError) as exc:
        raise UpdateError('Invalid JSON metadata') from exc


def command(args, cwd=None):
    try:
        result = subprocess.run(args, cwd=cwd, check=False, capture_output=True,
                                text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError('Command unavailable or timed out') from exc
    require(result.returncode == 0, 'Command failed (output suppressed to protect credentials)')
    return result.stdout.strip()


class GitHubAPI:
    def __call__(self, endpoint):
        require(isinstance(endpoint, str) and endpoint.startswith(
            (f'repos/{UPSTREAM}/', f'repos/{DOWNSTREAM}/'))
            and not re.search(r'[\s#]', endpoint), 'Unexpected API endpoint')
        # Explicit GET: gh defaults to POST if fields are added. Pin the API host;
        # never follow a URL supplied by release metadata or echo GH diagnostics.
        return parse_json(command(['gh', 'api', '--hostname', 'github.com',
                                   '--method', 'GET', '-H', 'Accept: application/vnd.github+json',
                                   '-H', 'X-GitHub-Api-Version: 2022-11-28', endpoint]))


def official_release(value):
    require(isinstance(value, dict), 'Release must be an object')
    require(value.get('draft') is False and value.get('prerelease') is False,
            'Only official non-draft, non-prerelease releases are accepted')
    return {'id': positive_int(value.get('id')), 'tag': exact_tag(value.get('tag_name'))}


def resolve_tag(api, tag):
    exact_tag(tag)  # Never look up a malformed token, even if GitHub would accept it.
    ref = api(f'repos/{UPSTREAM}/git/ref/tags/{tag}')
    require(isinstance(ref, dict) and ref.get('ref') == f'refs/tags/{tag}', 'Tag ref mismatch')
    obj = ref.get('object')
    seen = set()
    for depth in range(MAX_TAG_DEPTH + 1):
        require(isinstance(obj, dict), 'Invalid Git object')
        sha = exact_sha(obj.get('sha'))
        require(sha not in seen, 'Annotated tag cycle')
        seen.add(sha)
        if obj.get('type') == 'commit':
            return sha
        require(obj.get('type') == 'tag' and depth < MAX_TAG_DEPTH,
                'Tag must peel to a commit within the depth bound')
        annotated = api(f'repos/{UPSTREAM}/git/tags/{sha}')
        require(isinstance(annotated, dict) and annotated.get('sha') == sha,
                'Annotated tag identity mismatch')
        obj = annotated.get('object')
    raise UpdateError('Tag peel limit exceeded')


def compare_commits(api, current, candidate):
    exact_sha(current)
    exact_sha(candidate)
    result = api(f'repos/{UPSTREAM}/compare/{current}...{candidate}')
    require(isinstance(result, dict), 'Invalid comparison')
    require(result.get('base_commit', {}).get('sha') == current, 'Comparison base mismatch')
    status = result.get('status')
    ahead, behind = result.get('ahead_by'), result.get('behind_by')
    require(type(ahead) is int and type(behind) is int and ahead >= 0 and behind >= 0,
            'Invalid comparison counts')
    require(status in ('ahead', 'behind', 'identical'), 'Diverged or unknown upstream history')
    require((status == 'ahead' and ahead > 0 and behind == 0 and current != candidate
             and result.get('merge_base_commit', {}).get('sha') == current)
            or (status == 'behind' and ahead == 0 and behind > 0 and current != candidate
                and result.get('merge_base_commit', {}).get('sha') == candidate)
            or (status == 'identical' and ahead == behind == 0 and current == candidate),
            'Inconsistent comparison status/counts')
    return status


def desktop_version(api, sha):
    exact_sha(sha)
    content = api(f'repos/{UPSTREAM}/contents/apps/desktop/package.json?ref={sha}')
    require(isinstance(content, dict) and content.get('type') == 'file'
            and content.get('encoding') == 'base64', 'Expected base64 package.json file')
    require(type(content.get('size')) is int and 0 < content['size'] <= 1024 * 1024
            and isinstance(content.get('content'), str) and len(content['content']) <= 2 * 1024 * 1024,
            'Invalid package.json size/content')
    try:
        # The contents API wraps base64 in newlines. This is encoding, not a tag/SHA.
        raw = base64.b64decode(content['content'].replace('\n', ''), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise UpdateError('Invalid package.json base64') from exc
    require(len(raw) == content['size'], 'package.json size mismatch')
    package = parse_json(raw)
    require(isinstance(package, dict), 'package.json must be an object')
    version_tuple(package.get('version'))
    return package['version']


def paginated(api, resource):
    rows = []
    for page in range(1, MAX_PAGES + 1):
        batch = api(f'repos/{DOWNSTREAM}/{resource}?per_page=100&page={page}')
        require(isinstance(batch, list) and len(batch) <= 100
                and all(isinstance(item, dict) for item in batch), 'Invalid paginated API response')
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise UpdateError('Pagination limit exceeded; cannot prove version availability')


def reserved_versions(api):
    versions = set()
    ids = set()
    release_tags = set()
    for release in paginated(api, 'releases'):
        rid = positive_int(release.get('id'))
        require(rid not in ids and release.get('tag_name') not in release_tags,
                'Duplicate release during pagination; retry discovery')
        ids.add(rid)
        tag = release.get('tag_name')
        release_tags.add(tag)
        require(type(release.get('draft')) is bool and type(release.get('prerelease')) is bool,
                'Missing release visibility flags')
        versions.add(app_version(tag))
    tags = set()
    for record in paginated(api, 'tags'):
        tag = record.get('name')
        parsed = app_version(tag)
        require(tag not in tags, 'Duplicate tag during pagination; retry discovery')
        tags.add(tag)
        versions.add(parsed)
    return versions


def app_version(tag):
    require(isinstance(tag, str) and len(tag) <= 90 and APP_TAG.fullmatch(tag),
            'Unrecognized downstream version tag; manual review required')
    return tuple(map(int, tag[1:].split('.')))


def runs_endpoint(sha):
    exact_sha(sha)
    return (f'repos/{DOWNSTREAM}/actions/workflows/build.yml/runs'
            f'?head_sha={sha}&branch=main&event=workflow_dispatch&per_page=100&page=1')


def build_runs(api, sha):
    receipt = api(runs_endpoint(sha))
    require(isinstance(receipt, dict), 'Invalid build query')
    runs, count = receipt.get('workflow_runs'), receipt.get('total_count')
    # >100 is anomalous and fails closed rather than risking duplicate dispatch.
    require(isinstance(runs, list) and type(count) is int and 0 <= count <= 100
            and count == len(runs), 'Build query count mismatch or limit exceeded')
    ids = set()
    for run in runs:
        require(isinstance(run, dict) and run.get('head_sha') == sha
                and run.get('head_branch') == 'main' and run.get('event') == 'workflow_dispatch'
                and run.get('path') == '.github/workflows/build.yml'
                and run.get('repository', {}).get('full_name') == DOWNSTREAM
                and run.get('head_repository', {}).get('full_name') == DOWNSTREAM,
                'Build query returned a mismatched receipt')
        rid = positive_int(run.get('id'))
        require(rid not in ids, 'Duplicate build run')
        ids.add(rid)
        positive_int(run.get('run_attempt'))
        require(isinstance(run.get('status'), str) and run['status'] in (
            'queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending'), 'Invalid run status')
    return runs


def build_exists(api, sha):
    return bool(build_runs(api, sha))


def dispatch_decision(runs):
    if not runs:
        return True, 'recover_missing_dispatch'
    if any(run['status'] != 'completed' for run in runs):
        return False, 'build_in_progress'
    if any(run.get('conclusion') == 'success' for run in runs):
        return False, 'build_succeeded_publication_owned_by_release_workflow'
    require(all(run.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'startup_failure')
                for run in runs), 'Unexpected build conclusion; manual review required')
    return False, 'build_failed_manual_review'


def recovery_decision(api, pin, base_sha, now=None):
    """Recover missing dispatches only, not daily rebuilds or later main commits."""
    dispatch, reason = dispatch_decision(build_runs(api, base_sha))
    if not dispatch:
        return dispatch, reason
    record = api(f'repos/{DOWNSTREAM}/commits/{base_sha}')
    require(isinstance(record, dict) and record.get('sha') == base_sha,
            'Recovery commit identity mismatch')
    commit, files = record.get('commit'), record.get('files')
    if not (isinstance(commit, dict) and isinstance(files, list)
            and all(isinstance(file, dict) for file in files)):
        raise UpdateError('Invalid recovery commit')
    if (commit.get('message') != 'chore: pin official Hermes ' + pin['releaseTag']
            or len(files) != 1 or files[0].get('filename') != 'upstream.json'
            or files[0].get('status') != 'modified'):
        return False, 'not_updater_pin_commit_manual_review'
    committer = commit.get('committer')
    if not isinstance(committer, dict):
        raise UpdateError('Invalid recovery committer')
    stamp = committer.get('date')
    if not (isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', stamp)):
        raise UpdateError('Invalid recovery timestamp')
    try:
        created = datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise UpdateError('Invalid recovery timestamp') from exc
    age = ((now or datetime.now(timezone.utc)) - created).total_seconds()
    require(age >= 0, 'Recovery timestamp is in the future')
    if age >= RECOVERY_HOURS * 60 * 60:
        return False, 'dispatch_recovery_window_expired_manual_review'
    return True, 'recover_missing_dispatch'


def plan_update(api, pin, base_sha):
    validate_pin(pin)
    exact_sha(base_sha)
    if 'releaseTag' in pin:
        previous = official_release(api(f'repos/{UPSTREAM}/releases/{pin["releaseId"]}'))
        require(previous == {'id': pin['releaseId'], 'tag': pin['releaseTag']},
                'Pinned official release identity changed')
        require(resolve_tag(api, previous['tag']) == pin['commit'], 'Pinned upstream tag moved')
    release = official_release(api(f'repos/{UPSTREAM}/releases/latest'))
    sha = resolve_tag(api, release['tag'])
    if 'releaseTag' in pin and (release['tag'] == pin['releaseTag'] or release['id'] == pin['releaseId']):
        require(release == {'id': pin['releaseId'], 'tag': pin['releaseTag']} and sha == pin['commit'],
                'Official release tag/identity moved or was recreated')
    status = compare_commits(api, pin['commit'], sha)
    candidate = dict(pin)
    metadata = {'schema': 1, 'base_sha': base_sha, 'current': dict(pin),
                'release': dict(release, commit=sha), 'comparison': status,
                'changed': False, 'action': 'skip', 'reason': 'official_release_' + status}
    if status == 'behind':
        return candidate, metadata
    if status == 'identical':
        if 'releaseTag' not in pin or release['id'] != pin['releaseId']:
            return candidate, metadata
        require(desktop_version(api, sha) == pin['version'], 'Pinned package version mismatch')
        versions = reserved_versions(api)
        current_version = (*version_tuple(pin['version']), pin['revision'])
        if any(version >= current_version for version in versions):
            metadata['reason'] = 'version_already_reserved_or_superseded'
        else:
            dispatch, reason = recovery_decision(api, pin, base_sha)
            metadata.update(action='dispatch' if dispatch else 'skip', reason=reason)
        return candidate, metadata
    version = desktop_version(api, sha)
    numeric = version_tuple(version)
    require(numeric >= version_tuple(pin['version']), 'Refusing application version downgrade')
    versions = reserved_versions(api)
    require(not any(v[:3] > numeric for v in versions), 'Refusing published/reserved version downgrade')
    previous_revision = pin['revision'] if version == pin['version'] else 0
    revision = max([previous_revision] + [v[3] for v in versions if v[:3] == numeric]) + 1
    candidate.update(commit=sha, version=version, revision=revision,
                     releaseTag=release['tag'], releaseId=release['id'])
    validate_pin(candidate)
    metadata.update(changed=True, action='update', reason='new_official_release_ahead')
    return candidate, metadata


def remote_main(api):
    ref = api(f'repos/{DOWNSTREAM}/git/ref/heads/main')
    require(isinstance(ref, dict) and ref.get('ref') == 'refs/heads/main'
            and isinstance(ref.get('object'), dict) and ref['object'].get('type') == 'commit',
            'Invalid main ref')
    return exact_sha(ref['object'].get('sha'))


def dispatch_build(api, root, sha):
    require(remote_main(api) == sha, 'main changed before build dispatch')
    before = build_runs(api, sha)
    if not dispatch_decision(before)[0]:
        return
    ids = {run['id'] for run in before}
    command(['gh', 'workflow', 'run', 'build.yml', '--repo', DOWNSTREAM,
             '--ref', 'main', '-f', 'expected_sha=' + sha], cwd=root)
    for attempt in range(DISPATCH_POLLS):
        if any(run['id'] not in ids for run in build_runs(api, sha)):
            return
        if attempt + 1 < DISPATCH_POLLS:
            time.sleep(5)
    raise UpdateError('Build dispatch not visible; pin retained, next daily check can recover')


def apply_plan(api, root, candidate, metadata, expected_base):
    """Execute only after CI checked out this script at expected_base (never artifact code)."""
    exact_sha(expected_base)
    validate_pin(candidate)
    require(isinstance(metadata, dict) and metadata.get('base_sha') == expected_base,
            'Plan base SHA mismatch')
    require(command(['git', 'rev-parse', 'HEAD'], cwd=root) == expected_base, 'Checkout SHA mismatch')
    require(not command(['git', 'status', '--porcelain'], cwd=root), 'Checkout must be clean')
    require(remote_main(api) == expected_base, 'main changed since prepare; run discovery again')
    current = validate_pin(parse_json((root / 'upstream.json').read_text()))
    # Revalidate all provenance/history/version decisions; data from an artifact
    # cannot select code to run, alter node, relax gates or edit other files.
    fresh_candidate, fresh_metadata = plan_update(api, current, expected_base)
    require(candidate == fresh_candidate and metadata == fresh_metadata,
            'Plan changed or artifact was tampered with; run discovery again')
    if metadata['action'] == 'skip':
        return expected_base
    require(remote_main(api) == expected_base, 'main changed during revalidation')
    sha = expected_base
    if metadata['changed']:
        (root / 'upstream.json').write_text(json.dumps(fresh_candidate, indent=2) + '\n')
        command(['git', 'add', '--', 'upstream.json'], cwd=root)
        require(command(['git', 'diff', '--cached', '--name-only'], cwd=root) == 'upstream.json',
                'Only upstream.json may be committed')
        command(['git', 'config', 'user.name', 'github-actions[bot]'], cwd=root)
        command(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'], cwd=root)
        command(['git', 'commit', '-m', 'chore: pin official Hermes ' + candidate['releaseTag']], cwd=root)
        sha = exact_sha(command(['git', 'rev-parse', 'HEAD'], cwd=root))
        require(sha != expected_base, 'Expected a new pin commit')
        require(remote_main(api) == expected_base, 'main changed before push')
        # No force, merge, rebase or git pull: a concurrent forward update rejects
        # this push rather than replacing any other maintainer's work.
        command(['git', 'push', 'origin', 'HEAD:refs/heads/main'], cwd=root)
        require(remote_main(api) == sha, 'Pushed main SHA could not be verified')
        remote_pin = api(f'repos/{DOWNSTREAM}/contents/upstream.json?ref={sha}')
        require(isinstance(remote_pin, dict) and remote_pin.get('encoding') == 'base64'
                and isinstance(remote_pin.get('content'), str), 'Cannot read back pushed pin')
        try:
            pushed = parse_json(base64.b64decode(remote_pin['content'].replace('\n', ''), validate=True))
        except (ValueError, binascii.Error) as exc:
            raise UpdateError('Invalid pushed pin readback') from exc
        require(pushed == fresh_candidate, 'Pushed pin readback mismatch')
    dispatch_build(api, root, sha)
    return sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    plan = commands.add_parser('plan', help='Read-only GitHub discovery; write local JSON evidence')
    plan.add_argument('--output', type=Path, required=True)
    plan.add_argument('--metadata', type=Path, required=True)
    apply = commands.add_parser('apply', help='CI write job only: revalidate, commit, push, dispatch')
    apply.add_argument('--candidate', type=Path, required=True)
    apply.add_argument('--metadata', type=Path, required=True)
    apply.add_argument('--expected-base', required=True)
    args = parser.parse_args(argv)
    try:
        api = GitHubAPI()
        if args.operation == 'plan':
            pin = parse_json((ROOT / 'upstream.json').read_text())
            base = exact_sha(command(['git', 'rev-parse', 'HEAD'], cwd=ROOT))
            candidate, metadata = plan_update(api, pin, base)
            for path, data in [(args.output, candidate), (args.metadata, metadata)]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=2) + '\n')
            print(json.dumps(metadata, indent=2))
        else:
            sha = apply_plan(api, ROOT, parse_json(args.candidate.read_text()),
                             parse_json(args.metadata.read_text()), args.expected_base)
            print('Verified pin/build decision at ' + sha)
    except (UpdateError, OSError) as exc:
        print('Upstream update refused: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
