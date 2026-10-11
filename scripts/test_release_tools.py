#!/usr/bin/env python3
"""Regression tests for deterministic and bounded example delivery."""

import hashlib
import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "bundle", Path(__file__).with_name("build-example-bundle.py")
)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class ReleaseToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "app/Test").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        for relative in [
            *bundle.BUNDLE_DOCUMENTS,
            "app/Test/app.json",
            "EXAMPLE_APPS.md",
            "scripts/install-example.sh",
            "scripts/install-example.ps1",
            "scripts/taskboard_client.py",
        ]:
            (self.root / relative).write_text("{}\n", encoding="utf-8")
        self.context = patch.object(bundle, "ROOT", self.root)
        self.context.start()
        self.addCleanup(self.context.stop)

    def test_determinism_and_embedded_hashes(self):
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            first, second = self.root / "first.zip", self.root / "second.zip"
            bundle.build(first)
            bundle.build(second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(set(bundle.BUNDLE_DOCUMENTS).issubset(archive.namelist()))
            for record in archive.read("SHA256SUMS").decode().splitlines():
                digest, name = record.split("  ", 1)
                self.assertEqual(digest, hashlib.sha256(archive.read(name)).hexdigest())
            self.assertEqual(
                (archive.getinfo("scripts/install-example.sh").external_attr >> 16)
                & 0o777,
                0o755,
            )

    def test_missing_license_fails_before_archive_creation(self):
        (self.root / "LICENSE").unlink()
        output = self.root / "missing-license.zip"
        with self.assertRaises(FileNotFoundError):
            bundle.build(output)
        self.assertFalse(output.exists())

    def test_symlinks_collisions_and_size_are_rejected(self):
        link = self.root / "app/Test/link"
        try:
            link.symlink_to(self.root / "EXAMPLE_APPS.md")
        except OSError:
            if os.name != "nt":
                raise
        else:
            with self.assertRaisesRegex(RuntimeError, "symlinks"):
                bundle.build(self.root / "bad.zip")
            link.unlink()
        (self.root / "app/Test/APP.json").write_text("{}", encoding="utf-8")
        if not (self.root / "app/Test/APP.json").samefile(
            self.root / "app/Test/app.json"
        ):
            with self.assertRaisesRegex(RuntimeError, "colliding"):
                bundle.build(self.root / "bad.zip")
            (self.root / "app/Test/APP.json").unlink()
        # Exercise a duplicate source on both case-sensitive and insensitive FS.
        with (
            patch.object(
                bundle, "BUNDLE_DOCUMENTS", (*bundle.BUNDLE_DOCUMENTS, "LICENSE")
            ),
            self.assertRaisesRegex(RuntimeError, "colliding"),
        ):
            bundle.build(self.root / "bad.zip")
        with (
            patch.object(bundle, "MAX_FILE_BYTES", 1),
            self.assertRaisesRegex(RuntimeError, "file exceeds"),
        ):
            bundle.build(self.root / "bad.zip")
        self.assertFalse((self.root / "bad.zip").exists())
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_timestamp_and_output_boundaries(self):
        with self.assertRaisesRegex(RuntimeError, "inside app"):
            bundle.build(self.root / "app/output.zip")
        for epoch, year in [("0", 1980), ("999999999999", 2107)]:
            with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": epoch}):
                bundle.build(self.root / "epoch.zip")
            with zipfile.ZipFile(self.root / "epoch.zip") as archive:
                self.assertEqual(archive.infolist()[0].date_time[0], year)

    def test_changed_source_preserves_existing_output(self):
        output = self.root / "existing.zip"
        output.write_bytes(b"previous verified archive")
        sources = bundle._sources()
        (self.root / "app/Test/app.json").write_text(
            "changed contents\n", encoding="utf-8"
        )
        with (
            patch.object(bundle, "_sources", return_value=sources),
            self.assertRaisesRegex(RuntimeError, "source changed"),
        ):
            bundle.build(output)
        self.assertEqual(output.read_bytes(), b"previous verified archive")
        self.assertFalse(list(self.root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
