"""Windows launch guards. These tests never open a public tunnel."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Native Windows launcher")


@pytest.mark.parametrize("origin", [
    "http://jury.example", "https://user:synthetic@jury.example", "https://jury.example/path",
    "https://jury.example?token=synthetic",
])
def test_startup_rejects_unsafe_public_origin_before_starting_server(origin):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(ROOT / "start.ps1"), "-PublicOrigin", origin], cwd=ROOT,
                            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode != 0
    assert b"PublicOrigin must be an HTTPS origin" in result.stdout + result.stderr


def test_public_launcher_default_cannot_start_processes():
    if not (ROOT / ".tools/cloudflared.exe").is_file() or not shutil.which("powershell.exe"):
        pytest.skip("Verified local tunnel binary is not installed; no download in tests")
    script = str(ROOT / "deploy/start-public.ps1").replace("'", "''")
    code = "$ErrorActionPreference='Stop'; function Start-Process { throw 'Unexpected launch' }; & '" + script + "'"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", code],
                            cwd=ROOT, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")
    assert b"Prepared only: no server or tunnel started" in result.stdout
