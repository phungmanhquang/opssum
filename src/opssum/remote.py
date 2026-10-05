"""Download GitHub skills into the shared library without running repository code."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .catalog import NAME_RE, parse_frontmatter


@dataclass(frozen=True)
class RemoteSkill:
    name: str
    path: Path
    description: str
    publisher: str = ""

    @property
    def reference(self) -> str:
        return f"{self.publisher}/{self.name}" if self.publisher else self.name


def download(repository: str, destination: Path) -> list[RemoteSkill]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*", repository):
        raise ValueError("Repository cần có dạng owner/repo, ví dụ anthropics/skills.")
    try:
        subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", "clone", "--depth", "1", "--",
             f"https://github.com/{repository}.git", str(destination)],
            check=True, capture_output=True, timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise ValueError("Cần cài Git để tải skills từ GitHub.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Tải repository quá thời gian chờ (120 giây).") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("Không tải được repository. Kiểm tra tên, quyền truy cập và kết nối GitHub.") from exc
    owner, repo = repository.split("/")
    return discover(destination, root_name=repo, publisher=owner.lower())


def discover(root: Path, *, root_name: str | None = None, publisher: str = "") -> list[RemoteSkill]:
    found = []
    names = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git" and not (Path(directory) / d).is_symlink())
        if "SKILL.md" not in files:
            continue
        path = Path(directory)
        name = root_name if path == root and root_name else path.name
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"Tên skill không hợp lệ: {name}")
        if any(p.is_symlink() for p in path.rglob("*")):
            raise ValueError(f"Skill {path.name} chứa symlink; không thể nhập an toàn.")
        if name.casefold() in names:
            raise ValueError(f"Repository có nhiều skill trùng tên: {name}")
        names.add(name.casefold())
        meta, _ = parse_frontmatter((path / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        found.append(RemoteSkill(name, path, " ".join(meta.get("description", "").split()), publisher))
    if not found:
        raise ValueError("Repository không có skill chứa SKILL.md.")
    return sorted(found, key=lambda s: s.name.casefold())


def exists(home: Path, skill: RemoteSkill | str) -> bool:
    name = skill.reference if isinstance(skill, RemoteSkill) else skill
    return os.path.lexists(home / "skills" / name)


def install(home: Path, skill: RemoteSkill, *, overwrite: bool = False) -> None:
    if not NAME_RE.fullmatch(skill.name):
        raise ValueError("Tên skill không hợp lệ.")
    if skill.publisher and not NAME_RE.fullmatch(skill.publisher):
        raise ValueError("Nhà phát hành không hợp lệ.")
    root = home / "skills"
    if skill.publisher:
        if (root / skill.publisher / "SKILL.md").is_file():
            raise ValueError(f"Đường dẫn nhóm {skill.publisher} đang là một skill cũ; cần đổi tên trước.")
        if (root / skill.publisher).is_symlink():
            raise ValueError("Thư mục nhà phát hành không được là symlink.")
        root /= skill.publisher
    root.mkdir(parents=True, exist_ok=True)
    target = root / skill.name
    if target.is_dir() and not (target / "SKILL.md").is_file():
        raise ValueError(f"{target} đang là thư mục nhóm hoặc dữ liệu khác, không thể ghi đè bằng skill.")
    if exists(home, skill) and not overwrite:
        raise FileExistsError(f"Skill {skill.name} đã tồn tại; cần xác nhận ghi đè.")
    # Stage on the same filesystem; restore the original if replacement fails.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temp:
        stage, backup = Path(temp) / "new", Path(temp) / "old"
        shutil.copytree(skill.path, stage, ignore=shutil.ignore_patterns(".git"))
        had_old = exists(home, skill)
        if had_old:
            if not overwrite:
                raise FileExistsError(f"Skill {skill.name} đã tồn tại.")
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if had_old:
                backup.rename(target)
            raise
