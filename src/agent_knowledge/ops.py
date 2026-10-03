"""Logic cài đặt / gỡ / đọc trạng thái cho từng loại item và từng agent.

Nguyên tắc an toàn:
  * Skill   : symlink (fallback copy + file marker). Chỉ gỡ thứ do mình tạo.
  * Instr.  : chèn block có marker `<!-- agent-knowledge:begin NAME -->` vào file
              AGENTS.md / CLAUDE.md / GEMINI.md, không đụng phần còn lại.
  * MCP json: sửa key `mcpServers.<name>`, giữ nguyên mọi key khác, backup trước khi ghi.
  * MCP toml: (codex) chèn block có marker comment, không parse/ghi lại cả file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .agents import AgentSpec, load_agents
from .catalog import Item, Library, normalize_mcp

MARKER = ".agent-knowledge"          # file đánh dấu skill được cài bằng copy
BACKUP_SUFFIX = ".agent-knowledge.bak"


class OpsError(Exception):
    pass


@dataclass
class State:
    status: str                      # installed | outdated | absent | external
    managed: bool = True             # do agent-knowledge tạo/quản lý?
    removable: bool = True           # có cho phép gỡ không (external không chắc chắn -> confirm)
    detail: str = ""


@dataclass
class Result:
    ok: bool
    msg: str


# ----------------------------------------------------------------- file utils
def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def atomic_write(path: Path, text: str, backup: bool = True) -> None:
    path = _real(path)                       # giữ nguyên nếu file config là symlink (dotfiles)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    tmp = path.with_name(path.name + ".tmp-ak")
    tmp.write_text(text, encoding="utf-8")
    if path.exists():
        shutil.copymode(path, tmp)           # giữ quyền (vd ~/.claude.json = 600)
    os.replace(tmp, path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _inside(path: str | os.PathLike, root: Path) -> bool:
    try:
        _real(Path(path)).relative_to(_real(root))
        return True
    except ValueError:
        return False


# ============================================================ SKILLS
def _skill_managed(p: Path, lib_skills: Path) -> bool:
    if p.is_symlink():
        link = os.readlink(p)
        target = link if os.path.isabs(link) else os.path.join(p.parent, link)
        return _inside(os.path.normpath(target), lib_skills)
    return (p / MARKER).is_file()


def skill_states(spec: AgentSpec, scope: str, project: Path, lib: Library) -> dict[str, State]:
    d = spec.resolve("skills", scope, project)
    names = {i.name for i in lib.items("skill")}
    present: dict[str, Path] = {}
    if d is not None and d.is_dir():
        for c in sorted(d.iterdir(), key=lambda p: p.name.lower()):
            if c.name.startswith("."):
                continue
            if c.is_symlink() or (c.is_dir() and (c / "SKILL.md").is_file()):
                present[c.name] = c
    out: dict[str, State] = {}
    for name, c in present.items():
        managed = _skill_managed(c, lib.dir_for("skill"))
        broken = c.is_symlink() and not c.exists()
        if name in names:
            if managed:
                out[name] = State("outdated", True, True, "symlink bị hỏng") if broken else State("installed")
            else:
                out[name] = State("external", False, False,
                                  "đã có bản không do agent-knowledge quản lý (cùng tên)")
        else:
            out[name] = State("external", managed, managed,
                              "orphan: không còn trong library" if managed else "skill tự cài, chưa có trong library")
    for n in names - present.keys():
        out[n] = State("absent")
    return out


def _unlink_dir(p: Path) -> None:
    try:
        p.unlink()
    except (IsADirectoryError, PermissionError, OSError):
        os.rmdir(p)                          # Windows: directory symlink/junction


def skill_install(spec, scope, project, lib, item: Item, mode: str = "symlink") -> Result:
    d = spec.resolve("skills", scope, project)
    if d is None:
        return Result(False, f"{spec.label} không hỗ trợ skills ở scope {scope}")
    st = skill_states(spec, scope, project, lib).get(item.name)
    target = d / item.name
    if st and st.status == "installed":
        return Result(True, f"{item.name} đã được cài cho {spec.label}")
    if st and st.status == "external" and not st.managed:
        return Result(False, f"{target} đã tồn tại và không do agent-knowledge quản lý")
    if os.path.lexists(target):
        skill_uninstall(spec, scope, project, lib, item.name)
    d.mkdir(parents=True, exist_ok=True)
    if mode == "symlink":
        try:
            os.symlink(item.path, target, target_is_directory=True)
            return Result(True, f"Đã cài skill {item.name} → {target} (symlink)")
        except OSError:
            pass                              # Windows không có quyền symlink -> copy
    shutil.copytree(item.path, target, symlinks=True)
    (target / MARKER).write_text(str(item.path), encoding="utf-8")
    return Result(True, f"Đã cài skill {item.name} → {target} (copy)")


def skill_uninstall(spec, scope, project, lib, name: str) -> Result:
    d = spec.resolve("skills", scope, project)
    if d is None:
        return Result(False, "không hỗ trợ")
    target = d / name
    if not os.path.lexists(target):
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    if not _skill_managed(target, lib.dir_for("skill")):
        return Result(False, f"{target} không do agent-knowledge tạo — hãy xoá tay nếu chắc chắn")
    if target.is_symlink():
        _unlink_dir(target)
    else:
        shutil.rmtree(target)
    return Result(True, f"Đã gỡ skill {name} khỏi {spec.label}")


def skill_adopt(spec, scope, project, lib, name: str) -> Result:
    src = spec.resolve("skills", scope, project) / name
    dst = lib.dir_for("skill") / name
    if dst.exists():
        return Result(False, f"library đã có skills/{name}")
    if not (src / "SKILL.md").is_file():
        return Result(False, f"{src} không phải skill hợp lệ")
    shutil.copytree(src, dst, symlinks=False)
    return Result(True, f"Đã nhập skill {name} vào library (bản gốc ở agent giữ nguyên)")


# ============================================================ INSTRUCTIONS
_IBLOCK = re.compile(
    r"<!-- agent-knowledge:begin (?P<name>\S+) -->\n(?P<body>.*?)\n<!-- agent-knowledge:end (?P=name) -->\n?",
    re.S,
)


def _iblock(name: str, body: str) -> str:
    return f"<!-- agent-knowledge:begin {name} -->\n{body.strip()}\n<!-- agent-knowledge:end {name} -->\n"


def instr_states(spec, scope, project, lib) -> dict[str, State]:
    f = spec.resolve("instr", scope, project)
    out: dict[str, State] = {}
    found: dict[str, str] = {}
    if f is not None:
        for m in _IBLOCK.finditer(_read_text(f)):
            found[m.group("name")] = m.group("body")
    names = {i.name: i for i in lib.items("instruction")}
    for n, it in names.items():
        if n in found:
            same = found[n].strip() == it.body.strip()
            out[n] = State("installed") if same else State("outdated", True, True, "nội dung khác library")
        else:
            out[n] = State("absent")
    for n in found.keys() - names.keys():
        out[n] = State("external", True, True, "orphan: không còn trong library")
    return out


def instr_install(spec, scope, project, lib, item: Item) -> Result:
    f = spec.resolve("instr", scope, project)
    if f is None:
        return Result(False, f"{spec.label} không hỗ trợ instructions ở scope {scope}")
    text = _read_text(f)
    block = _iblock(item.name, item.body)
    if _IBLOCK.search(text) and any(m.group("name") == item.name for m in _IBLOCK.finditer(text)):
        text = _IBLOCK.sub(lambda m: block if m.group("name") == item.name else m.group(0), text)
        verb = "Đã cập nhật"
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        if text.strip():
            text += "\n"
        text += block
        verb = "Đã cài"
    atomic_write(f, text)
    return Result(True, f"{verb} instruction {item.name} → {f}")


def instr_uninstall(spec, scope, project, lib, name: str) -> Result:
    f = spec.resolve("instr", scope, project)
    if f is None or not f.exists():
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    text = _read_text(f)
    new = _IBLOCK.sub(lambda m: "" if m.group("name") == name else m.group(0), text)
    if new == text:
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    new = re.sub(r"\n{3,}", "\n\n", new).lstrip("\n")
    atomic_write(f, new)
    return Result(True, f"Đã gỡ instruction {name} khỏi {f}")


# ============================================================ MCP
def render_json(style: str, spec: dict) -> dict:
    e: dict = {}
    if "command" in spec:
        if style == "claude":
            e["type"] = "stdio"
        e["command"] = spec["command"]
        if spec.get("args"):
            e["args"] = list(spec["args"])
        if spec.get("env"):
            e["env"] = dict(spec["env"])
        return e
    if style == "agy":
        e["serverUrl"] = spec["url"]
    else:
        if style == "claude":
            e["type"] = "sse" if spec.get("transport") == "sse" else "http"
        elif spec.get("transport") == "sse":
            e["type"] = "sse"
        e["url"] = spec["url"]
    if spec.get("headers"):
        e["headers"] = dict(spec["headers"])
    return e


def _toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def render_toml(name: str, spec: dict) -> str:
    k = _toml_key(name)
    lines = [f"[mcp_servers.{k}]"]
    if "command" in spec:
        lines.append(f"command = {json.dumps(spec['command'], ensure_ascii=False)}")
        if spec.get("args"):
            lines.append("args = " + json.dumps(spec["args"], ensure_ascii=False))
        if spec.get("env"):
            lines.append(f"[mcp_servers.{k}.env]")
            lines += [f"{_toml_key(a)} = {json.dumps(b, ensure_ascii=False)}" for a, b in spec["env"].items()]
    else:
        if spec.get("transport") == "sse":
            raise OpsError("Codex chỉ hỗ trợ stdio và streamable HTTP, không hỗ trợ SSE")
        lines.append(f"url = {json.dumps(spec['url'], ensure_ascii=False)}")
        if spec.get("headers"):
            lines.append(f"[mcp_servers.{k}.http_headers]")
            lines += [f"{_toml_key(a)} = {json.dumps(b, ensure_ascii=False)}" for a, b in spec["headers"].items()]
    return "\n".join(lines) + "\n"


_TBLOCK = re.compile(
    r"# >>> agent-knowledge:(?P<name>\S+) >>>\n(?P<body>.*?)# <<< agent-knowledge:(?P=name) <<<\n?", re.S
)
_TTABLE = re.compile(r'^\[mcp_servers\.("[^"]+"|[A-Za-z0-9_-]+)\][ \t]*$', re.M)


def _tblock(name: str, body: str) -> str:
    return f"# >>> agent-knowledge:{name} >>>\n{body}# <<< agent-knowledge:{name} <<<\n"


def _json_load(path: Path) -> dict:
    text = _read_text(path).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise OpsError(f"{path}: JSON không hợp lệ ({e}) — không ghi đè để tránh mất dữ liệu")
    if not isinstance(data, dict):
        raise OpsError(f"{path}: root phải là object JSON")
    return data


def _json_servers(data: dict, path: Path) -> dict:
    s = data.get("mcpServers")
    if s is None:
        return {}
    if not isinstance(s, dict):
        raise OpsError(f"{path}: `mcpServers` phải là object")
    return s


def mcp_states(spec: AgentSpec, scope, project, lib) -> dict[str, State]:
    f = spec.resolve("mcp", scope, project)
    items = {i.name: i for i in lib.items("mcp")}
    out: dict[str, State] = {}
    if f is None:
        return {n: State("absent") for n in items}
    if spec.mcp_format == "toml":
        text = _read_text(f)
        managed = {m.group("name"): m.group("body") for m in _TBLOCK.finditer(text)}
        rest = _TBLOCK.sub("", text)
        ext = {m.group(1).strip('"') for m in _TTABLE.finditer(rest)}
        for n, it in items.items():
            if n in managed:
                try:
                    same = managed[n] == render_toml(n, it.spec)
                except OpsError:
                    same = False
                out[n] = State("installed") if same else State("outdated", True, True, "khác library")
            elif n in ext:
                out[n] = State("external", False, False, "đã có server cùng tên trong config.toml (tự khai báo)")
            else:
                out[n] = State("absent")
        for n in managed.keys() - items.keys():
            out[n] = State("external", True, True, "orphan: không còn trong library")
        for n in ext - items.keys():
            out[n] = State("external", False, False, "khai báo tay trong config.toml (sửa bằng tay)")
        return out
    servers = _json_servers(_json_load(f), f)
    for n, it in items.items():
        if n in servers:
            if servers[n] == render_json(spec.mcp_style, it.spec):
                out[n] = State("installed")
            else:
                out[n] = State("outdated", False, True, "khác library (có thể bạn đã sửa tay)")
        else:
            out[n] = State("absent")
    for n in servers.keys() - items.keys():
        out[n] = State("external", False, True, "server tự khai báo, chưa có trong library")
    return out


def mcp_install(spec, scope, project, lib, item: Item) -> Result:
    f = spec.resolve("mcp", scope, project)
    if f is None:
        return Result(False, f"{spec.label} không hỗ trợ MCP ở scope {scope}")
    if spec.mcp_format == "toml":
        text = _read_text(f)
        body = render_toml(item.name, item.spec)
        names = {m.group("name") for m in _TBLOCK.finditer(text)}
        if item.name in names:
            text = _TBLOCK.sub(lambda m: _tblock(item.name, body) if m.group("name") == item.name else m.group(0), text)
        else:
            rest = _TBLOCK.sub("", text)
            if any(m.group(1).strip('"') == item.name for m in _TTABLE.finditer(rest)):
                return Result(False, f"{f} đã có [mcp_servers.{item.name}] tự khai báo — không ghi đè")
            if text and not text.endswith("\n"):
                text += "\n"
            if text.strip():
                text += "\n"
            text += _tblock(item.name, body)
        atomic_write(f, text)
        return Result(True, f"Đã cài MCP {item.name} → {f}")
    data = _json_load(f)
    servers = _json_servers(data, f)
    servers[item.name] = render_json(spec.mcp_style, item.spec)
    data["mcpServers"] = servers
    atomic_write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    extra = " (cần extension pi-mcp-adapter)" if spec.id == "pi" else ""
    return Result(True, f"Đã cài MCP {item.name} → {f}{extra}")


def mcp_uninstall(spec, scope, project, lib, name: str) -> Result:
    f = spec.resolve("mcp", scope, project)
    if f is None or not f.exists():
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    if spec.mcp_format == "toml":
        text = _read_text(f)
        new = _TBLOCK.sub(lambda m: "" if m.group("name") == name else m.group(0), text)
        if new == text:
            return Result(False, f"{name} khai báo tay trong {f} — hãy sửa bằng tay")
        atomic_write(f, re.sub(r"\n{3,}", "\n\n", new).lstrip("\n"))
        return Result(True, f"Đã gỡ MCP {name} khỏi {f}")
    data = _json_load(f)
    servers = _json_servers(data, f)
    if name not in servers:
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    del servers[name]
    data["mcpServers"] = servers
    atomic_write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return Result(True, f"Đã gỡ MCP {name} khỏi {f}")


def mcp_adopt(spec, scope, project, lib, name: str) -> Result:
    f = spec.resolve("mcp", scope, project)
    dst = lib.dir_for("mcp") / f"{name}.json"
    if dst.exists():
        return Result(False, f"library đã có mcp/{name}.json")
    if spec.mcp_format == "toml":
        try:
            import tomllib                    # Python >= 3.11
        except ImportError:
            return Result(False, "Cần Python 3.11+ để nhập MCP từ config.toml")
        raw = tomllib.loads(_read_text(f)).get("mcp_servers", {}).get(name)
        if raw is None:
            return Result(False, f"không thấy mcp_servers.{name}")
        raw = dict(raw)
        if "http_headers" in raw:
            raw["headers"] = raw.pop("http_headers")
    else:
        raw = _json_servers(_json_load(f), f).get(name)
        if raw is None:
            return Result(False, f"không thấy mcpServers.{name}")
    try:
        norm, _ = normalize_mcp(raw)
    except ValueError as e:
        return Result(False, f"không nhập được: {e}")
    out = {"description": "", **{k: v for k, v in norm.items() if v not in ({}, [])}}
    if out.get("transport") == "http":
        out.pop("transport")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return Result(True, f"Đã nhập MCP {name} vào library (mcp/{name}.json) — nhớ kiểm tra secrets/env")


# ============================================================ MANAGER
class Manager:
    """Điểm vào duy nhất cho TUI và CLI."""

    def __init__(self, home: Path, project: Path | None = None, skill_mode: str = "symlink"):
        self.home = Path(home)
        self.project = Path(project or Path.cwd())
        self.skill_mode = skill_mode
        self.agents = load_agents(self.home)
        self.lib = Library(self.home)
        self.errors: dict[tuple[str, str, str], str] = {}

    def reload(self) -> None:
        self.agents = load_agents(self.home)
        self.lib.load()
        self.errors.clear()

    def agent_list(self) -> list[AgentSpec]:
        return list(self.agents.values())

    # ---- state
    def states(self, agent_id: str, scope: str, kind: str) -> dict[str, State]:
        spec = self.agents[agent_id]
        self.errors.pop((agent_id, scope, kind), None)
        try:
            if kind == "skill":
                return skill_states(spec, scope, self.project, self.lib)
            if kind == "instruction":
                return instr_states(spec, scope, self.project, self.lib)
            return mcp_states(spec, scope, self.project, self.lib)
        except OpsError as e:
            self.errors[(agent_id, scope, kind)] = str(e)
        except OSError as e:
            self.errors[(agent_id, scope, kind)] = f"Lỗi I/O: {e}"
        return {i.name: State("absent") for i in self.lib.items(kind)}

    def target_path(self, agent_id: str, scope: str, kind: str, name: str) -> Path | None:
        spec = self.agents[agent_id]
        if kind == "skill":
            d = spec.resolve("skills", scope, self.project)
            return d / name if d else None
        return spec.resolve("instr" if kind == "instruction" else "mcp", scope, self.project)

    def shared_with(self, agent_id: str, scope: str, kind: str) -> list[str]:
        what = {"skill": "skills", "instruction": "instr", "mcp": "mcp"}[kind]
        mine = self.agents[agent_id].resolve(what, scope, self.project)
        if mine is None:
            return []
        return [a.id for a in self.agents.values()
                if a.id != agent_id and a.resolve(what, scope, self.project) == mine]

    # ---- actions
    def install(self, agent_id: str, scope: str, kind: str, name: str) -> Result:
        item = self.lib.get(kind, name)
        if item is None:
            return Result(False, f"library không có {kind}:{name}")
        spec = self.agents[agent_id]
        try:
            if kind == "skill":
                return skill_install(spec, scope, self.project, self.lib, item, self.skill_mode)
            if kind == "instruction":
                return instr_install(spec, scope, self.project, self.lib, item)
            return mcp_install(spec, scope, self.project, self.lib, item)
        except OpsError as e:
            return Result(False, str(e))
        except OSError as e:
            return Result(False, f"Lỗi I/O: {e}")

    def uninstall(self, agent_id: str, scope: str, kind: str, name: str) -> Result:
        spec = self.agents[agent_id]
        try:
            if kind == "skill":
                return skill_uninstall(spec, scope, self.project, self.lib, name)
            if kind == "instruction":
                return instr_uninstall(spec, scope, self.project, self.lib, name)
            return mcp_uninstall(spec, scope, self.project, self.lib, name)
        except OpsError as e:
            return Result(False, str(e))
        except OSError as e:
            return Result(False, f"Lỗi I/O: {e}")

    def adopt(self, agent_id: str, scope: str, kind: str, name: str) -> Result:
        spec = self.agents[agent_id]
        try:
            if kind == "skill":
                r = skill_adopt(spec, scope, self.project, self.lib, name)
            elif kind == "mcp":
                r = mcp_adopt(spec, scope, self.project, self.lib, name)
            else:
                return Result(False, "Instructions chỉ nhập thủ công (copy nội dung vào instructions/<name>.md)")
        except OpsError as e:
            return Result(False, str(e))
        except OSError as e:
            return Result(False, f"Lỗi I/O: {e}")
        if r.ok:
            self.lib.load()
        return r
