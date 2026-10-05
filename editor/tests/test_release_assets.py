"""Release provenance and completeness must fail closed before publication."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('editor_release', ROOT / 'editor/packaging/validate-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseAssetsTests(unittest.TestCase):
    def test_latest_failure_cannot_fall_back_to_old_success(self):
        spec = importlib.util.spec_from_file_location('editor_publication', ROOT / 'editor/packaging/check-publication.py')
        publication = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publication)
        good = dict(id=1, run_attempt=1, head_sha='a' * 40, head_branch='Editor', event='push', status='completed', conclusion='success')
        self.assertEqual(publication.latest_success([good], 'a' * 40), good)
        with self.assertRaisesRegex(ValueError, 'not successful'):
            publication.latest_success([good, {**good, 'id': 2, 'conclusion': 'failure'}], 'a' * 40)
        with self.assertRaisesRegex(ValueError, 'not successful'):
            publication.latest_success([good, {**good, 'run_attempt': 2, 'status': 'in_progress'}], 'a' * 40)

    def test_complete_assets_provenance_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            hashes = {}
            for platform in release.PLATFORMS:
                prefix = 'JSON-API-Forge-Editor-v0.5.2-' + platform
                archive = folder / (prefix + '.zip')
                binary = 'JSON-API-Forge-Editor.app/Contents/MacOS/JSON-API-Forge-Editor' if platform.startswith('macos') else 'bin/JSON-API-Forge-Editor' + ('.exe' if platform.startswith('windows') else '')
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(binary, b'executable fixture')
                suffix = '.exe' if platform.startswith('windows') else '.dmg' if platform.startswith('macos') else '.run'
                installer = folder / (prefix + '-setup' + suffix)
                installer.write_bytes(b'MZfixture' if suffix == '.exe' else b'\x7fELFfixture' if suffix == '.run' else b'koly' + b'\0' * 508)
                for path in (archive, installer):
                    hashes[path.name] = release.digest(path)
                    (folder / (path.name + '.sha256')).write_text(hashes[path.name] + '  ' + path.name + '\n')
            proof = dict(sha='a' * 40, run_id='12', version='0.5.2', branch='Editor', call_client_revision=2, assets=hashes)
            (folder / 'release-build.json').write_text(json.dumps(proof))
            self.assertEqual(len(release.validate(folder, 'a' * 40, '12')), 12)
            with self.assertRaisesRegex(ValueError, 'provenance'):
                release.validate(folder, 'b' * 40, '12')
            with self.assertRaisesRegex(ValueError, 'provenance'):
                release.validate(folder, 'a' * 40, '13')
            installer.write_bytes(b'corrupted installer')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                release.validate(folder, 'a' * 40, '12')


if __name__ == '__main__':
    unittest.main()
