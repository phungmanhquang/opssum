"""Giao diện TUI (Textual) — ma trận item × agent, phong cách gần Claude Code."""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static, TextArea

from . import __version__
from . import remote, mcp_marketplace
from .catalog import KIND_LABEL, KINDS, Item
from .ops import Manager, Result, State, ExternalSkill, ExternalMcp, SkillConflict, McpConflict

ACCENT = "#d97757"
OK = "#4eba65"
WARN = "#ffc107"
DIM = "#8a847d"
EXT = "#6fa8dc"

SYMBOL = {"installed": ("●", OK), "outdated": ("◐", WARN), "absent": ("○", DIM), "external": ("◌", EXT)}
STATUS_TEXT = {"installed": "đã cài", "outdated": "cần cập nhật", "absent": "chưa cài", "external": "external"}


def _tilde(p: Path | str | None) -> str:
    if p is None:
        return "—"
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h) else s


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


@dataclass
class Row:
    kind: str
    name: str
    item: Item | None
    states: dict[str, State | None]

    @property
    def key(self) -> str:
        return f"@external:{self.name}" if self.item is None else self.name


# ----------------------------------------------------------------- modals
class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", "Có"), Binding("n,escape", "no", "Không")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("Xác nhận", style=f"bold {ACCENT}"))
            with VerticalScroll(id="confirm-message"):
                yield Static(Text(self.message))
            yield Static(Text("[y] đồng ý    [n] huỷ", style=DIM))

    def on_mount(self) -> None:
        self.query_one("#confirm-message", VerticalScroll).focus()

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PromptScreen(ModalScreen["str | None"]):
    BINDINGS = [Binding("escape", "cancel", "Huỷ")]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self.title_text = title
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.title_text, style=f"bold {ACCENT}"))
            yield Input(value=self.value)
            yield Static(Text("Enter để xác nhận · Esc để huỷ", style=DIM))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SkillDescriptionScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,i", "close", "Quay lại")]

    def __init__(self, skill: remote.RemoteSkill):
        super().__init__()
        self.skill = skill

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-description-dialog"):
            yield Static(Text(self.skill.name, style="bold #e5e5e5"))
            with VerticalScroll(id="skill-description-scroll"):
                yield Static(Text(self.skill.description or "Skill này chưa có mô tả.", style="#a3a3a3"))
            yield Static("↑ ↓ cuộn · Esc / i quay lại", classes="skill-hint")

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class SkillSelectScreen(ModalScreen[list[int] | None]):
    BINDINGS = [Binding("escape", "cancel", "Huỷ"),
                Binding("enter", "submit", "Cài đã chọn", priority=True),
                Binding("space", "toggle_skill", "Chọn", priority=True),
                Binding("i", "description", "Mô tả", priority=True)]

    def __init__(self, skills: list[remote.RemoteSkill], home: Path):
        super().__init__()
        self.skills = skills
        self.home = home
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-select-dialog"):
            yield Static(Text("Install skills", style="bold #e5e5e5"))
            publisher = self.skills[0].publisher if self.skills else ""
            yield Static(Text(f"Nhà phát hành: {publisher or 'other'}"), classes="skill-hint")
            yield DataTable(id="remote-skills", cursor_type="row", show_row_labels=False,
                            cursor_foreground_priority="renderable")
            yield Static(id="skill-selection-count", classes="skill-hint")
            yield Static("Space chọn/bỏ · i mô tả · Enter cài · Esc huỷ", classes="skill-hint")

    def on_mount(self) -> None:
        self.call_after_refresh(self._populate_table)

    def on_resize(self) -> None:
        self.call_after_refresh(self._populate_table)

    def _populate_table(self) -> None:
        table = self.query_one("#remote-skills", DataTable)
        cursor = table.cursor_row
        table.clear(columns=True)
        table.add_column("", key="check", width=3)
        width = self.query_one("#skill-select-dialog").content_size.width
        name_width = min(28, max(12, width // 3))
        table.add_column("Skill", key="name", width=name_width)
        table.add_column("Mô tả", key="description", width=max(8, width - name_width - 13))
        for i, skill in enumerate(self.skills):
            name = Text(skill.name, style="bold #d4d4d4")
            if remote.exists(self.home, skill):
                name.append(" (đã có)", style="#a3a3a3")
            check = Text("[✓]", style="bold #e5e5e5") if i in self.selected else Text("[ ]", style="#737373")
            table.add_row(check, name,
                          Text(skill.description, style="#a3a3a3", no_wrap=True, overflow="ellipsis"),
                          key=str(i), height=1)
        self._update_count()
        table.move_cursor(row=cursor)
        table.focus()

    def _update_count(self) -> None:
        self.query_one("#skill-selection-count", Static).update(f"Đã chọn {len(self.selected)} / {len(self.skills)} skills")

    def action_toggle_skill(self) -> None:
        table = self.query_one("#remote-skills", DataTable)
        index = table.cursor_row
        if not 0 <= index < len(self.skills):
            return
        if index in self.selected:
            self.selected.remove(index)
        else:
            self.selected.add(index)
        table.update_cell(str(index), "check", Text("[✓]" if index in self.selected else "[ ]",
                                                   style="bold #e5e5e5" if index in self.selected else "#737373"))
        self._update_count()

    def action_description(self) -> None:
        index = self.query_one("#remote-skills", DataTable).cursor_row
        if 0 <= index < len(self.skills):
            self.app.push_screen(SkillDescriptionScreen(self.skills[index]))

    def action_submit(self) -> None:
        if not self.selected:
            self.notify("Chọn ít nhất một skill bằng Space.", severity="warning")
            return
        self.dismiss(sorted(self.selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


class McpSearchScreen(ModalScreen[int | None]):
    BINDINGS = [Binding("escape", "cancel", "Đóng"),
                Binding("i", "description", "Mô tả", priority=True)]

    def __init__(self, items: list[mcp_marketplace.Listing]):
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-search-dialog"):
            yield Static("Kết quả MCP · Claude Marketplaces", classes="sync-title")
            yield DataTable(id="mcp-results", cursor_type="row")
            yield Static("Enter chọn để cấu hình · i xem mô tả · Esc đóng", classes="skill-hint")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("MCP", width=26)
        table.add_column("Nhà phát hành", width=18)
        table.add_column("Mô tả", width=52)
        for index, item in enumerate(self.items):
            table.add_row(Text(item.name, style="bold"), item.publisher,
                          Text(item.description, style=DIM, no_wrap=True, overflow="ellipsis"), key=str(index))
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(int(event.row_key.value))

    def action_description(self) -> None:
        index = self.query_one(DataTable).cursor_row
        if 0 <= index < len(self.items):
            item = self.items[index]
            self.app.push_screen(McpDescriptionScreen(item))

    def action_cancel(self) -> None:
        self.dismiss(None)


class McpDescriptionScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,i", "close", "Quay lại")]

    def __init__(self, item: mcp_marketplace.Listing):
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-description-dialog"):
            yield Static(self.item.name, classes="sync-title")
            with VerticalScroll(id="skill-description-scroll"):
                yield Static(Text(self.item.description or "Chưa có mô tả.", style=DIM))
                yield Static(f"Nguồn: {mcp_marketplace.BASE}/mcp/{self.item.slug}", classes="skill-hint")
            yield Static("Esc / i quay lại", classes="skill-hint")

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class McpConfigScreen(ModalScreen[tuple[str, dict] | None]):
    BINDINGS = [Binding("ctrl+s", "save", "Lưu", priority=True), Binding("escape", "cancel", "Huỷ", priority=True)]

    def __init__(self, reference: str, config: dict, title: str = "Cấu hình MCP"):
        super().__init__()
        self.reference = reference
        self.config = config
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-config-dialog"):
            yield Static(self.title_text, classes="sync-title")
            yield Static("Tên trong library: publisher/server hoặc server", classes="skill-hint")
            yield Input(value=self.reference, id="mcp-reference")
            yield Static("Sửa JSON bên dưới; điền API key/env/header trực tiếp trước khi lưu.", classes="skill-hint")
            yield TextArea(json.dumps(self.config, indent=2, ensure_ascii=False), id="mcp-json", language="json")
            yield Static("Ctrl+S lưu · Esc huỷ · Chỉ lưu cấu hình; không tải package/Docker", classes="skill-hint")

    def action_save(self) -> None:
        ref = self.query_one("#mcp-reference", Input).value.strip()
        try:
            config = json.loads(self.query_one("#mcp-json", TextArea).text)
            from .catalog import NAME_RE, normalize_mcp
            if len(ref.split("/")) not in (1, 2) or any(not NAME_RE.fullmatch(p) for p in ref.split("/")):
                raise ValueError("Tên phải là server hoặc publisher/server hợp lệ.")
            normalize_mcp(config)
        except (ValueError, TypeError) as exc:
            self.notify(str(exc), severity="error", timeout=6)
            return
        self.dismiss((ref, config))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SyncSkillsScreen(ModalScreen[list[int] | None]):
    BINDINGS = [Binding("escape", "cancel", "Đóng", priority=True),
                Binding("enter", "submit", "Đồng bộ", priority=True),
                Binding("space", "toggle", "Chọn/bỏ", priority=True)]

    def __init__(self, skills: list[ExternalSkill | ExternalMcp], project: Path):
        super().__init__()
        self.skills = skills
        self.project = project
        self.selected = set(range(len(skills)))

    def compose(self) -> ComposeResult:
        with Vertical(id="sync-dialog"):
            yield Static("Đồng bộ skills và MCP vào opssum?", classes="sync-title")
            yield Static(Text(f"Project: {_tilde(self.project)}"), classes="skill-hint")
            yield Static("Skills thành symlink; MCP được lưu cấu hình. Không tải package hay Docker.", classes="skill-hint")
            yield DataTable(id="sync-skills", cursor_type="row", cursor_foreground_priority="renderable")
            yield Static(id="sync-count", classes="skill-hint")
            yield Static("Space chọn/bỏ · Enter đồng bộ đã chọn · Esc đóng", classes="skill-hint")

    def on_mount(self) -> None:
        self.call_after_refresh(self._populate)

    def _populate(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", key="check", width=3)
        table.add_column("Item", key="name", width=24)
        width = self.query_one("#sync-dialog").content_size.width
        table.add_column("Agent / đường dẫn", key="source", width=max(12, width - 38))
        first = None
        for kind, scope, label in (("skill", "global", "Global skills"),
                                    ("skill", "project", "Project skills"),
                                    ("mcp", "global", "Global MCP"),
                                    ("mcp", "project", "Project MCP")):
            indices = [i for i, skill in enumerate(self.skills)
                       if skill.scope == scope and ("skill" if isinstance(skill, ExternalSkill) else "mcp") == kind]
            table.add_row("", Text(f"{label} ({len(indices)})", style="bold #e5e5e5"),
                          "" if indices else "Không có item chưa quản lý", key=f"scope:{kind}:{scope}")
            for i in indices:
                skill = self.skills[i]
                if first is None:
                    first = table.row_count
                table.add_row(Text("[✓]", style="bold #e5e5e5"), Text(skill.name),
                              Text(f"{', '.join(skill.agents or (skill.agent_id,))}: {_tilde(skill.path)}", style="#a3a3a3"), key=str(i))
        self._count()
        table.move_cursor(row=first or 0)
        table.focus()

    def _count(self) -> None:
        self.query_one("#sync-count", Static).update(f"Đã chọn {len(self.selected)} / {len(self.skills)} items")

    def action_toggle(self) -> None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is None or key.startswith("scope:"):
            return
        index = int(key)
        self.selected.symmetric_difference_update({index})
        table.update_cell(key, "check", Text("[✓]" if index in self.selected else "[ ]", style="#e5e5e5"))
        self._count()

    def action_submit(self) -> None:
        self.dismiss(sorted(self.selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SyncProgressScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Đang đồng bộ skills…", id="sync-progress")


HELP = [
    ("i", "Skills: GitHub owner/repo · MCP: tìm trên Claude Marketplaces"),
    ("e", "sửa cấu hình MCP trong library"),
    ("Di chuyển", "↑ ↓ ← →  chọn ô (hàng = item, cột = agent)"),
    ("space / enter", "cài ↔ gỡ item cho agent ở cột đang chọn"),
    ("u", "cập nhật item đang lệch (◐) theo library"),
    ("A / X", "cài / gỡ item cho TẤT CẢ agent"),
    ("m", "nhập item external (◌) vào library"),
    ("d", "xoá skill/MCP khỏi library và gỡ khỏi agent đã ghi nhận"),
    ("1 2 3  [ ]", "chuyển Skills / MCP / Instructions"),
    ("f", "lọc theo 1 agent (xem & quản lý riêng agent đó)"),
    ("o", "chỉ hiện item đã cài"),
    ("s", "đổi scope global ↔ project"),
    ("P", "đổi thư mục project"),
    ("r", "tải lại library và config agent"),
    ("q", "thoát"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark,enter", "close", "Đóng")]

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("Phím tắt\n\n", style=f"bold {ACCENT}")
        for k, v in HELP:
            t.append(f"{k:<14}", style="bold")
            t.append(f"{v}\n")
        t.append("\n")
        for sym, label in (("installed", "đã cài"), ("outdated", "lệch với library"),
                           ("absent", "chưa cài"), ("external", "có ở agent nhưng chưa có trong library")):
            s, c = SYMBOL[sym]
            t.append(f"{s} ", style=c)
            t.append(f"{label}   ", style=DIM)
        t.append("\n\n* sau tên agent = chưa thấy binary trong PATH", style=DIM)
        with Vertical(id="dialog"):
            yield Static(t)

    def action_close(self) -> None:
        self.dismiss(None)


# -------------------------------------------------------------------- app
class OpssumApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "opssum"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("i", "remote_install", "Tìm/cài"),
        Binding("e", "edit_mcp", "sửa MCP", show=False),
        Binding("space", "toggle", "cài/gỡ", key_display="space"),
        Binding("enter", "toggle", "cài/gỡ", show=False),
        Binding("u", "update", "cập nhật", show=False),
        Binding("A", "install_all", "cài all", show=False),
        Binding("X", "uninstall_all", "gỡ all", show=False),
        Binding("m", "adopt", "nhập", show=True),
        Binding("d", "remove_skill", "xoá library"),
        Binding("f", "filter", "agent"),
        Binding("s", "scope", "scope"),
        Binding("o", "only", "đã cài"),
        Binding("1", "tab('skill')", "Skills", show=False),
        Binding("2", "tab('mcp')", "MCP", show=False),
        Binding("3", "tab('instruction')", "Instructions", show=False),
        Binding("left_square_bracket", "prev_tab", "prev", show=False),
        Binding("right_square_bracket", "next_tab", "next", show=False),
        Binding("r", "reload", "tải lại", show=False),
        Binding("P", "project_path", "project", show=False),
        Binding("question_mark", "help", "trợ giúp", key_display="?"),
        Binding("q", "quit", "thoát"),
    ]

    def __init__(self, mgr: Manager, scope: str = "global"):
        super().__init__()
        self.mgr = mgr
        self.cur_scope = scope
        self.cur_kind = "skill"
        self.agent_filter: str | None = None
        self.only_installed = False
        self.rows_data: list[Row] = []
        self.col_keys: list[str] = []
        self._loaded = False
        self._remote_busy = False

    # ------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        yield Static(id="tabs")
        yield Static(id="filters")
        yield DataTable(id="matrix", cursor_type="cell", zebra_stripes=False)
        yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#banner", Static).border_title = f"✻ opssum  v{__version__}"
        self.query_one("#matrix", DataTable).focus()
        self._loaded = True
        self.rebuild()
        self.run_worker(self._startup_sync(), name="startup-sync")

    def on_resize(self) -> None:
        if self._loaded:
            self.rebuild()

    # -------------------------------------------------------------- state
    def visible_agents(self):
        if self.agent_filter and self.agent_filter in self.mgr.agents:
            return [self.mgr.agents[self.agent_filter]]
        return self.mgr.agent_list()

    def _collect(self) -> tuple[list[Row], dict[str, dict[str, State]]]:
        kind = self.cur_kind
        lib_items = {i.name: i for i in self.mgr.lib.items(kind)}
        per = {a.id: self.mgr.states(a.id, self.cur_scope, kind) for a in self.mgr.agent_list()}
        names = set(lib_items)
        for st in per.values():
            names |= set(st)
        if kind in ("skill", "mcp"):
            external: dict[str, dict[str, State]] = {}
            for aid, states in per.items():
                for name, state in states.items():
                    if state.status == "external":
                        leaf = lib_items[name].skill_name if name in lib_items else name
                        external.setdefault(leaf, {})[aid] = state
            rows = [Row(kind, item.name, item, {aid: (State("absent") if states.get(item.name, State("absent")).status == "external"
                        else states.get(item.name, State("absent"))) for aid, states in per.items()}) for item in lib_items.values()]
            rows += [Row(kind, name, None, {aid: states.get(aid, State("absent")) for aid in per})
                     for name, states in external.items()]
            rows.sort(key=lambda r: (r.item is None, r.name.lower()))
            return rows, per
        rows = [Row(kind, n, lib_items.get(n), {aid: st.get(n, State("absent")) for aid, st in per.items()})
                for n in names]
        rows.sort(key=lambda r: (r.item is None, r.name.lower()))
        return rows, per

    def _cursor_keys(self) -> tuple[str | None, str | None]:
        table = self.query_one("#matrix", DataTable)
        if table.row_count == 0:
            return None, None
        try:
            ck = table.coordinate_to_cell_key(table.cursor_coordinate)
            return ck.row_key.value, ck.column_key.value
        except Exception:
            return None, None

    def _current(self) -> tuple[Row, str | None] | None:
        rk, ck = self._cursor_keys()
        if rk is None:
            return None
        row = next((r for r in self.rows_data if r.key == rk), None)
        if row is None:
            return None
        if ck in self.mgr.agents:
            return row, ck
        single = self.visible_agents()
        return row, (single[0].id if len(single) == 1 else None)

    # ------------------------------------------------------------ render
    def rebuild(self) -> None:
        table = self.query_one("#matrix", DataTable)
        prev_row, prev_col = self._cursor_keys()
        rows, per = self._collect()
        agents = self.visible_agents()
        single = len(agents) == 1

        # lọc / sắp xếp
        if self.only_installed:
            rows = [r for r in rows
                    if any((r.states.get(a.id) or State("absent")).status != "absent" for a in agents)]
        if single:
            aid = agents[0].id
            rows.sort(key=lambda r: ((r.states.get(aid) or State("absent")).status == "absent",
                                     r.item is None, r.name.lower()))
        if self.cur_kind in ("skill", "mcp"):
            rows.sort(key=lambda r: (2, "") if r.item is None else
                      ((0, r.item.publisher.casefold()) if r.item.publisher else (1, "")))
        self.rows_data = rows

        # độ rộng cột
        lib_total = len(self.mgr.lib.items(self.cur_kind))
        agent_w = 12
        name_w = 22
        status_w = 14 if single else 0
        ncols = 2 + len(agents) + (1 if single else 0)
        avail = max(self.size.width, 80) - 8 - 2 * ncols - name_w - agent_w * len(agents) - status_w
        desc_w = max(14, min(52, avail))

        table.clear(columns=True)
        self.col_keys = ["name", "desc"]
        table.add_column("Tên", key="name", width=name_w)
        table.add_column("Mô tả", key="desc", width=desc_w)
        for a in agents:
            done = sum(1 for n, s in per[a.id].items()
                       if s.status in ("installed", "outdated") and self.mgr.lib.get(self.cur_kind, n))
            label = a.label + ("" if a.detected() else "*")
            if lib_total:
                label += f" {done}/{lib_total}"
            table.add_column(Text(label, justify="center"), key=a.id, width=agent_w)
            self.col_keys.append(a.id)
        if single:
            table.add_column("Trạng thái", key="status", width=status_w)
            self.col_keys.append("status")

        row_keys = []
        last_publisher = None
        grouped = self.cur_kind in ("skill", "mcp")
        for r in rows:
            publisher = r.item.publisher if r.item else "@external"
            if grouped and publisher != last_publisher:
                divider = f"@publisher:{publisher}"
                label = "External" if r.item is None else (publisher or "other")
                table.add_row(Text(f"── {label}", style="bold #a3a3a3"),
                              *[Text("─" * column.width, style="#333333")
                                for column in list(table.columns.values())[1:]], key=divider)
                row_keys.append(divider)
                last_publisher = publisher
            label = r.item.skill_name if r.item and self.cur_kind in ("skill", "mcp") else r.name
            name = Text(label, style="bold") if r.item else Text(r.name, style=f"italic {EXT}")
            desc = Text(_clip(r.item.description, desc_w - 1), style=DIM) if r.item else \
                Text("external — chưa có trong library", style=f"italic {DIM}")
            cells = [name, desc] + [self._cell(r.states.get(a.id)) for a in agents]
            if single:
                st = r.states.get(agents[0].id)
                cells.append(self._status_text(st))
            table.add_row(*cells, key=r.key)
            row_keys.append(r.key)

        if table.row_count:
            ri = row_keys.index(prev_row) if prev_row in row_keys else next(
                (i for i, key in enumerate(row_keys) if not key.startswith("@publisher:")), 0)
            ci = self.col_keys.index(prev_col) if prev_col in self.col_keys else 2
            table.move_cursor(row=ri, column=min(ci, len(self.col_keys) - 1), animate=False)

        self._render_header(agents)
        self.update_detail()

    @staticmethod
    def _cell(st: State | None) -> Text:
        if st is None:
            return Text("–", style=DIM, justify="center")
        sym, color = SYMBOL[st.status]
        return Text(sym, style=color, justify="center")

    @staticmethod
    def _status_text(st: State | None) -> Text:
        if st is None:
            return Text("—", style=DIM)
        return Text(STATUS_TEXT[st.status], style=SYMBOL[st.status][1])

    def _render_header(self, agents) -> None:
        mgr = self.mgr
        counts = "  ·  ".join(f"{len(mgr.lib.items(k))} {KIND_LABEL[k].lower()}" for k in KINDS)
        b = Text()
        b.append("✻ ", style=ACCENT)
        b.append("Xin chào! Quản lý skills, MCP và instructions cho mọi agent CLI.\n", style="bold")
        b.append("  library  ", style=DIM)
        b.append(_tilde(mgr.home) + "\n")
        b.append("  scope    ", style=DIM)
        if self.cur_scope == "global":
            b.append("global", style=f"bold {ACCENT}")
            b.append("  (config người dùng)\n", style=DIM)
        else:
            b.append("project", style=f"bold {ACCENT}")
            b.append(f"  {_tilde(mgr.project)}\n", style=DIM)
        b.append("  items    ", style=DIM)
        b.append(counts)
        if mgr.lib.errors:
            b.append(f"   ⚠ {len(mgr.lib.errors)} lỗi: {mgr.lib.errors[0]}", style=WARN)
        self.query_one("#banner", Static).update(b)

        tabs = Text()
        for i, k in enumerate(KINDS, 1):
            chip = f" {i} {KIND_LABEL[k]} ({len(mgr.lib.items(k))}) "
            tabs.append(chip, style=f"bold #1a1918 on {ACCENT}" if k == self.cur_kind else DIM)
            tabs.append("  ")
        self.query_one("#tabs", Static).update(tabs)

        f = Text()
        f.append("agent ", style=DIM)
        f.append("[all]" if not self.agent_filter else " all ",
                 style=f"bold {ACCENT}" if not self.agent_filter else DIM)
        for a in mgr.agent_list():
            on = self.agent_filter == a.id
            f.append(f" {a.label}{'' if a.detected() else '*'} " if not on else f"[{a.label}]",
                     style=f"bold {ACCENT}" if on else DIM)
            f.append(" ")
        f.append("   chỉ đã cài: ", style=DIM)
        f.append("bật" if self.only_installed else "tắt", style=f"bold {ACCENT}" if self.only_installed else DIM)
        self.query_one("#filters", Static).update(f)

    def update_detail(self) -> None:
        box = self.query_one("#detail", Static)
        cur = self._current()
        if cur is None:
            d = self.mgr.lib.dir_for(self.cur_kind)
            box.update(Text(
                f"Chưa có {KIND_LABEL[self.cur_kind]} nào.\n"
                f"Thêm file vào {_tilde(d)} rồi nhấn r — hoặc chạy `opssum init --examples`.",
                style=DIM))
            return
        row, aid = cur
        t = Text()
        t.append(row.name, style=f"bold {ACCENT}")
        t.append(f"   {KIND_LABEL[row.kind]}", style=DIM)
        if row.item is None:
            t.append("   external — chưa có trong library", style=EXT)
        t.append("\n")
        if row.item and row.item.description:
            t.append(_clip(row.item.description, 220) + "\n")
        if row.item and row.item.path:
            t.append(f"nguồn    {_tilde(row.item.path)}\n", style=DIM)
        if aid:
            st = row.states.get(aid)
            t.append(f"{aid:<8} ", style="bold")
            if st:
                t.append(STATUS_TEXT[st.status], style=SYMBOL[st.status][1])
            t.append(f"   {_tilde(self.mgr.target_path(aid, self.cur_scope, row.kind, row.name))}\n", style=DIM)
            if st and st.detail:
                t.append(f"         ⚠ {st.detail}\n", style=WARN)
            shared = self.mgr.shared_with(aid, self.cur_scope, row.kind)
            if shared:
                t.append(f"         ⚠ dùng chung đường dẫn với {', '.join(shared)} — cài/gỡ ảnh hưởng cả các agent này\n",
                         style=WARN)
            err = self.mgr.errors.get((aid, self.cur_scope, row.kind))
            if err:
                t.append(f"         ✘ {err}\n", style="#e5534b")
        else:
            t.append("← → chọn cột agent rồi nhấn space để cài/gỡ\n", style=DIM)
        box.update(t)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if self._loaded:
            self.update_detail()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        self.action_toggle()          # Enter trên ô = cài/gỡ (DataTable chiếm phím enter trước app)

    # ------------------------------------------------------------ helpers
    def _finish(self, r: Result) -> None:
        self.notify(r.msg, severity="information" if r.ok else "error", timeout=5)
        self.rebuild()

    def _confirm(self, message: str, then) -> None:
        def cb(ok: bool | None) -> None:
            if ok:
                then()
        self.push_screen(ConfirmScreen(message), cb)

    # ------------------------------------------------------------ actions
    async def _startup_sync(self) -> None:
        self._remote_busy = True
        try:
            skills, errors = await self._remote_io(self.mgr.external_skills)
            mcps, mcp_errors = await self._remote_io(self.mgr.external_mcps)
            errors += mcp_errors
            for error in errors:
                self.notify(error, severity="warning", timeout=8)
            candidates = [*skills, *mcps]
            if candidates:
                selected = await self.push_screen_wait(SyncSkillsScreen(candidates, self.mgr.project))
                if selected:
                    await self._sync_candidates([candidates[i] for i in selected])
        finally:
            self._remote_busy = False

    async def _sync_candidates(self, skills: list[ExternalSkill | ExternalMcp]) -> None:
        progress = SyncProgressScreen()
        await self.push_screen(progress)
        count = 0
        errors = []
        try:
            for index, skill in enumerate(skills, 1):
                progress.query_one("#sync-progress", Static).update(Text(f"Đồng bộ {index}/{len(skills)}: {skill.name}\n{skill.path}"))
                try:
                    kind = "skill" if isinstance(skill, ExternalSkill) else "mcp"
                    result = await self._remote_io(self.mgr.adopt, skill.agent_id, skill.scope, kind, skill.name)
                except SkillConflict:
                    if not await self.push_screen_wait(ConfirmScreen(
                        f"Library đã có {skill.name} với nội dung khác.\nGhi đè bằng bản từ {skill.path}?\n"
                        "Các agent đang liên kết tới bản trong library cũng nhận nội dung mới."
                    )):
                        continue
                    result = await self._remote_io(self.mgr.adopt, skill.agent_id, skill.scope, "skill", skill.name, overwrite=True)
                except McpConflict:
                    if not await self.push_screen_wait(ConfirmScreen(
                        f"Library đã có MCP {skill.name} với cấu hình khác.\n"
                        f"Ghi đè bằng bản tại {skill.path}? Các agent khác sẽ cần cập nhật cấu hình.")):
                        continue
                    result = await self._remote_io(self.mgr.adopt, skill.agent_id, skill.scope, "mcp", skill.name, overwrite=True)
                if result.ok:
                    count += 1
                else:
                    errors.append(f"{skill.path}: {result.msg}")
        finally:
            self.pop_screen()
            self.mgr.reload()
            self.rebuild()
        self.notify(f"Đã đồng bộ {count}/{len(skills)} items.", timeout=5)
        for error in errors:
            self.notify(error, severity="error", timeout=10)

    def action_remote_install(self) -> None:
        if self._remote_busy:
            self.notify("Đang xử lý nguồn từ xa, vui lòng chờ.")
            return
        self._remote_busy = True
        if self.cur_kind == "mcp":
            self.run_worker(self._remote_mcp(), name="search-mcp")
        elif self.cur_kind == "skill":
            self.run_worker(self._remote_install(), name="install-skills")
        else:
            self._remote_busy = False
            self.notify("Chọn tab Skills hoặc MCP để tìm cài.", severity="warning")

    async def _remote_mcp(self) -> None:
        try:
            query = await self.push_screen_wait(PromptScreen("Tìm MCP trên Claude Marketplaces"))
            if not query:
                return
            self.notify(f"Đang tìm MCP: {query}…", timeout=5)
            listings = await self._remote_io(mcp_marketplace.search, query)
            if not listings:
                self.notify("Không tìm thấy MCP phù hợp.", severity="warning")
                return
            index = await self.push_screen_wait(McpSearchScreen(listings))
            if index is None:
                return
            listing = listings[index]
            try:
                name, spec = await self._remote_io(mcp_marketplace.configuration, listing)
            except ValueError as exc:
                self.notify(str(exc), severity="warning", timeout=8)
                name, spec = listing.slug.rsplit("/", 1)[-1], {"command": "", "args": []}
            reference = f"{listing.publisher}/{name}"
            config = {"description": listing.description, **spec}
            edited = await self.push_screen_wait(McpConfigScreen(reference, config, f"Cấu hình MCP · {listing.name}"))
            if edited is None:
                return
            reference, config = edited
            overwrite = self.mgr.lib.get("mcp", reference) is not None
            if overwrite and not await self.push_screen_wait(ConfirmScreen(
                f"Ghi đè MCP {reference} trong library?\nCác agent đang quản lý MCP này sẽ cần cập nhật.")):
                return
            self._finish(self.mgr.save_mcp(reference, config, overwrite=overwrite))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.notify(str(exc), severity="error", timeout=8)
        finally:
            self._remote_busy = False
            self.mgr.reload()
            if self.is_running:
                self.rebuild()

    def action_edit_mcp(self) -> None:
        cur = self._current()
        if cur is None or cur[0].kind != "mcp" or cur[0].item is None:
            self.notify("Chọn MCP trong library để sửa cấu hình.", severity="warning")
            return
        item = cur[0].item
        async def edit() -> None:
            config = {"description": item.description, **item.spec}
            edited = await self.push_screen_wait(McpConfigScreen(item.name, config, f"Sửa MCP · {item.name}"))
            if edited is None:
                return
            reference, body = edited
            if reference != item.name:
                self.notify("Không đổi tên khi sửa; hãy cài bản mới nếu cần tên khác.", severity="warning")
                return
            self._finish(self.mgr.save_mcp(reference, body, overwrite=True))
        self.run_worker(edit(), name="edit-mcp")

    async def _remote_install(self) -> None:
        try:
            repository = await self.push_screen_wait(PromptScreen("Install skills — GitHub owner/repo (vd anthropics/skills)"))
            if not repository:
                return
            self.notify(f"Đang tải {repository}…", timeout=5)
            with tempfile.TemporaryDirectory(prefix="opssum-skills-") as temp:
                skills = await self._remote_io(remote.download, repository, Path(temp) / "repo")
                selected = await self.push_screen_wait(SkillSelectScreen(skills, self.mgr.home))
                if selected is None:
                    return
                count = 0
                for i in selected:
                    skill = skills[i]
                    overwrite = remote.exists(self.mgr.home, skill)
                    if overwrite and not await self.push_screen_wait(ConfirmScreen(
                        f"Ghi đè skill [{skill.reference}] trong library?\nChỉnh sửa cũ sẽ mất; agent liên kết tới skill này cũng nhận bản mới."
                    )):
                        continue
                    await self._remote_io(remote.install, self.mgr.home, skill, overwrite=overwrite)
                    count += 1
                self.notify(f"Đã cài {count}/{len(selected)} skill vào library.")
        except (ValueError, OSError) as exc:
            self.notify(str(exc), severity="error", timeout=8)
        finally:
            self._remote_busy = False
            self.mgr.reload()
            if self.is_running:
                self.rebuild()

    async def _remote_io(self, function, *args, **kwargs):
        # Wait for background file operations before temporary-directory cleanup.
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                raise

    def action_toggle(self) -> None:
        cur = self._current()
        if cur is None:
            return
        row, aid = cur
        if aid is None:
            self.notify("Dùng ← → để chọn cột agent rồi nhấn space.", severity="warning")
            return
        st = row.states.get(aid)
        if st is None:
            return
        if st.status == "absent":
            conflict = (self.mgr.skill_conflict(aid, self.cur_scope, row.name) if row.kind == "skill" else
                        self.mgr.mcp_conflict(aid, self.cur_scope, row.name) if row.kind == "mcp" else None)
            if conflict:
                self._confirm(f"{aid} đang dùng {conflict}.\nThay bằng {row.name}?", lambda:
                              self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name, replace=True)))
            else:
                self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name))
            return
        if st.status == "external" and not st.removable:
            self.notify(st.detail or "Không thể gỡ item này từ đây.", severity="warning")
            return

        def do() -> None:
            self._finish(self.mgr.uninstall(aid, self.cur_scope, row.kind, row.name))

        if st.managed:
            do()
        else:
            self._confirm(f"Gỡ [{row.name}] khỏi {aid}?\nItem này không do opssum tạo "
                          f"(file config sẽ được backup trước khi sửa).", do)

    def action_update(self) -> None:
        cur = self._current()
        if cur is None or cur[1] is None or cur[0].item is None:
            return
        row, aid = cur
        st = row.states.get(aid)
        if st is None or st.status != "outdated":
            self.notify("Item này không cần cập nhật.", severity="information")
            return

        def do() -> None:
            self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name,
                                          replace=row.kind == "mcp" and not st.managed))

        if st.managed:
            do()
        else:
            self._confirm(f"Ghi đè [{row.name}] của {aid} bằng bản trong library?\n"
                          f"Mọi chỉnh sửa tay sẽ mất (có backup).", do)

    def action_install_all(self) -> None:
        cur = self._current()
        if cur is None or cur[0].item is None:
            self.notify("Chỉ cài được item có trong library.", severity="warning")
            return
        row = cur[0]
        targets = [a.id for a in self.mgr.agent_list()
                   if (row.states.get(a.id) or State("absent")).status in ("absent", "outdated")
                   and (row.states.get(a.id) or State("absent")).managed]
        conflicts = [f"{aid}: {conflict}" for aid in targets
                     if (conflict := (self.mgr.skill_conflict(aid, self.cur_scope, row.name) if row.kind == "skill" else
                                      self.mgr.mcp_conflict(aid, self.cur_scope, row.name) if row.kind == "mcp" else None))]

        def do() -> None:
            results = [self.mgr.install(aid, self.cur_scope, row.kind, row.name, replace=bool(conflicts)) for aid in targets]
            ok = sum(r.ok for r in results)
            self.notify(f"Cài {row.name}: {ok}/{len(results)} agent thành công.",
                        severity="information" if ok == len(results) else "warning")
            self.rebuild()

        if conflicts:
            self._confirm(f"Thay các bản sau bằng {row.name}?\n" + "\n".join(conflicts), do)
        else:
            do()

    def action_uninstall_all(self) -> None:
        cur = self._current()
        if cur is None:
            return
        row = cur[0]
        targets = [a.id for a in self.mgr.agent_list()
                   if (row.states.get(a.id) or State("absent")).status in ("installed", "outdated")
                   or ((row.states.get(a.id) or State("absent")).status == "external"
                       and (row.states.get(a.id)).managed and (row.states.get(a.id)).removable)]
        if not targets:
            self.notify("Không agent nào đang cài item này.", severity="information")
            return

        def do() -> None:
            res = [self.mgr.uninstall(a, self.cur_scope, row.kind, row.name) for a in targets]
            ok = sum(r.ok for r in res)
            self.notify(f"Gỡ {row.name}: {ok}/{len(res)} agent thành công.",
                        severity="information" if ok == len(res) else "warning")
            self.rebuild()

        self._confirm(f"Gỡ [{row.name}] khỏi: {', '.join(targets)}?", do)

    def action_adopt(self) -> None:
        cur = self._current()
        if cur is None or cur[1] is None:
            return
        row, aid = cur
        st = row.states.get(aid)
        if row.item is not None or st is None or st.status != "external":
            self.notify("Chỉ nhập được item external (◌) chưa có trong library.", severity="warning")
            return
        if row.kind == "skill":
            candidate = ExternalSkill(row.name, aid, self.cur_scope,
                                      self.mgr.agents[aid].resolve("skills", self.cur_scope, self.mgr.project) / row.name)
            self._confirm(f"Đồng bộ {row.name} vào library?\nThư mục gốc được chuyển; nếu là symlink, nguồn thật được giữ nguyên.", lambda:
                          self.run_worker(self._sync_candidates([candidate]), name="import-skill"))
        else:
            self._confirm(f"Đồng bộ MCP {row.name} vào library?\nCấu hình gốc tại agent sẽ được quản lý bởi opssum.",
                          lambda: self.run_worker(self._sync_candidates([
                              ExternalMcp(row.name, aid, self.cur_scope,
                                          self.mgr.agents[aid].resolve("mcp", self.cur_scope, self.mgr.project))
                          ]), name="import-mcp"))

    def action_remove_skill(self) -> None:
        cur = self._current()
        if cur is None or cur[0].kind not in ("skill", "mcp") or cur[0].item is None:
            self.notify("Chọn skill hoặc MCP trong library để xoá.", severity="warning")
            return
        row = cur[0]
        try:
            paths = self.mgr.skill_dependents(row.name) if row.kind == "skill" else self.mgr.mcp_dependents(row.name)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        message = f"Xoá {row.name} khỏi library và gỡ {len(paths)} vị trí agent?\n"
        message += "\n".join(_tilde(p if isinstance(p, Path) else p[0]) for p in paths)
        message += "\nBản khôi phục được lưu trong library/.trash."
        self._confirm(message, lambda: self._finish(self.mgr.remove_skill(row.name) if row.kind == "skill"
                                                  else self.mgr.remove_mcp(row.name)))

    def action_filter(self) -> None:
        order: list[str | None] = [None] + [a.id for a in self.mgr.agent_list()]
        i = order.index(self.agent_filter) if self.agent_filter in order else 0
        self.agent_filter = order[(i + 1) % len(order)]
        self.rebuild()

    def action_only(self) -> None:
        self.only_installed = not self.only_installed
        self.rebuild()

    def action_scope(self) -> None:
        self.cur_scope = "project" if self.cur_scope == "global" else "global"
        if self.cur_scope == "project":
            self.notify(f"Scope project: {_tilde(self.mgr.project)} (nhấn P để đổi thư mục)", timeout=4)
        self.rebuild()

    def action_tab(self, kind: str) -> None:
        self.cur_kind = kind
        self.rebuild()

    def action_next_tab(self) -> None:
        self.action_tab(KINDS[(KINDS.index(self.cur_kind) + 1) % len(KINDS)])

    def action_prev_tab(self) -> None:
        self.action_tab(KINDS[(KINDS.index(self.cur_kind) - 1) % len(KINDS)])

    def action_reload(self) -> None:
        self.mgr.reload()
        self.rebuild()
        self.notify("Đã tải lại library và cấu hình agent.", timeout=2)

    def action_project_path(self) -> None:
        def cb(value: str | None) -> None:
            if not value:
                return
            p = Path(value).expanduser()
            if not p.is_dir():
                self.notify(f"{value} không phải thư mục.", severity="error")
                return
            self.mgr.project = p.resolve()
            self.mgr.remember_skill_locations()
            self.mgr.remember_mcp_locations()
            self.rebuild()
        self.push_screen(PromptScreen("Thư mục project", str(self.mgr.project)), cb)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def run(mgr: Manager) -> None:
    OpssumApp(mgr).run()
