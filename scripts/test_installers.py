#!/usr/bin/env python3
"""Exercise public copy installers on real paths and filesystem boundaries."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def bash_path() -> str | None:
    if os.name == "nt":
        for path in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/bin/bash.exe",
            Path("C:/msys64/usr/bin/bash.exe"),
        ):
            if path.is_file():
                return str(path)
        return None
    return shutil.which("bash")


class CopyInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-install-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "checkout"
        self.scripts = self.checkout / "scripts"
        self.scripts.mkdir(parents=True)
        self.source = self.checkout / "app/Test"
        self.source.mkdir(parents=True)
        (self.source / "app.json").write_text('{"slug":"test"}\n', encoding="utf-8")
        (self.source / ".hidden").write_bytes(b"hidden\x00content")
        (self.source / "nested").mkdir()
        (self.source / "nested/notes.txt").write_text(
            "Türkçe 日本語\n", encoding="utf-8"
        )
        for name in ("install-example.sh", "install-example.ps1"):
            shutil.copyfile(SOURCE / name, self.scripts / name)

    def run_installer(self, shell: str, destination: Path | str, *, env=None):
        if shell == "bash":
            executable = bash_path()
            if not executable:
                self.skipTest("Bash is unavailable")
            command = [
                executable,
                str(self.scripts / "install-example.sh"),
                "Test",
                str(destination),
            ]
            if env and env.get("FORGE_TEST_BIN"):
                command = [
                    executable,
                    "-c",
                    'prefix="$FORGE_TEST_BIN"; '
                    'if command -v cygpath >/dev/null 2>&1; then prefix=$(cygpath -u "$prefix"); fi; '
                    'export PATH="$prefix:$PATH"; exec bash "$@"',
                    "test-installer",
                    *command[1:],
                ]
        else:
            executable = shutil.which("pwsh")
            if not executable:
                self.skipTest("PowerShell is unavailable")
            command = [
                executable,
                "-NoProfile",
                "-File",
                str(self.scripts / "install-example.ps1"),
                "-Name",
                "Test",
                "-Destination",
                str(destination),
            ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            env=env,
        )

    def assert_copied(self, destination: Path):
        expected = {
            path.relative_to(self.source): hashlib.sha256(path.read_bytes()).digest()
            for path in self.source.rglob("*")
            if path.is_file()
        }
        actual = {
            path.relative_to(destination / "Test"): hashlib.sha256(
                path.read_bytes()
            ).digest()
            for path in (destination / "Test").rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, expected)
        self.assertFalse(list(destination.glob(".forge-example*")))

    def test_bash_drive_path_spaces_and_existing_target(self):
        destination = self.root / "server with spaces/app"
        result = self.run_installer("bash", destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_copied(destination)
        repeated = self.run_installer("bash", destination)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("Refusing to overwrite", repeated.stderr)
        self.assert_copied(destination)

    def test_powershell_literal_brackets_and_existing_target(self):
        destination = self.root / "server [private]/app"
        result = self.run_installer("pwsh", destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_copied(destination)
        repeated = self.run_installer("pwsh", destination)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("Refusing to overwrite", repeated.stderr)
        self.assert_copied(destination)

    def test_bash_trailing_slash_link_parent_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "link"
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
            )
            if result.returncode:
                self.skipTest("Junction creation unavailable")
        else:
            link.symlink_to(outside, target_is_directory=True)
        result = self.run_installer("bash", str(link).replace("\\", "/") + "/")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic-link destination parent", result.stderr)
        self.assertFalse((outside / "Test").exists())

    def test_bash_late_target_is_preserved_without_nested_copy(self):
        wrapper = self.root / "wrapper"
        wrapper.mkdir()
        fake_move = wrapper / "mv"
        fake_move.write_text(
            "#!/usr/bin/env bash\n"
            "destination=${@: -1}\n"
            'mkdir -p -- "${destination%/}/Test"\n'
            'printf user-data > "${destination%/}/Test/user.txt"\n'
            'exec /usr/bin/mv "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        fake_move.chmod(0o755)
        destination = self.root / "late-target"
        environment = {**os.environ, "FORGE_TEST_BIN": str(wrapper)}
        result = self.run_installer("bash", destination, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Target appeared", result.stderr)
        self.assertEqual((destination / "Test/user.txt").read_bytes(), b"user-data")
        self.assertFalse((destination / "Test/Test").exists())
        self.assertFalse((destination / "Test/app.json").exists())
        self.assertFalse(list(destination.glob(".forge-example*")))


if __name__ == "__main__":
    unittest.main()
