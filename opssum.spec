# PyInstaller spec for a native, console-based, one-file executable.
# Run on each target OS/architecture; PyInstaller does not cross-compile.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


repo = Path(SPEC).resolve().parent
datas = (
    collect_data_files("opssum")
    + collect_data_files("textual")
    + copy_metadata("opssum")
)

a = Analysis(
    [str(repo / "scripts" / "standalone_entry.py")],
    pathex=[str(repo / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=["opssum.tui"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="opssum",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
