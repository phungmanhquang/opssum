"""Track skill locations and remove library skills with recoverable backups."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

REGISTRY = ".skill-locations.json"


def _locations(home: Path) -> set[Path]:
    path = home / REGISTRY
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in raw):
        raise ValueError(f"Danh sách đường dẫn skill không hợp lệ: {path}")
    return {Path(p) for p in raw}


def remember_locations(home: Path, locations: list[Path]) -> None:
    existing = _locations(home)
    updated = existing | {p.resolve() for p in locations}
    if existing == updated:
        return
    home.mkdir(parents=True, exist_ok=True)
    path = home / REGISTRY
    if path.is_symlink():
        raise ValueError(f"Không ghi registry là symlink: {path}")
    with tempfile.TemporaryDirectory(prefix=".locations-", dir=home) as temp:
        stage = Path(temp) / "registry.json"
        stage.write_text(json.dumps(sorted(map(str, updated)), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(stage, path)


def _points_to(path: Path, source: Path) -> bool:
    if path.is_symlink():
        return path.resolve() == source.resolve()
    marker = path / ".opssum"
    return marker.is_file() and Path(marker.read_text(encoding="utf-8").strip()).resolve() == source.resolve()


def linked_paths(home: Path, source: Path, current: list[Path]) -> list[Path]:
    locations = _locations(home) | {p.resolve() for p in current}
    found: set[Path] = set()
    for directory in locations:
        if directory.is_dir():
            for path in directory.iterdir():
                if not path.name.startswith(".") and path != source and _points_to(path, source):
                    found.add(path)
    return sorted(found)


def remove_from_library(home: Path, source: Path, links: list[Path]) -> Path:
    root = (home / "skills").resolve()
    source = source.absolute()
    relative = source.relative_to(root)
    if len(relative.parts) not in (1, 2) or source.is_symlink() or not (source / "SKILL.md").is_file():
        raise ValueError("Chỉ xoá thư mục skill hợp lệ trong library.")
    # Resolve parent aliases too: publisher folders must not redirect outside library.
    source.resolve().relative_to(root)
    for path in links:
        if not _points_to(path, source):
            raise ValueError(f"Liên kết đã thay đổi, vui lòng tải lại: {path}")
    trash_root = home / ".trash"
    if trash_root.is_symlink():
        raise ValueError("Thư mục khôi phục không được là symlink.")
    trash = trash_root / uuid4().hex
    trash.mkdir(parents=True)
    (trash / "manifest.json").write_text(json.dumps({
        "source": str(source), "links": [str(p) for p in links],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    moved: list[tuple[Path, Path]] = []
    try:
        for index, path in enumerate(links):
            backup = trash / f"agent-{index}"
            shutil.move(str(path), str(backup))
            moved.append((path, backup))
        source.rename(trash / "skill")
    except OSError:
        for path, backup in reversed(moved):
            shutil.move(str(backup), str(path))
        raise
    return trash
