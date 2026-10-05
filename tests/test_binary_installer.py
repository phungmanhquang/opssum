"""Exercise the POSIX installer without touching a real GitHub release or HOME."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def _mock_tools(tmp_path: Path) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text("""#!/bin/sh
out=
url=
previous=
for part do
  if [ "$previous" = -o ]; then out=$part; fi
  case "$part" in https://*) url=$part ;; esac
  previous=$part
done
cp "$OPSSUM_TEST_ASSETS/${url##*/}" "$out"
""")
    curl.chmod(0o755)
    uname = tools / "uname"
    uname.write_text("""#!/bin/sh
case "$1" in
  -s) printf '%s\\n' "$OPSSUM_TEST_OS" ;;
  -m) printf '%s\\n' "$OPSSUM_TEST_ARCH" ;;
esac
""")
    uname.chmod(0o755)
    return tools


def _asset(directory: Path, name: str, version: str, *, bad_checksum: bool = False) -> bytes:
    binary = f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'opssum {version}'; fi\n".encode()
    (directory / name).write_bytes(binary)
    digest = hashlib.sha256(binary).hexdigest()
    if bad_checksum:
        digest = "0" * 64
    (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    return binary


@pytest.mark.parametrize("system,architecture,asset", [
    ("Linux", "x86_64", "opssum-linux-x64"),
    ("Linux", "aarch64", "opssum-linux-arm64"),
    ("Darwin", "x86_64", "opssum-macos-x64"),
    ("Darwin", "arm64", "opssum-macos-arm64"),
])
def test_install_update_and_checksum_failure(tmp_path, system, architecture, asset):
    assets = tmp_path / "assets"
    assets.mkdir()
    mockbin = _mock_tools(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "OPSSUM_REPO": "owner/repo",
           "OPSSUM_TEST_ASSETS": str(assets), "OPSSUM_TEST_OS": system, "OPSSUM_TEST_ARCH": architecture,
           "PATH": str(mockbin) + os.pathsep + os.environ["PATH"]}

    def install():
        return subprocess.run(["sh", str(INSTALLER)], env=env, capture_output=True, text=True)

    first = _asset(assets, asset, "1.0.0")
    result = install()
    assert result.returncode == 0, result.stderr
    destination = home / ".local/bin/opssum"
    assert destination.read_bytes() == first
    assert "PATH" in result.stdout

    assert "đã là bản mới nhất" in install().stdout
    second = _asset(assets, asset, "1.0.1")
    assert install().returncode == 0
    assert destination.read_bytes() == second

    _asset(assets, asset, "1.0.2", bad_checksum=True)
    failed = install()
    assert failed.returncode != 0
    assert "SHA256 không khớp" in failed.stderr
    assert destination.read_bytes() == second


def test_placeholder_fails_before_download(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    env["OPSSUM_REPO"] = "OWNER/REPO"
    result = subprocess.run(["sh", str(INSTALLER)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "GitHub owner/repo chưa được cấu hình hợp lệ" in result.stderr
