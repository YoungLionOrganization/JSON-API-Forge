"""Validate the complete Editor release and bind every binary to its build commit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

PLATFORMS = ('linux-x64', 'linux-arm64', 'windows-x64', 'windows-arm64', 'macos-x64', 'macos-arm64')


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(folder: Path, sha: str, run_id: str) -> dict:
    if not re.fullmatch('[0-9a-f]{40}', sha) or not str(run_id).isdigit():
        raise ValueError('Release requires an exact commit SHA and workflow run ID')
    proof = json.loads((folder / 'release-build.json').read_text(encoding='utf-8'))
    if proof.get('sha') != sha or str(proof.get('run_id')) != str(run_id) or proof.get('version') != '0.5.2' or proof.get('branch') != 'Editor':
        raise ValueError('Release provenance does not match the selected Editor build')
    expected = {}
    for platform in PLATFORMS:
        prefix = 'JSON-API-Forge-Editor-v0.5.2-' + platform
        extension = '.exe' if platform.startswith('windows') else '.dmg' if platform.startswith('macos') else '.run'
        for name in (prefix + '.zip', prefix + '-setup' + extension):
            path = folder / name
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Missing release asset: ' + name)
            checksum = (folder / (name + '.sha256')).read_text().split()
            if len(checksum) != 2 or checksum[1].lstrip('*') != name or checksum[0] != digest(path):
                raise ValueError('Release checksum mismatch: ' + name)
            expected[name] = checksum[0]
            if name.endswith('.zip'):
                with zipfile.ZipFile(path) as archive:
                    members = archive.namelist()
                    if any(Path(member).is_absolute() or '..' in Path(member).parts or '\\' in member for member in members):
                        raise ValueError('Unsafe portable archive: ' + name)
                    binary = ('JSON-API-Forge-Editor.app/Contents/MacOS/JSON-API-Forge-Editor' if platform.startswith('macos')
                              else 'bin/JSON-API-Forge-Editor' + ('.exe' if platform.startswith('windows') else ''))
                    if binary not in members:
                        raise ValueError('Missing portable executable: ' + name)
            else:
                with path.open('rb') as stream:
                    header = stream.read(4)
                    if extension == '.exe' and not header.startswith(b'MZ'):
                        raise ValueError('Invalid Windows installer')
                    if extension == '.run' and header != b'\x7fELF':
                        raise ValueError('Invalid Linux installer')
                    if extension == '.dmg':
                        stream.seek(-512, 2)
                        if stream.read(4) != b'koly':
                            raise ValueError('Invalid macOS installer')
    actual_binaries = {p.name for p in folder.iterdir() if p.suffix in ('.zip', '.exe', '.run', '.dmg')}
    if actual_binaries != set(expected) or proof.get('assets') != expected or proof.get('call_client_revision') != 2:
        raise ValueError('Release asset set or call-client contract does not match')
    return expected


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    assets = validate(args.folder, args.sha, args.run_id)
    print(f'Validated {len(assets)} Editor binaries with exact build provenance and checksums')
