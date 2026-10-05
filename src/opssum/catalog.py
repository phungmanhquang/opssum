"""Read the `.opssum` library directory.

Layout:

    .opssum/
    ├── skills/[<publisher>/]<name>/SKILL.md
    ├── mcp/[<publisher>/]<name>.json # one server per file (or {"mcpServers": {...}})
    ├── instructions/<name>.md        # AGENTS.md / CLAUDE.md fragment
    └── agents.json                   # optional per-agent path overrides
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("skill", "mcp", "instruction")
KIND_LABEL = {"skill": "Skills", "mcp": "MCP", "instruction": "Instructions"}
KIND_DIR = {"skill": "skills", "mcp": "mcp", "instruction": "instructions"}
KIND_ALIASES = {
    "skill": "skill", "skills": "skill",
    "mcp": "mcp", "mcps": "mcp",
    "instruction": "instruction", "instructions": "instruction",
    "rule": "instruction", "rules": "instruction", "agent-file": "instruction",
}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_HOME_NAME = ".opssum"


def default_home(override: str | os.PathLike | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get("OPSSUM_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / DEFAULT_HOME_NAME


@dataclass
class Item:
    kind: str
    name: str
    description: str = ""
    path: Path | None = None          # skill dir / instruction file / mcp file
    spec: dict | None = None          # mcp: normalized spec
    body: str = ""                    # instruction body (front matter removed)
    publisher: str = ""

    @property
    def skill_name(self) -> str:
        return self.name.rsplit("/", 1)[-1]

    @property
    def server_name(self) -> str:
        return self.name.rsplit("/", 1)[-1]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        if km:
            key, val = km.group(1), km.group(2).strip()
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                buf: list[str] = []
                i += 1
                while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                    buf.append(lines[i].strip())
                    i += 1
                meta[key] = " ".join(x for x in buf if x)
                continue
            meta[key] = val.strip("\"'")
        i += 1
    return meta, text[m.end():]


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def normalize_mcp(raw: dict) -> tuple[dict, str]:
    """Normalize one MCP server definition into the internal representation.

    stdio  -> {"command", "args", "env"}
    remote -> {"url", "transport": "http"|"sse", "headers"}
    """
    if not isinstance(raw, dict):
        raise ValueError("MCP definition must be a JSON object")
    desc = _squash(str(raw.get("description", "")))
    url = raw.get("url") or raw.get("serverUrl") or raw.get("httpUrl")
    if url:
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError("MCP URL must start with https:// or http://")
        t = str(raw.get("transport") or raw.get("type") or "http").lower()
        spec: dict = {"url": str(url), "transport": "sse" if t == "sse" else "http"}
        if raw.get("headers"):
            if not isinstance(raw["headers"], dict):
                raise ValueError("MCP headers must be a JSON object")
            spec["headers"] = {str(k): str(v) for k, v in dict(raw["headers"]).items()}
    elif raw.get("command"):
        if not isinstance(raw["command"], str) or not isinstance(raw.get("args", []), list):
            raise ValueError("MCP command must be a string and args must be a list")
        spec = {"command": str(raw["command"]), "args": [str(a) for a in raw.get("args", [])]}
        if raw.get("env"):
            if not isinstance(raw["env"], dict):
                raise ValueError("MCP env must be a JSON object")
            spec["env"] = {str(k): str(v) for k, v in dict(raw["env"]).items()}
    else:
        raise ValueError("either `command` (stdio) or `url` (remote) is required")
    return spec, desc


class Library:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.errors: list[str] = []
        self._items: dict[str, list[Item]] = {k: [] for k in KINDS}
        self.load()

    # ------------------------------------------------------------------ API
    def exists(self) -> bool:
        return self.home.is_dir()

    def items(self, kind: str) -> list[Item]:
        return self._items[kind]

    def get(self, kind: str, name: str) -> Item | None:
        for it in self._items[kind]:
            if it.name == name:
                return it
        return None

    def dir_for(self, kind: str) -> Path:
        return self.home / KIND_DIR[kind]

    def load(self) -> None:
        self.errors = []
        self._items = {
            "skill": self._scan_skills(),
            "mcp": self._scan_mcp(),
            "instruction": self._scan_instructions(),
        }

    # ------------------------------------------------------------- scanners
    def _scan_skills(self) -> list[Item]:
        root = self.dir_for("skill")
        out: list[Item] = []
        if not root.is_dir():
            return out
        def scan(directory: Path, publisher: str = "") -> None:
            for d in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                if d.name.startswith(".") or not d.is_dir():
                    continue
                if not NAME_RE.fullmatch(d.name):
                    self.errors.append(f"{d}: invalid directory name")
                    continue
                md = d / "SKILL.md"
                if md.is_file():
                    meta, _ = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
                    name = f"{publisher}/{d.name}" if publisher else d.name
                    out.append(Item("skill", name, _squash(meta.get("description", "")), d, publisher=publisher))
                elif not publisher and not d.is_symlink():
                    scan(d, d.name)
        scan(root)
        return out

    def _scan_instructions(self) -> list[Item]:
        root = self.dir_for("instruction")
        out: list[Item] = []
        if not root.is_dir():
            return out
        for f in sorted(root.glob("*.md"), key=lambda p: p.name.lower()):
            if f.name.startswith("."):
                continue
            name = f.stem
            if not NAME_RE.match(name):
                self.errors.append(f"instructions/{f.name}: invalid file name")
                continue
            meta, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            body = body.strip()
            desc = _squash(meta.get("description", ""))
            if not desc:
                for line in body.splitlines():
                    line = line.strip().lstrip("#").strip()
                    if line:
                        desc = line
                        break
            out.append(Item("instruction", name, desc, f, body=body))
        return out

    def _scan_mcp(self) -> list[Item]:
        root = self.dir_for("mcp")
        out: list[Item] = []
        if not root.is_dir():
            return out
        files = sorted((*root.glob("*.json"), *root.glob("*/*.json")), key=lambda p: str(p).lower())
        for f in files:
            if f.name.startswith("."):
                continue
            publisher = f.parent.name if f.parent != root else ""
            if publisher and not NAME_RE.fullmatch(publisher):
                self.errors.append(f"mcp/{publisher}: invalid publisher name")
                continue
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                self.errors.append(f"mcp/{f.name}: {e}")
                continue
            entries: dict[str, dict]
            if isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict):
                entries = raw["mcpServers"]          # common copy-paste format from an MCP server README
            else:
                entries = {f.stem: raw}
            for name, body in entries.items():
                if not isinstance(name, str) or not NAME_RE.fullmatch(name):
                    self.errors.append(f"mcp/{f.name}: invalid server name `{name}`")
                    continue
                try:
                    spec, desc = normalize_mcp(body)
                except ValueError as e:
                    self.errors.append(f"mcp/{f.name} ({name}): {e}")
                    continue
                reference = f"{publisher}/{name}" if publisher else name
                out.append(Item("mcp", reference, desc, f, spec=spec, publisher=publisher))
        out.sort(key=lambda i: i.name.lower())
        return out
