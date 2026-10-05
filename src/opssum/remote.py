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
        raise ValueError("Repository must use the owner/repo format, for example anthropics/skills.")
    try:
        subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", "clone", "--depth", "1", "--",
             f"https://github.com/{repository}.git", str(destination)],
            check=True, capture_output=True, timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise ValueError("Git is required to download skills from GitHub.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Repository download timed out (120 seconds).") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("Could not download the repository. Check its name, access permissions, and GitHub connection.") from exc
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
            raise ValueError(f"Invalid skill name: {name}")
        if any(p.is_symlink() for p in path.rglob("*")):
            raise ValueError(f"Skill {path.name} contains a symlink and cannot be imported safely.")
        if name.casefold() in names:
            raise ValueError(f"Repository contains multiple skills with the same name: {name}")
        names.add(name.casefold())
        meta, _ = parse_frontmatter((path / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        found.append(RemoteSkill(name, path, " ".join(meta.get("description", "").split()), publisher))
    if not found:
        raise ValueError("The repository contains no skill with SKILL.md.")
    return sorted(found, key=lambda s: s.name.casefold())


def exists(home: Path, skill: RemoteSkill | str) -> bool:
    name = skill.reference if isinstance(skill, RemoteSkill) else skill
    return os.path.lexists(home / "skills" / name)


def install(home: Path, skill: RemoteSkill, *, overwrite: bool = False) -> None:
    if not NAME_RE.fullmatch(skill.name):
        raise ValueError("Invalid skill name.")
    if skill.publisher and not NAME_RE.fullmatch(skill.publisher):
        raise ValueError("Invalid publisher name.")
    root = home / "skills"
    if skill.publisher:
        if (root / skill.publisher / "SKILL.md").is_file():
            raise ValueError(f"Publisher path {skill.publisher} is an existing legacy skill; rename it first.")
        if (root / skill.publisher).is_symlink():
            raise ValueError("The publisher directory must not be a symlink.")
        root /= skill.publisher
    root.mkdir(parents=True, exist_ok=True)
    target = root / skill.name
    if target.is_dir() and not (target / "SKILL.md").is_file():
        raise ValueError(f"{target} is a publisher directory or other data and cannot be overwritten by a skill.")
    if exists(home, skill) and not overwrite:
        raise FileExistsError(f"Skill {skill.name} already exists; overwrite confirmation is required.")
    # Stage on the same filesystem; restore the original if replacement fails.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temp:
        stage, backup = Path(temp) / "new", Path(temp) / "old"
        shutil.copytree(skill.path, stage, ignore=shutil.ignore_patterns(".git"))
        had_old = exists(home, skill)
        if had_old:
            if not overwrite:
                raise FileExistsError(f"Skill {skill.name} already exists.")
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if had_old:
                backup.rename(target)
            raise
