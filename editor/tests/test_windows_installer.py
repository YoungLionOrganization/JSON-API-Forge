"""Real Windows install/repair/legacy-upgrade/downgrade contract in disposable roots."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


def fixture_version(path: Path, minor: int, patch: int):
    """Change only the disposable executable's version resource; never execute it."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_bool]
    kernel.BeginUpdateResourceW.restype = ctypes.c_void_p
    kernel.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_ushort, ctypes.c_void_p, ctypes.c_uint]
    kernel.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    body = struct.pack('<13I', 0xFEEF04BD, 0x10000, minor, patch << 16,
                       minor, patch << 16, 0x3f, 0, 0x40004, 1, 0, 0, 0)
    key = 'VS_VERSION_INFO\0'.encode('utf-16-le')
    resource = struct.pack('<3H', 92, len(body), 0) + key + b'\0\0' + body
    assert len(resource) == 92
    buffer = ctypes.create_string_buffer(resource)
    handle = kernel.BeginUpdateResourceW(str(path), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    ok = kernel.UpdateResourceW(handle, 16, 1, 0x409, buffer, len(resource))
    if not kernel.EndUpdateResourceW(handle, not ok) or not ok:
        raise ctypes.WinError(ctypes.get_last_error())


@unittest.skipUnless(os.name == 'nt' and os.environ.get('FORGE_WINDOWS_INSTALLER'), 'Dedicated Windows installer contract')
class WindowsInstallerTests(unittest.TestCase):
    def test_install_repair_upgrade_and_downgrade(self):
        import winreg

        installer = Path(os.environ['FORGE_WINDOWS_INSTALLER']).resolve()
        payload = Path(os.environ['FORGE_INSTALLER_PAYLOAD']).resolve()
        product = os.environ.get('FORGE_INSTALLER_PRODUCT', 'JSON API Forge Editor')
        base = ROOT / 'build/review/windows-installer-tests'
        base.mkdir(parents=True, exist_ok=True)
        expected = hashlib.sha256((payload / 'bin/JSON-API-Forge-Editor.exe').read_bytes()).hexdigest()
        legacy_key = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ForgeFixture-' + str(uuid.uuid4())
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            folder = Path(temporary).resolve()
            self.assertTrue(folder.is_relative_to(base.resolve()))
            target = folder / 'Installed Editor'
            executable = target / 'bin/JSON-API-Forge-Editor.exe'

            def install(mode, label):
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
                log = folder / (label + '.log')
                result = subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                    '/NORESTART', '/CURRENTUSER', '/NOICONS', '/DIR=' + str(target),
                    '/FORGEMODE=' + mode, '/LOG=' + str(log)], startupinfo=startup, timeout=180)
                return result.returncode, log.read_text(encoding='utf-8-sig', errors='replace')

            def verify_payload():
                self.assertEqual(hashlib.sha256(executable.read_bytes()).hexdigest(), expected)

            try:
                code, _ = install('install', 'fresh')
                self.assertEqual(code, 0)
                verify_payload()
                sentinel = target / 'My project.txt'
                sentinel.write_text('Keep my project and settings', encoding='utf-8')
                executable.unlink()
                code, log = install('repair', 'repair')
                self.assertEqual(code, 0, log[-3000:])
                self.assertIn('Forge action: Repair', log)
                verify_payload()
                self.assertEqual(sentinel.read_text(), 'Keep my project and settings')

                fixture_version(executable, 5, 1)
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, legacy_key, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_32KEY) as key:
                    winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, product)
                    winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, '0.5.1')
                    winreg.SetValueEx(key, 'InstallLocation', 0, winreg.REG_SZ, str(target))
                code, log = install('upgrade', 'upgrade')
                self.assertEqual(code, 0, log[-3000:])
                self.assertIn('Forge action: Upgrade', log)
                verify_payload()
                with self.assertRaises(FileNotFoundError):
                    winreg.OpenKey(winreg.HKEY_CURRENT_USER, legacy_key, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
                self.assertTrue(sentinel.exists())

                fixture_version(executable, 9, 0)
                newer = executable.read_bytes()
                code, _ = install('repair', 'downgrade')
                self.assertNotEqual(code, 0)
                self.assertEqual(executable.read_bytes(), newer)
                self.assertTrue(sentinel.exists())
                shutil.copyfile(payload / 'bin/JSON-API-Forge-Editor.exe', executable)
            finally:
                uninstall = target / 'unins000.exe'
                if uninstall.is_file():
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = 0
                    subprocess.run([str(uninstall), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                                   startupinfo=startup, timeout=120, check=True)
                try:
                    winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, legacy_key, winreg.KEY_WOW64_32KEY)
                except FileNotFoundError:
                    pass


if __name__ == '__main__':
    unittest.main()
