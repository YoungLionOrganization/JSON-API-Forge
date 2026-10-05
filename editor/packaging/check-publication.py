"""Read-only, exact-commit publication readiness check. Does not create tags/releases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

REPO = 'YoungLionOrganization/JSON-API-Forge'
WORKFLOWS = ('editor-build.yml', 'editor-codeql.yml')
PLATFORMS = ('linux-x64', 'linux-arm64', 'windows-x64', 'windows-arm64', 'macos-x64', 'macos-arm64')


def gh(*arguments):
    result = subprocess.run(['gh', *arguments], text=True, capture_output=True, check=True)
    return result.stdout


def latest_success(runs, sha):
    selected = [run for run in runs if run['head_sha'] == sha and run['head_branch'] == 'Editor' and run['event'] == 'push']
    if not selected:
        raise ValueError('No Editor push workflow run exists for this commit')
    latest = max(selected, key=lambda run: (run['id'], run['run_attempt']))
    if latest['status'] != 'completed' or latest['conclusion'] != 'success':
        raise ValueError('Latest workflow attempt is not successful')
    return latest


def verify_proof(proof, sha, run):
    expected = set()
    for platform in PLATFORMS:
        prefix = 'JSON-API-Forge-Editor-v0.5.2-' + platform
        suffix = '.exe' if platform.startswith('windows') else '.dmg' if platform.startswith('macos') else '.run'
        expected.update((prefix + '.zip', prefix + '-setup' + suffix))
    if (proof.get('sha') != sha or proof.get('version') != '0.5.2' or proof.get('branch') != 'Editor'
        or str(proof.get('run_id')) != str(run['id']) or str(proof.get('run_attempt')) != str(run['run_attempt'])
        or proof.get('call_client_revision') != 2 or set(proof.get('assets', {})) != expected
        or any(not re.fullmatch('[0-9a-f]{64}', value) for value in proof['assets'].values())):
        raise ValueError('Publication proof does not match this complete, successful build')


def check(sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Select an immutable 40-character Editor commit SHA')
    gates = {}
    for workflow in WORKFLOWS:
        response = json.loads(gh('api', f'repos/{REPO}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100'))
        if len(response['workflow_runs']) >= 100:
            raise ValueError('Workflow history exceeds the bounded verification limit')
        gates[workflow] = latest_success(response['workflow_runs'], sha)
    build = gates['editor-build.yml']
    artifacts = json.loads(gh('api', f'repos/{REPO}/actions/runs/{build["id"]}/artifacts?per_page=100'))['artifacts']
    expected = {'Editor-v0.5.2-release-proof', 'JSON-API-Forge-Editor-v0.5.2-release-assets'}
    if not expected.issubset({artifact['name'] for artifact in artifacts if not artifact['expired']}):
        raise ValueError('Verified publication artifacts are missing or expired')
    with tempfile.TemporaryDirectory(prefix='forge-publication-proof-') as temporary:
        gh('run', 'download', str(build['id']), '--repo', REPO, '--name', 'Editor-v0.5.2-release-proof', '--dir', temporary)
        proof = json.loads((Path(temporary) / 'release-build.json').read_text(encoding='utf-8'))
    verify_proof(proof, sha, build)
    for workflow, run in gates.items():
        current = json.loads(gh('api', f'repos/{REPO}/actions/runs/{run["id"]}'))
        if latest_success([current], sha)['run_attempt'] != run['run_attempt']:
            raise ValueError('Workflow attempt changed during verification')
    return {'status': 'ready', 'sha': sha, 'tag': 'editor-v0.5.2', 'version': '0.5.2',
            'workflows': {name: run['html_url'] for name, run in gates.items()}, 'assets': proof['assets']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.sha), indent=2))
