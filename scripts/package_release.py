"""Smoke-test and name a native PyInstaller binary for GitHub Releases."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


TARGETS = {
    "linux-x64": ("Linux", {"x86_64", "amd64"}),
    "linux-arm64": ("Linux", {"aarch64", "arm64"}),
    "macos-x64": ("Darwin", {"x86_64"}),
    "macos-arm64": ("Darwin", {"arm64"}),
    "windows-x64": ("Windows", {"AMD64", "x86_64"}),
}


def run_checked(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Smoke test thất bại: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in TARGETS:
        print("Usage: python scripts/package_release.py <linux-x64|linux-arm64|macos-x64|macos-arm64|windows-x64>", file=sys.stderr)
        return 2
    target = sys.argv[1]
    expected_os, expected_arches = TARGETS[target]
    actual_os, actual_arch = platform.system(), platform.machine()
    if actual_os != expected_os or actual_arch not in expected_arches:
        raise RuntimeError(f"Runner không đúng kiến trúc: {actual_os}/{actual_arch}; cần {target}")

    root = Path(__file__).resolve().parents[1]
    binary = root / "dist" / ("agent-knowledge.exe" if os.name == "nt" else "agent-knowledge")
    if not binary.is_file():
        raise FileNotFoundError(f"PyInstaller chưa tạo binary: {binary}")
    asset_name = f"agent-knowledge-{target}" + (".exe" if os.name == "nt" else "")
    assets = root / "dist" / "release-assets"
    assets.mkdir(parents=True, exist_ok=True)
    asset = assets / asset_name
    shutil.copy2(binary, asset)
    if os.name != "nt":
        asset.chmod(asset.stat().st_mode | 0o111)

    version = run_checked(str(asset), "--version")
    expected_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if version != f"agent-knowledge {expected_version}":
        raise RuntimeError(f"Binary trả về version {version!r}; cần agent-knowledge {expected_version}")
    with tempfile.TemporaryDirectory(prefix="ak-release-smoke-") as temp:
        home = Path(temp) / "library"
        run_checked(str(asset), "init", "--home", str(home), "--examples")
        data = json.loads(run_checked(str(asset), "list", "--home", str(home), "--json"))
        if not data.get("skill") or not data.get("mcp"):
            raise RuntimeError("Binary không đọc được library mẫu.")

    with asset.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = assets / f"{asset_name}.sha256"
    checksum.write_text(f"{digest}  {asset_name}\n", encoding="ascii")
    print(f"OK: {asset_name} ({version})")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
