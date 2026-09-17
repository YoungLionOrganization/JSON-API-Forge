"""Regression checks for installer layout, payload and DMG cleanup."""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
IFW = ROOT / 'editor/packaging/qtifw'


class InstallerPackagingTests(unittest.TestCase):
    def test_wizard_has_no_unbounded_header_pixmap(self):
        for path in sorted((IFW / 'config').glob('config*.xml')):
            with self.subTest(path=path.name):
                config = ET.parse(path).getroot()
                for tag in ('Logo', 'Banner', 'Watermark', 'Background', 'PageListPixmap'):
                    self.assertIsNone(config.find(tag))
                self.assertTrue(config.findtext('InstallerWindowIcon'))
                self.assertLessEqual(int(config.findtext('WizardDefaultWidth')), 800)
                self.assertLessEqual(int(config.findtext('WizardDefaultHeight')), 600)
                self.assertTrue(config.findtext('RunProgram'))
                stylesheet = IFW / 'config' / config.findtext('StyleSheet', '')
                self.assertTrue(stylesheet.is_file(), 'Theme must be shipped for every platform')

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by Linux/macOS')
    def test_linux_stages_theme_and_installs_without_admin_rights(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            stage = folder / 'stage with spaces'
            (stage / 'bin').mkdir(parents=True)
            for name in ('bin/JSON-API-Forge-Editor', 'json-api-forge-editor'):
                path = stage / name
                path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
            tool = folder / 'binarycreator'
            tool.write_text('''#!/usr/bin/env python3
import pathlib, sys, xml.etree.ElementTree as ET
args = sys.argv
config = pathlib.Path(args[args.index('-c') + 1])
theme = ET.parse(config).getroot().findtext('StyleSheet')
assert (config.parent / theme).is_file(), 'missing staged theme'
assert ET.parse(config).getroot().findtext('TargetDir').startswith('@HomeDir@/')
packages = pathlib.Path(args[args.index('-p') + 1])
meta = packages / 'dev.jsonapiforge.editor/meta/package.xml'
assert ET.parse(meta).getroot().findtext('RequiresAdminRights') == 'false'
assert (packages / 'dev.jsonapiforge.editor/data/bin/JSON-API-Forge-Editor').is_file()
pathlib.Path(args[-1]).write_bytes(b'x' * 1000001)
''')
            tool.chmod(0o755)
            run = subprocess.run(['bash', str(IFW / 'build-installer.sh'), str(stage),
                                  str(folder / 'setup.run'), 'linux', str(tool)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            # Linux staging must not change the shared Windows/macOS metadata.
            metadata = IFW / 'packages/dev.jsonapiforge.editor/meta/package.xml'
            self.assertEqual(ET.parse(metadata).getroot().findtext('RequiresAdminRights'), 'true')

    def run_dmg_fixture(self, folder, failures=0, verify_exit=0):
        app = folder / 'Editor Setup.app'
        (app / 'Contents/MacOS').mkdir(parents=True)
        log = folder / 'calls.jsonl'
        tool = folder / 'hdiutil'
        tool.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
log = pathlib.Path(os.environ['CALL_LOG'])
calls = log.read_text().splitlines() if log.exists() else []
with log.open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'verify': sys.exit(int(os.environ['VERIFY_EXIT']))
assert sys.argv[1] == 'create'
source = pathlib.Path(sys.argv[sys.argv.index('-srcfolder') + 1])
assert (source / 'Editor Setup.app/Contents/MacOS').is_dir(), 'launchable app lost during retry'
if len(calls) < int(os.environ['FAILURES']):
    print('hdiutil: create failed - Resource busy', file=sys.stderr)
    sys.exit(16)
pathlib.Path(sys.argv[-1]).write_bytes(b'disk image fixture')
''')
        tool.chmod(0o755)
        sleep = folder / 'sleep'; sleep.write_text('#!/bin/sh\nexit 0\n'); sleep.chmod(0o755)
        env = dict(os.environ, PATH=str(folder) + os.pathsep + os.environ['PATH'],
                   CALL_LOG=str(log), FAILURES=str(failures), VERIFY_EXIT=str(verify_exit))
        run = subprocess.run(['bash', str(IFW / 'create-dmg.sh'), str(app), str(folder / 'setup.dmg')],
                             env=env, capture_output=True, text=True)
        return run, [json.loads(line) for line in log.read_text().splitlines()]

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by macOS')
    def test_dmg_retries_busy_image_and_verifies_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, calls = self.run_dmg_fixture(Path(tmp), failures=2)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual([call[0] for call in calls], ['create', 'create', 'create', 'verify'])
            self.assertIn('Resource busy', run.stderr)

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by macOS')
    def test_dmg_creation_failure_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, calls = self.run_dmg_fixture(Path(tmp), failures=3)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual([call[0] for call in calls], ['create'] * 3)
            self.assertIn('failed after three attempts', run.stderr)

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by macOS')
    def test_dmg_verification_failure_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, calls = self.run_dmg_fixture(Path(tmp), verify_exit=7)
            self.assertEqual(run.returncode, 7)
            self.assertEqual([call[0] for call in calls], ['create', 'verify'])

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by Linux/macOS')
    def test_installer_rejects_empty_stage_before_binarycreator(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'empty'; stage.mkdir()
            tool = Path(tmp) / 'binarycreator'
            tool.write_text('#!/bin/sh\necho unexpected-builder-call\nexit 0\n')
            tool.chmod(0o755)
            run = subprocess.run(['bash', str(IFW / 'build-installer.sh'), str(stage), str(Path(tmp) / 'setup.run'), 'linux', str(tool)], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertNotIn('unexpected-builder-call', run.stdout)

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by macOS')
    def test_busy_dmg_retry_and_forced_detach(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            tool = folder / 'hdiutil'
            tool.write_text('#!/bin/sh\necho "$*" >> "$CALL_LOG"\n[ "$2" = "-force" ]\n')
            tool.chmod(0o755)
            sleep = folder / 'sleep'; sleep.write_text('#!/bin/sh\nexit 0\n'); sleep.chmod(0o755)
            log = folder / 'calls'
            env = dict(os.environ, PATH=str(folder) + os.pathsep + os.environ['PATH'], CALL_LOG=str(log))
            run = subprocess.run(['bash', str(IFW / 'detach-dmg.sh'), '/tmp/Qt IFW image'], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(log.read_text().splitlines(), ['detach /tmp/Qt IFW image'] * 3 + ['detach -force /tmp/Qt IFW image'])

    @unittest.skipIf(os.name == 'nt', 'Bash helper is used by macOS')
    def test_successful_detach_does_not_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / 'hdiutil'
            tool.write_text('#!/bin/sh\necho "$*"\nexit 0\n'); tool.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ['PATH'])
            run = subprocess.run(['bash', str(IFW / 'detach-dmg.sh'), '/tmp/image'], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stdout.strip(), 'detach /tmp/image')


if __name__ == '__main__':
    unittest.main()
