"""Manifest validation must reject unsafe records and filesystem boundaries before reading."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "editor_manifest", SOURCE / "scripts/check_manifest.py"
)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class ManifestSafetyTests(unittest.TestCase):
    def setUp(self):
        base = SOURCE / "build/review/manifest-tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base, prefix="case-")
        self.folder = Path(self.temporary.name).resolve()
        assert self.folder.is_relative_to(base.resolve())
        self.addCleanup(self.temporary.cleanup)
        self.root = self.folder / "source"
        self.root.mkdir()
        self.path = self.root / "MANIFEST.sha256"
        (self.folder / "outside.txt").write_bytes(b"private fixture")
        context = patch.multiple(manifest, ROOT=self.root, MANIFEST=self.path)
        context.start()
        self.addCleanup(context.stop)

    def test_invalid_records_are_rejected_before_scan_or_hash(self):
        cases = [
            (relative, "a" * 64)
            for relative in (
                "../outside.txt",
                "/absolute/file",
                "scripts\\tool.py",
                "C:/outside/private.txt",
                "README.md:alternate-stream",
                "nested/C:drive.txt",
                ".env",
                "secrets/.env.production",
                "build/private.txt",
                "a//b",
                "a/./b",
            )
        ] + [
            ("README.md", digest)
            for digest in ("g" * 64, "a" * 63, "A" * 64, "not-a-sha256")
        ]
        for relative, digest in cases:
            with self.subTest(path=relative, digest=digest):
                self.path.write_text(f"{digest}  {relative}\n", encoding="utf-8")
                with (
                    patch.object(manifest, "digest") as hash_file,
                    patch.object(manifest, "release_files", return_value=set()) as scan,
                ):
                    with self.assertRaises(SystemExit):
                        manifest.main([])
                scan.assert_not_called()
                hash_file.assert_not_called()

    def test_normal_fallback_manifest_and_ignored_build_work(self):
        (self.root / "README.md").write_bytes(b"release fixture")
        (self.root / "build").mkdir()
        (self.root / "build/private.txt").write_bytes(b"ignored")
        (self.root / ".env").write_bytes(b"ignored secret")
        with patch.object(manifest.subprocess, "check_output", side_effect=OSError):
            manifest.main(["--write"])
            self.assertEqual(manifest.main([]), 0)
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 1)

    def test_symbolic_parent_is_rejected_before_hash(self):
        outside = self.folder / "external"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"outside fixture")
        link = self.root / "linked-source"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlink creation unavailable")
        self.addCleanup(link.unlink)
        self.path.write_text(
            "a" * 64 + "  linked-source/private.txt\n", encoding="utf-8"
        )
        with (
            patch.object(
                manifest.subprocess,
                "check_output",
                side_effect=[str(self.root), b"linked-source/private.txt\0"],
            ),
            patch.object(manifest, "digest") as hash_file,
        ):
            with self.assertRaisesRegex(ValueError, "link|junction"):
                manifest.main([])
        hash_file.assert_not_called()

    def junction(self, name):
        outside = self.folder / "external"
        outside.mkdir(exist_ok=True)
        (outside / "private.txt").write_bytes(b"outside fixture")
        link = self.root / name
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        )
        if result.returncode:
            self.skipTest("Junction creation unavailable")
        self.addCleanup(link.rmdir)
        self.assertFalse(link.is_symlink())
        return link

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_fallback_rejects_junction_without_descending(self):
        link = self.junction("linked-source")
        real_scan = os.scandir

        def checked_scan(path):
            self.assertNotEqual(Path(path), link, "The fallback entered a junction")
            return real_scan(path)

        with (
            patch.object(manifest.subprocess, "check_output", side_effect=OSError),
            patch("os.scandir", side_effect=checked_scan),
        ):
            with self.assertRaisesRegex(ValueError, "link|junction"):
                manifest.release_files()

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_git_candidate_checks_parent_before_hash(self):
        self.junction("linked-source")
        self.path.write_text(
            "a" * 64 + "  linked-source/private.txt\n", encoding="utf-8"
        )
        with (
            patch.object(
                manifest.subprocess,
                "check_output",
                side_effect=[str(self.root), b"linked-source/private.txt\0"],
            ),
            patch.object(manifest, "digest") as hash_file,
        ):
            with self.assertRaisesRegex(ValueError, "link|junction"):
                manifest.main([])
        hash_file.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_ignored_build_junction_does_not_block_release(self):
        self.junction("build")
        (self.root / "README.md").write_bytes(b"release fixture")
        with patch.object(manifest.subprocess, "check_output", side_effect=OSError):
            self.assertEqual(manifest.release_files(), {"README.md"})


if __name__ == "__main__":
    unittest.main()
