"""Logic cài đặt / gỡ / đọc trạng thái cho từng loại item và từng agent.

Nguyên tắc an toàn:
  * Skill   : symlink tới library. Chỉ gỡ thứ do mình tạo.
  * Instr.  : chèn block có marker `<!-- opssum:begin NAME -->` vào file
              AGENTS.md / CLAUDE.md / GEMINI.md, không đụng phần còn lại.
  * MCP json: sửa key `mcpServers.<name>`, giữ nguyên mọi key khác, backup trước khi ghi.
  * MCP toml: (codex) chèn block có marker comment, không parse/ghi lại cả file.
"""
from __future__ import annotations

import json
import filecmp
import os
import re
import shutil
import tempfile
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path

from .agents import AgentSpec, load_agents
from .catalog import Item, Library, NAME_RE, normalize_mcp
from .skill_storage import remember_locations, linked_paths, remove_from_library
from .mcp_storage import bindings as mcp_bindings, record as record_mcp, forget as forget_mcp, _write as write_mcp_bindings

MARKER = ".opssum"          # file đánh dấu skill được cài bằng copy
BACKUP_SUFFIX = ".opssum.bak"


class OpsError(Exception):
    pass


@dataclass
class State:
    status: str                      # installed | outdated | absent | external
    managed: bool = True             # do opssum tạo/quản lý?
    removable: bool = True           # có cho phép gỡ không (external không chắc chắn -> confirm)
    detail: str = ""


@dataclass
class Result:
    ok: bool
    msg: str


class SkillConflict(OpsError):
    """The caller must obtain overwrite confirmation before retrying."""


class McpConflict(OpsError):
    """Existing library MCP differs from the external source."""


@dataclass(frozen=True)
class ExternalSkill:
    name: str
    agent_id: str
    scope: str
    path: Path
    agents: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalMcp:
    name: str
    agent_id: str
    scope: str
    path: Path
    agents: tuple[str, ...] = ()


# ----------------------------------------------------------------- file utils
def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def atomic_write(path: Path, text: str, backup: bool = True) -> None:
    path = _real(path)                       # giữ nguyên nếu file config là symlink (dotfiles)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    tmp = path.with_name(path.name + ".tmp-opssum")
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
    items = lib.items("skill")
    present: dict[str, Path] = {}
    if d is not None and d.is_dir():
        for c in sorted(d.iterdir(), key=lambda p: p.name.lower()):
            if c.name.startswith("."):
                continue
            if c.is_symlink() or (c.is_dir() and (c / "SKILL.md").is_file()):
                present[c.name] = c
    out: dict[str, State] = {}
    for item in items:
        c = present.get(item.skill_name)
        if c is None:
            out[item.name] = State("absent")
            continue
        managed = _skill_managed(c, lib.dir_for("skill"))
        if c.is_symlink() and _real(c) == _real(item.path):
            out[item.name] = State("installed" if c.exists() else "outdated")
        elif managed and not c.is_symlink() and _read_text(c / MARKER).strip() == str(item.path):
            out[item.name] = State("outdated", detail="bản copy cũ; nhấn u để chuyển sang symlink")
        elif managed:
            out[item.name] = State("absent", detail="agent đang dùng bản cùng tên từ nguồn khác")
        else:
            out[item.name] = State("external", False, False, "đã có skill ngoài library cùng tên")
    for name, c in present.items():
        managed = _skill_managed(c, lib.dir_for("skill"))
        if name not in {i.skill_name for i in items} or (not managed and name not in {i.name for i in items}):
            out[name] = State("external", managed, managed,
                              "orphan: không còn trong library" if managed else "skill tự cài, chưa có trong library")
    return out


def _unlink_dir(p: Path) -> None:
    try:
        p.unlink()
    except (IsADirectoryError, PermissionError, OSError):
        os.rmdir(p)                          # Windows: directory symlink/junction


def skill_install(spec, scope, project, lib, item: Item, mode: str = "symlink", replace: bool = False) -> Result:
    d = spec.resolve("skills", scope, project)
    if d is None:
        return Result(False, f"{spec.label} không hỗ trợ skills ở scope {scope}")
    st = skill_states(spec, scope, project, lib).get(item.name)
    target = d / item.skill_name
    if st and st.status == "installed" and target.is_symlink() and _real(target) == _real(item.path):
        return Result(True, f"{item.name} đã được cài cho {spec.label}")
    if st and st.status == "external" and not st.managed:
        return Result(False, f"{target} đã tồn tại và không do opssum quản lý")
    if os.path.lexists(target) and st and st.status == "absent" and not replace:
        return Result(False, f"{target} đang dùng bản cùng tên từ nguồn khác; cần xác nhận thay thế.")
    if mode != "symlink":
        return Result(False, "Skills cần dùng symlink để mọi agent nhận cập nhật từ library.")
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".opssum-link-", dir=d) as temp:
        link, backup = Path(temp) / "link", Path(temp) / "backup"
        # Create the link before touching the old installation. No copy fallback.
        os.symlink(_real(item.path), link, target_is_directory=True)
        had_old = os.path.lexists(target)
        if had_old:
            target.rename(backup)
        try:
            link.rename(target)
        except OSError:
            if had_old:
                backup.rename(target)
            raise
    return Result(True, f"Đã cài skill {item.name} → {target} (symlink)")


def skill_uninstall(spec, scope, project, lib, name: str) -> Result:
    d = spec.resolve("skills", scope, project)
    if d is None:
        return Result(False, "không hỗ trợ")
    item = lib.get("skill", name)
    leaf = item.skill_name if item else name
    if not NAME_RE.fullmatch(leaf):
        return Result(False, "Tên skill không hợp lệ hoặc không có trong library.")
    target = d / leaf
    if not os.path.lexists(target):
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    if not _skill_managed(target, lib.dir_for("skill")):
        return Result(False, f"{target} không do opssum tạo — hãy xoá tay nếu chắc chắn")
    if item and skill_states(spec, scope, project, lib)[name].status == "absent":
        return Result(True, f"{name} chưa được cài; giữ nguyên bản của nhà phát hành khác.")
    if target.is_symlink():
        _unlink_dir(target)
    else:
        shutil.rmtree(target)
    return Result(True, f"Đã gỡ skill {name} khỏi {spec.label}")


def _same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right, ignore=[MARKER])
    if comparison.left_only or comparison.right_only or comparison.common_funny:
        return False
    if any(not filecmp.cmp(left / name, right / name, shallow=False) for name in comparison.common_files):
        return False
    return all(_same_tree(left / name, right / name) for name in comparison.common_dirs)


def skill_adopt(spec, scope, project, lib, name: str, *, overwrite: bool = False) -> Result:
    if not NAME_RE.fullmatch(name):
        return Result(False, "Tên skill nhập vào không hợp lệ.")
    directory = spec.resolve("skills", scope, project)
    if directory is None:
        return Result(False, "Agent không hỗ trợ skills ở scope này.")
    src = directory / name
    dst = lib.dir_for("skill") / name
    if not (src / "SKILL.md").is_file():
        return Result(False, f"{src} không phải skill hợp lệ")
    if src.is_symlink() and _skill_managed(src, lib.dir_for("skill")):
        return Result(True, f"{name} đã liên kết với library.")
    if os.path.lexists(dst) and (dst.is_symlink() or not (dst / "SKILL.md").is_file()):
        return Result(False, f"{dst} không phải thư mục skill có thể đồng bộ.")
    if _inside(dst, src):
        return Result(False, "Thư mục nguồn chứa đường dẫn library; không thể copy vào chính nó.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    was_symlink = src.is_symlink()
    # Stage a complete, independent copy before touching either original path.
    library_temp = Path(tempfile.mkdtemp(prefix=".opssum-import-", dir=dst.parent))
    agent_temp = None
    keep_backups = False
    try:
        agent_temp = Path(tempfile.mkdtemp(prefix=".opssum-adopt-", dir=directory))
        stage, old_library = Path(library_temp) / "new", Path(library_temp) / "old"
        link, old_source = Path(agent_temp) / "link", Path(agent_temp) / "old"
        shutil.copytree(src, stage, symlinks=False)
        had_library = os.path.lexists(dst)
        reuse = had_library and _same_tree(stage, dst)
        if had_library and not reuse and not overwrite:
            raise SkillConflict(f"Library đã có {name} với nội dung khác.")
        os.symlink(_real(dst), link, target_is_directory=True)
        saved_library = installed_library = saved_source = False
        try:
            if not reuse:
                if had_library:
                    dst.rename(old_library)
                    saved_library = True
                stage.rename(dst)
                installed_library = True
            src.rename(old_source)
            saved_source = True
            link.rename(src)
        except OSError:
            try:
                if saved_source:
                    old_source.rename(src)
                if installed_library:
                    dst.rename(stage)
                if saved_library:
                    old_library.rename(dst)
            except OSError as exc:
                keep_backups = True
                raise OpsError(f"Không thể khôi phục tự động. Dữ liệu được giữ tại {library_temp} và {agent_temp}: {exc}") from exc
            raise
    finally:
        if not keep_backups:
            shutil.rmtree(library_temp)
            if agent_temp is not None:
                shutil.rmtree(agent_temp)
    detail = "Nguồn thật được giữ nguyên; symlink agent đã được thay." if was_symlink else "Thư mục gốc được thay bằng symlink."
    return Result(True, f"Đã đồng bộ {name} vào library. {detail}")


# ============================================================ INSTRUCTIONS
_IBLOCK = re.compile(
    r"<!-- opssum:begin (?P<name>\S+) -->\n(?P<body>.*?)\n<!-- opssum:end (?P=name) -->\n?",
    re.S,
)


def _iblock(name: str, body: str) -> str:
    return f"<!-- opssum:begin {name} -->\n{body.strip()}\n<!-- opssum:end {name} -->\n"


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
    r"# >>> opssum:(?P<name>\S+) >>>\n(?P<body>.*?)# <<< opssum:(?P=name) <<<\n?", re.S
)
_TTABLE = re.compile(r'^\[mcp_servers\.("[^"]+"|[A-Za-z0-9_-]+)\][ \t]*$', re.M)


def _tblock(name: str, body: str) -> str:
    return f"# >>> opssum:{name} >>>\n{body}# <<< opssum:{name} <<<\n"


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


def mcp_states(spec: AgentSpec, scope, project, lib, home: Path | None = None) -> dict[str, State]:
    f = spec.resolve("mcp", scope, project)
    items = {i.name: i for i in lib.items("mcp")}
    out: dict[str, State] = {}
    if f is None:
        return {n: State("absent") for n in items}
    owned = mcp_bindings(home).get(str(f.resolve()), {}) if home else {}
    if spec.mcp_format == "toml":
        text = _read_text(f)
        managed = {m.group("name"): m.group("body") for m in _TBLOCK.finditer(text)}
        rest = _TBLOCK.sub("", text)
        ext = {m.group(1).strip('"') for m in _TTABLE.finditer(rest)}
        for n, it in items.items():
            leaf = it.server_name
            if leaf in managed and (not owned.get(leaf) or owned[leaf] == n):
                try:
                    same = managed[leaf] == render_toml(leaf, it.spec)
                except OpsError:
                    same = False
                out[n] = State("installed") if same else State("outdated", True, True, "khác library")
            elif leaf in ext:
                out[n] = State("external", False, False, "đã có server cùng tên trong config.toml (tự khai báo)")
            else:
                out[n] = State("absent", detail="agent đang dùng MCP cùng tên từ nhà phát hành khác" if leaf in managed else "")
        leaves = {i.server_name for i in items.values()}
        for n in managed.keys() - leaves:
            out[n] = State("external", True, True, "orphan: không còn trong library")
        for n in ext - leaves:
            out[n] = State("external", False, False, "khai báo tay trong config.toml (sửa bằng tay)")
        return out
    servers = _json_servers(_json_load(f), f)
    for n, it in items.items():
        leaf = it.server_name
        if leaf in servers:
            if owned.get(leaf) and owned[leaf] != n:
                out[n] = State("absent", detail="agent đang dùng MCP cùng tên từ nhà phát hành khác")
            elif servers[leaf] == render_json(spec.mcp_style, it.spec):
                out[n] = (State("installed") if owned.get(leaf) == n or not home else
                          State("external", False, False, "cấu hình tự khai báo; chưa đồng bộ vào library"))
            else:
                out[n] = State("outdated" if owned.get(leaf) == n else "external", False, False,
                               "khác library (có thể bạn đã sửa tay)")
        else:
            out[n] = State("absent")
    leaves = {i.server_name for i in items.values()}
    for n in servers.keys() - leaves:
        out[n] = State("external", False, False, "server tự khai báo, chưa có trong library")
    return out


def mcp_install(spec, scope, project, lib, item: Item, *, replace: bool = False, home: Path | None = None) -> Result:
    f = spec.resolve("mcp", scope, project)
    if f is None:
        return Result(False, f"{spec.label} không hỗ trợ MCP ở scope {scope}")
    if spec.mcp_format == "toml":
        text = _read_text(f)
        leaf = item.server_name
        body = render_toml(leaf, item.spec)
        names = {m.group("name") for m in _TBLOCK.finditer(text)}
        owned = mcp_bindings(home).get(str(f.resolve()), {}) if home else {}
        if leaf in names:
            if owned.get(leaf) not in (None, item.name) and not replace:
                return Result(False, f"{leaf} đang dùng bản {owned[leaf]}; cần xác nhận thay thế")
            current_block = next(m.group("body") for m in _TBLOCK.finditer(text) if m.group("name") == leaf)
            if current_block != body and not replace:
                return Result(False, f"{leaf} trong {f} khác library; cần xác nhận ghi đè")
            text = _TBLOCK.sub(lambda m: _tblock(leaf, body) if m.group("name") == leaf else m.group(0), text)
        else:
            rest = _TBLOCK.sub("", text)
            if any(m.group(1).strip('"') == leaf for m in _TTABLE.finditer(rest)):
                return Result(False, f"{f} đã có [mcp_servers.{leaf}] tự khai báo — không ghi đè")
            if text and not text.endswith("\n"):
                text += "\n"
            if text.strip():
                text += "\n"
            text += _tblock(leaf, body)
        atomic_write(f, text)
        if home:
            record_mcp(home, f, leaf, item.name)
        return Result(True, f"Đã cài MCP {item.name} → {f}")
    data = _json_load(f)
    servers = _json_servers(data, f)
    leaf = item.server_name
    current = mcp_bindings(home).get(str(f.resolve()), {}).get(leaf) if home else None
    if leaf in servers and servers[leaf] != render_json(spec.mcp_style, item.spec) and not replace:
        return Result(False, f"{f} đã có MCP {leaf}; cần xác nhận ghi đè")
    if current and current != item.name and not replace:
        return Result(False, f"{leaf} đang dùng bản {current}; cần xác nhận thay thế")
    servers[leaf] = render_json(spec.mcp_style, item.spec)
    data["mcpServers"] = servers
    atomic_write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if home:
        record_mcp(home, f, leaf, item.name)
    extra = " (cần extension pi-mcp-adapter)" if spec.id == "pi" else ""
    return Result(True, f"Đã cài MCP {item.name} → {f}{extra}")


def mcp_uninstall(spec, scope, project, lib, name: str, home: Path | None = None) -> Result:
    f = spec.resolve("mcp", scope, project)
    if f is None or not f.exists():
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    item = lib.get("mcp", name)
    leaf = item.server_name if item else name
    if home and mcp_bindings(home).get(str(f.resolve()), {}).get(leaf) not in (None, name):
        return Result(False, f"{leaf} đang thuộc một MCP khác; không gỡ")
    if spec.mcp_format == "toml":
        text = _read_text(f)
        new = _TBLOCK.sub(lambda m: "" if m.group("name") == leaf else m.group(0), text)
        if new == text:
            return Result(False, f"{name} khai báo tay trong {f} — hãy sửa bằng tay")
        atomic_write(f, re.sub(r"\n{3,}", "\n\n", new).lstrip("\n"))
        if home:
            forget_mcp(home, f, leaf)
        return Result(True, f"Đã gỡ MCP {name} khỏi {f}")
    data = _json_load(f)
    servers = _json_servers(data, f)
    if leaf not in servers:
        return Result(True, f"{name} chưa được cài cho {spec.label}")
    if home and mcp_bindings(home).get(str(f.resolve()), {}).get(leaf) != name:
        return Result(False, f"{leaf} không do opssum quản lý; dùng đồng bộ trước")
    del servers[leaf]
    data["mcpServers"] = servers
    atomic_write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if home:
        forget_mcp(home, f, leaf)
    return Result(True, f"Đã gỡ MCP {name} khỏi {f}")


def mcp_adopt(spec, scope, project, lib, name: str, home: Path | None = None, *, overwrite: bool = False) -> Result:
    f = spec.resolve("mcp", scope, project)
    dst = lib.dir_for("mcp") / f"{name}.json"
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
    if dst.exists():
        existing = lib.get("mcp", name)
        if existing is None or existing.spec != norm:
            if not overwrite:
                raise McpConflict(f"Library đã có MCP {name} với cấu hình khác.")
    out = {"description": "", **{k: v for k, v in norm.items() if v not in ({}, [])}}
    if out.get("transport") == "http":
        out.pop("transport")
    if not dst.exists() or overwrite:
        dst.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(dst, json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        dst.chmod(0o600)
    if spec.mcp_format == "toml":
        text = _read_text(f)
        # Preserve unrelated sections verbatim. Only replace tables belonging to this server.
        lines = text.splitlines(keepends=True)
        header = re.compile(r'^\s*\[([^]]+)\]\s*$')
        sections = []
        for index, line in enumerate(lines):
            match = header.match(line)
            if match:
                sections.append((index, match.group(1).strip('"')))
        remove = set()
        for index, (start, section) in enumerate(sections):
            if section == f"mcp_servers.{name}" or section.startswith(f"mcp_servers.{name}."):
                end = sections[index + 1][0] if index + 1 < len(sections) else len(lines)
                remove.update(range(start, end))
        new = "".join(line for index, line in enumerate(lines) if index not in remove)
        if new and not new.endswith("\n"):
            new += "\n"
        if new.strip():
            new += "\n"
        new += _tblock(name, render_toml(name, norm))
        atomic_write(f, new)
    else:
        data = _json_load(f)
        data["mcpServers"][name] = render_json(spec.mcp_style, norm)
        atomic_write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if home:
        record_mcp(home, f, name, name)
    return Result(True, f"Đã nhập MCP {name} vào library (mcp/{name}.json) — nhớ kiểm tra secrets/env")


# ============================================================ MANAGER
class Manager:
    """Điểm vào duy nhất cho TUI và CLI."""

    def __init__(self, home: Path, project: Path | None = None, skill_mode: str = "symlink"):
        self.home = Path(home).expanduser().resolve()
        self.project = Path(project or Path.cwd())
        self.skill_mode = skill_mode
        self.agents = load_agents(self.home)
        self.lib = Library(self.home)
        self.errors: dict[tuple[str, str, str], str] = {}
        self.remember_skill_locations()
        self.remember_mcp_locations()

    def skill_locations(self) -> list[Path]:
        return [p for spec in self.agent_list() for scope in ("global", "project")
                if (p := spec.resolve("skills", scope, self.project)) is not None]

    def external_skills(self) -> tuple[list[ExternalSkill], list[str]]:
        found: dict[tuple[str, Path], ExternalSkill] = {}
        errors = []
        for scope in ("global", "project"):
            for spec in self.agent_list():
                directory = spec.resolve("skills", scope, self.project)
                if directory is None or not directory.is_dir():
                    continue
                try:
                    for path in sorted(directory.iterdir()):
                        if not NAME_RE.fullmatch(path.name) or not (path / "SKILL.md").is_file():
                            continue
                        if _skill_managed(path, self.lib.dir_for("skill")):
                            continue
                        # Resolve the containing directory, not the skill symlink:
                        # distinct agent links pointing to the same source all need replacing.
                        key = (scope, path.parent.resolve() / path.name)
                        previous = found.get(key)
                        agents = (*previous.agents, spec.id) if previous else (spec.id,)
                        found[key] = ExternalSkill(path.name, previous.agent_id if previous else spec.id,
                                                   scope, path, agents)
                except OSError as exc:
                    errors.append(f"{directory}: {exc}")
        return list(found.values()), errors

    def external_mcps(self) -> tuple[list[ExternalMcp], list[str]]:
        found: dict[tuple[str, Path, str], ExternalMcp] = {}
        errors = []
        try:
            owned = mcp_bindings(self.home)
        except (ValueError, OSError) as exc:
            return [], [str(exc)]
        for scope in ("global", "project"):
            for spec in self.agent_list():
                path = spec.resolve("mcp", scope, self.project)
                if path is None or not path.is_file():
                    continue
                try:
                    if spec.mcp_format == "toml":
                        rest = _TBLOCK.sub("", _read_text(path))
                        names = {m.group(1).strip('"') for m in _TTABLE.finditer(rest)}
                    else:
                        names = set(_json_servers(_json_load(path), path))
                    for name in sorted(names):
                        if not NAME_RE.fullmatch(name) or name in owned.get(str(path.resolve()), {}):
                            continue
                        key = (scope, path.resolve(), name)
                        previous = found.get(key)
                        agents = (*previous.agents, spec.id) if previous else (spec.id,)
                        found[key] = ExternalMcp(name, previous.agent_id if previous else spec.id,
                                                 scope, path, agents)
                except (OSError, OpsError) as exc:
                    errors.append(str(exc))
        return list(found.values()), errors

    def remember_skill_locations(self) -> None:
        # Opening an older project records its existing library links.
        try:
            locations = [p for p in self.skill_locations() if p.is_dir() and any(
                _skill_managed(c, self.lib.dir_for("skill")) for c in p.iterdir() if not c.name.startswith("."))]
            if locations:
                remember_locations(self.home, locations)
        except (ValueError, OSError) as exc:
            self.lib.errors.append(f"Không ghi nhận được đường dẫn skills: {exc}")

    def remember_mcp_locations(self) -> None:
        # Legacy TOML blocks have an explicit marker, so they can be attributed
        # when a previously used project is opened. JSON has no marker and must
        # be offered for explicit sync instead of being claimed automatically.
        try:
            seen: set[Path] = set()
            for scope in ("global", "project"):
                for agent in self.agent_list():
                    path = agent.resolve("mcp", scope, self.project)
                    if path is None or agent.mcp_format != "toml" or path in seen or not path.is_file():
                        continue
                    seen.add(path)
                    for block in _TBLOCK.finditer(_read_text(path)):
                        matching = []
                        for item in self.lib.items("mcp"):
                            if item.server_name != block.group("name"):
                                continue
                            try:
                                if render_toml(item.server_name, item.spec) == block.group("body"):
                                    matching.append(item)
                            except OpsError:
                                continue
                        if len(matching) == 1:
                            record_mcp(self.home, path, block.group("name"), matching[0].name)
        except (OSError, ValueError, OpsError) as exc:
            self.lib.errors.append(f"Không ghi nhận được đường dẫn MCP: {exc}")

    def skill_conflict(self, agent_id: str, scope: str, name: str) -> str | None:
        item = self.lib.get("skill", name)
        target = self.target_path(agent_id, scope, "skill", name)
        if item and target and os.path.lexists(target) and _skill_managed(target, self.lib.dir_for("skill")):
            source = _real(target) if target.is_symlink() else _real(Path(_read_text(target / MARKER).strip()))
            if source != _real(item.path):
                return next((i.name for i in self.lib.items("skill") if _real(i.path) == source), str(source))
        return None

    def skill_dependents(self, name: str) -> list[Path]:
        item = self.lib.get("skill", name)
        if item is None:
            raise OpsError(f"Library không có skill {name}")
        return linked_paths(self.home, item.path, self.skill_locations())

    def remove_skill(self, name: str) -> Result:
        item = self.lib.get("skill", name)
        if item is None:
            return Result(False, f"Library không có skill {name}")
        try:
            paths = self.skill_dependents(name)
            trash = remove_from_library(self.home, item.path, paths)
            self.lib.load()
            return Result(True, f"Đã gỡ {name} khỏi library và {len(paths)} vị trí agent. Bản khôi phục: {trash}")
        except (OSError, ValueError) as exc:
            return Result(False, str(exc))

    def save_mcp(self, reference: str, definition: dict, *, overwrite: bool = False) -> Result:
        parts = reference.split("/")
        if len(parts) not in (1, 2) or any(not NAME_RE.fullmatch(part) for part in parts):
            return Result(False, "Tên MCP phải là name hoặc publisher/name hợp lệ.")
        existing = self.lib.get("mcp", reference)
        path = existing.path if existing is not None else self.lib.dir_for("mcp").joinpath(*parts[:-1], parts[-1] + ".json")
        if path.exists() and not overwrite:
            return Result(False, f"Library đã có {reference}; cần xác nhận ghi đè.")
        try:
            spec, description = normalize_mcp(definition)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.parent.is_symlink():
                raise OpsError("Không ghi cấu hình MCP qua symlink trong library.")
            payload = {"description": description, **spec}
            if existing and path != self.lib.dir_for("mcp").joinpath(*parts[:-1], parts[-1] + ".json"):
                original = _json_load(path)
                if isinstance(original.get("mcpServers"), dict):
                    original["mcpServers"][existing.server_name] = payload
                    payload = original
            atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            path.chmod(0o600)
            self.lib.load()
            return Result(True, f"Đã lưu MCP {reference} vào library.")
        except (OSError, ValueError, OpsError) as exc:
            return Result(False, str(exc))

    def mcp_dependents(self, name: str) -> list[tuple[Path, str]]:
        if self.lib.get("mcp", name) is None:
            raise OpsError(f"Library không có MCP {name}")
        entries = mcp_bindings(self.home)
        return sorted((Path(path), server) for path, names in entries.items()
                      for server, reference in names.items() if reference == name)

    def remove_mcp(self, name: str) -> Result:
        item = self.lib.get("mcp", name)
        if item is None or item.path is None:
            return Result(False, f"Library không có MCP {name}")
        try:
            dependents = self.mcp_dependents(name)
            root = self.lib.dir_for("mcp").resolve()
            source = item.path.absolute()
            source.relative_to(root)
            source.resolve().relative_to(root)
            if source.is_symlink() or source.suffix != ".json":
                raise OpsError("File MCP trong library không hợp lệ.")
            # Validate all locations before touching any config.
            for path, server in dependents:
                if not path.exists():
                    continue
                if path.suffix == ".toml":
                    if server not in {m.group("name") for m in _TBLOCK.finditer(_read_text(path))}:
                        raise OpsError(f"MCP đã thay đổi tại {path}; hãy tải lại và kiểm tra.")
                elif server not in _json_servers(_json_load(path), path):
                    raise OpsError(f"MCP đã thay đổi tại {path}; hãy tải lại và kiểm tra.")
            trash = self.home / ".trash" / uuid4().hex
            trash.mkdir(parents=True)
            (trash / "manifest.json").write_text(json.dumps({"source": str(source),
                "configs": [str(path) for path, _ in dependents]}, ensure_ascii=False, indent=2), encoding="utf-8")
            previous_bindings = mcp_bindings(self.home)
            updated_bindings = json.loads(json.dumps(previous_bindings))
            changed: list[tuple[Path, Path]] = []
            source_changed = False
            try:
                for index, (path, server) in enumerate(dependents):
                    key = str(path.resolve())
                    updated_bindings.get(key, {}).pop(server, None)
                    if key in updated_bindings and not updated_bindings[key]:
                        del updated_bindings[key]
                    if not path.exists():
                        continue
                    backup = trash / f"config-{index}{path.suffix}"
                    shutil.copy2(path, backup)
                    if path.suffix == ".toml":
                        text = _read_text(path)
                        new = _TBLOCK.sub(lambda m: "" if m.group("name") == server else m.group(0), text)
                        atomic_write(path, re.sub(r"\n{3,}", "\n\n", new).lstrip("\n"))
                    else:
                        data = _json_load(path)
                        del data["mcpServers"][server]
                        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                    changed.append((path, backup))
                raw_source = _json_load(source)
                if isinstance(raw_source.get("mcpServers"), dict) and len(raw_source["mcpServers"]) > 1:
                    shutil.copy2(source, trash / "mcp.json")
                    del raw_source["mcpServers"][item.server_name]
                    atomic_write(source, json.dumps(raw_source, indent=2, ensure_ascii=False) + "\n")
                else:
                    source.rename(trash / "mcp.json")
                source_changed = True
                write_mcp_bindings(self.home, updated_bindings)
            except (OSError, ValueError):
                if source_changed:
                    shutil.copy2(trash / "mcp.json", source)
                for path, backup in reversed(changed):
                    shutil.copy2(backup, path)
                write_mcp_bindings(self.home, previous_bindings)
                raise
            self.lib.load()
            return Result(True, f"Đã xoá MCP {name} và gỡ {len(dependents)} vị trí. Bản khôi phục: {trash}")
        except (OSError, ValueError, OpsError) as exc:
            return Result(False, str(exc))

    def mcp_conflict(self, agent_id: str, scope: str, name: str) -> str | None:
        item = self.lib.get("mcp", name)
        if item is None:
            return None
        spec = self.agents[agent_id]
        path = spec.resolve("mcp", scope, self.project)
        if path is None:
            return None
        leaf = item.server_name
        if spec.mcp_format == "toml":
            text = _read_text(path)
            if leaf not in {m.group("name") for m in _TBLOCK.finditer(text)}:
                return None
        else:
            if leaf not in _json_servers(_json_load(path), path):
                return None
        owner = mcp_bindings(self.home).get(str(path.resolve()), {}).get(leaf)
        return owner if owner and owner != name else ("bản tự khai báo" if owner is None and spec.mcp_format != "toml" else None)

    def reload(self) -> None:
        self.agents = load_agents(self.home)
        self.lib.load()
        self.errors.clear()
        self.remember_skill_locations()
        self.remember_mcp_locations()

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
            return mcp_states(spec, scope, self.project, self.lib, self.home)
        except OpsError as e:
            self.errors[(agent_id, scope, kind)] = str(e)
        except OSError as e:
            self.errors[(agent_id, scope, kind)] = f"Lỗi I/O: {e}"
        return {i.name: State("absent") for i in self.lib.items(kind)}

    def target_path(self, agent_id: str, scope: str, kind: str, name: str) -> Path | None:
        spec = self.agents[agent_id]
        if kind == "skill":
            d = spec.resolve("skills", scope, self.project)
            item = self.lib.get("skill", name)
            return d / (item.skill_name if item else name) if d else None
        return spec.resolve("instr" if kind == "instruction" else "mcp", scope, self.project)

    def shared_with(self, agent_id: str, scope: str, kind: str) -> list[str]:
        what = {"skill": "skills", "instruction": "instr", "mcp": "mcp"}[kind]
        mine = self.agents[agent_id].resolve(what, scope, self.project)
        if mine is None:
            return []
        return [a.id for a in self.agents.values()
                if a.id != agent_id and a.resolve(what, scope, self.project) == mine]

    # ---- actions
    def install(self, agent_id: str, scope: str, kind: str, name: str, *, replace: bool = False) -> Result:
        item = self.lib.get(kind, name)
        if item is None:
            return Result(False, f"library không có {kind}:{name}")
        spec = self.agents[agent_id]
        try:
            if kind == "skill":
                directory = spec.resolve("skills", scope, self.project)
                if directory:
                    remember_locations(self.home, [directory])
                return skill_install(spec, scope, self.project, self.lib, item, self.skill_mode, replace)
            if kind == "instruction":
                return instr_install(spec, scope, self.project, self.lib, item)
            return mcp_install(spec, scope, self.project, self.lib, item, replace=replace, home=self.home)
        except (OpsError, ValueError) as e:
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
            return mcp_uninstall(spec, scope, self.project, self.lib, name, self.home)
        except (OpsError, ValueError) as e:
            return Result(False, str(e))
        except OSError as e:
            return Result(False, f"Lỗi I/O: {e}")

    def adopt(self, agent_id: str, scope: str, kind: str, name: str, *, overwrite: bool = False) -> Result:
        spec = self.agents[agent_id]
        try:
            if kind == "skill":
                directory = spec.resolve("skills", scope, self.project)
                if directory:
                    remember_locations(self.home, [directory])
                r = skill_adopt(spec, scope, self.project, self.lib, name, overwrite=overwrite)
            elif kind == "mcp":
                r = mcp_adopt(spec, scope, self.project, self.lib, name, self.home, overwrite=overwrite)
            else:
                return Result(False, "Instructions chỉ nhập thủ công (copy nội dung vào instructions/<name>.md)")
        except (SkillConflict, McpConflict):
            raise
        except (OpsError, ValueError) as e:
            return Result(False, str(e))
        except OSError as e:
            return Result(False, f"Lỗi I/O: {e}")
        if r.ok:
            self.lib.load()
        return r
