#!/usr/bin/env python3
"""Reject untrusted manifest paths and filesystem link boundaries before hashing."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "manifest", Path(__file__).with_name("check_manifest.py")
)
manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)


class ManifestBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-manifest-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "MANIFEST.sha256"
        context = patch.multiple(manifest, ROOT=self.root, MANIFEST=self.path)
        context.start()
        self.addCleanup(context.stop)

    def test_invalid_records_do_not_hash_external_paths(self):
        for relative, digest in (
            ("../outside.txt", "a" * 64),
            ("/absolute/file", "a" * 64),
            ("scripts\\tool.py", "a" * 64),
            ("C:/outside/private.txt", "a" * 64),
            ("README.md:alternate-stream", "a" * 64),
            (".env", "a" * 64),
            ("README.md", "not-a-sha256"),
        ):
            with self.subTest(path=relative):
                self.path.write_text(f"{digest}  {relative}\n", encoding="utf-8")
                with (
                    patch.object(manifest, "digest") as digest_file,
                    self.assertRaises(SystemExit),
                ):
                    manifest.main([])
                digest_file.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_junction_parent_never_hashes_external_content(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"outside fixture")
        link = self.root / "app"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        )
        if created.returncode:
            self.skipTest("Junction creation unavailable")
        try:
            with (
                patch.object(manifest.subprocess, "check_output", side_effect=OSError),
                self.assertRaisesRegex(ValueError, "junction"),
            ):
                manifest.release_files()
        finally:
            link.rmdir()


if __name__ == "__main__":
    unittest.main()
