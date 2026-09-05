"""Validate native CI provenance before a credentialed release job trusts artifacts."""
import argparse
import json
from pathlib import Path
import re
import subprocess

REPO = 'frankhommers/hermes-desktop-builds'
REQUIRED_JOBS = {'scripts', 'native-darwin-arm64', 'native-darwin-x64',
                 'native-win32-x64', 'native-linux-x64'}


def validate_build_receipt(receipt, run):
    if not isinstance(run, str) or not re.fullmatch(r'[1-9][0-9]*', run):
        raise ValueError('Exact numeric run ID required')
    if not isinstance(receipt, dict):
        raise ValueError('Missing CI receipt')
    expected = {
        'id': int(run), 'head_branch': 'main', 'event': 'workflow_dispatch',
        'path': '.github/workflows/build.yml', 'status': 'completed',
        'conclusion': 'success', 'html_url': f'https://github.com/{REPO}/actions/runs/{run}',
    }
    for key, value in expected.items():
        if type(receipt.get(key)) is not type(value) or receipt[key] != value:
            raise ValueError(f'Invalid build receipt: {key}')
    for key in ('repository', 'head_repository'):
        if not isinstance(receipt.get(key), dict) or receipt[key].get('full_name') != REPO:
            raise ValueError(f'Build must originate from this repository: {key}')
    if not isinstance(receipt.get('head_sha'), str) or not re.fullmatch('[0-9a-f]{40}', receipt['head_sha']):
        raise ValueError('Invalid build commit')
    if type(receipt.get('run_attempt')) is not int or receipt['run_attempt'] < 1:
        raise ValueError('Invalid build attempt')
    return receipt


def validate_build_jobs(payload, sha):
    if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
        raise ValueError('Missing native job list')
    rows = payload['jobs']
    if type(payload.get('total_count')) is not int or payload['total_count'] != len(rows):
        raise ValueError('Incomplete native job list')
    names = [row.get('name') for row in rows]
    if len(names) != len(set(names)) or set(names) != REQUIRED_JOBS:
        raise ValueError('Expected exactly the scripts gate and all four native jobs')
    for row in rows:
        if row.get('status') != 'completed' or row.get('conclusion') != 'success' or row.get('head_sha') != sha:
            raise ValueError('Native job did not succeed at the expected commit: ' + str(row.get('name')))


def validate_release_order(version, releases):
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid distribution version')
    proposed = tuple(map(int, version.split('.')))
    for release in releases:
        tag = release.get('tag_name', '')
        if release.get('draft') is False and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+', tag):
            if tuple(map(int, tag[1:].split('.'))) > proposed:
                raise ValueError('Refusing to publish an older build over a newer release')


def read_api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint]))


def prepare_verified_artifacts(run):
    if not isinstance(run, str) or not re.fullmatch(r'[1-9][0-9]*', run):
        raise ValueError('Exact numeric run ID required')
    receipt = validate_build_receipt(read_api(f'repos/{REPO}/actions/runs/{run}'), run)
    sha = receipt['head_sha']
    # The initial checkout is main, not a fork/PR-controlled revision. Establish
    # ancestry BEFORE executing any scripts from the build revision.
    subprocess.run(['git', 'merge-base', '--is-ancestor', sha, 'HEAD'], check=True)
    endpoint = f'repos/{REPO}/actions/runs/{run}/attempts/{receipt["run_attempt"]}/jobs?per_page=100'
    validate_build_jobs(read_api(endpoint), sha)
    # Build and publication share exactly the same pin, patch set and validator,
    # even if unrelated commits advanced main while native jobs were running.
    subprocess.run(['git', 'checkout', '--detach', sha], check=True)
    subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], check=True)
    Path('out').mkdir(exist_ok=True)
    Path('out/build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    subprocess.run(['gh', 'run', 'download', run, '--repo', REPO, '--pattern', 'desktop-*',
                    '--dir', 'out/downloads'], check=True)
    after = validate_build_receipt(read_api(f'repos/{REPO}/actions/runs/{run}'), run)
    if (after['run_attempt'], after['head_sha']) != (receipt['run_attempt'], sha):
        raise ValueError('Build changed while artifacts were being downloaded')
    subprocess.run(['python3', 'scripts/release.py', '--downloads', 'out/downloads',
                    '--destination', 'out/release', '--run-url', receipt['html_url']], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    # Validate before putting an identifier into an API path, not only after lookup.
    if not re.fullmatch(r'[1-9][0-9]*', args.run):
        parser.error('Exact numeric run ID required')
    prepare_verified_artifacts(args.run)
