"""Giao diện TUI (Textual) — ma trận item × agent, phong cách gần Claude Code."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static, SelectionList

from . import __version__
from . import remote
from .catalog import KIND_LABEL, KINDS, Item
from .ops import Manager, Result, State

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


# ----------------------------------------------------------------- modals
class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", "Có"), Binding("n,escape", "no", "Không")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text.assemble(("Xác nhận\n\n", f"bold {ACCENT}"), self.message,
                                       ("\n\n[y] đồng ý    [n] huỷ", DIM)))

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


class SkillSelectScreen(ModalScreen[list[int] | None]):
    BINDINGS = [Binding("escape", "cancel", "Huỷ"),
                Binding("enter", "submit", "Cài đã chọn", priority=True)]

    def __init__(self, skills: list[remote.RemoteSkill], home: Path):
        super().__init__()
        self.skills = skills
        self.home = home

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Install skills — chọn skill để thêm vào library")
            yield SelectionList(*[
                (Text(f"{s.name}{' (đã có)' if remote.exists(self.home, s.name) else ''} — {s.description}"), i)
                for i, s in enumerate(self.skills)
            ], id="remote-skills")
            yield Static("↑ ↓ di chuyển · Space chọn/bỏ · Enter cài · Esc huỷ")

    def on_mount(self) -> None:
        self.query_one(SelectionList).focus()

    def action_submit(self) -> None:
        selected = self.query_one(SelectionList).selected
        if not selected:
            self.notify("Chọn ít nhất một skill bằng Space.", severity="warning")
            return
        self.dismiss(sorted(selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


HELP = [
    ("i", "Install skills từ GitHub (owner/repo) vào library"),
    ("Di chuyển", "↑ ↓ ← →  chọn ô (hàng = item, cột = agent)"),
    ("space / enter", "cài ↔ gỡ item cho agent ở cột đang chọn"),
    ("u", "cập nhật item đang lệch (◐) theo library"),
    ("A / X", "cài / gỡ item cho TẤT CẢ agent"),
    ("m", "nhập item external (◌) vào library"),
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
class AgentKnowledgeApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "Agent Knowledge"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("i", "remote_install", "Install skills"),
        Binding("space", "toggle", "cài/gỡ", key_display="space"),
        Binding("enter", "toggle", "cài/gỡ", show=False),
        Binding("u", "update", "cập nhật", show=False),
        Binding("A", "install_all", "cài all", show=False),
        Binding("X", "uninstall_all", "gỡ all", show=False),
        Binding("m", "adopt", "nhập", show=True),
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
        self.query_one("#banner", Static).border_title = f"✻ Agent Knowledge  v{__version__}"
        self.query_one("#matrix", DataTable).focus()
        self._loaded = True
        self.rebuild()

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
        row = next((r for r in self.rows_data if r.name == rk), None)
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

        for r in rows:
            name = Text(r.name, style="bold") if r.item else Text(r.name, style=f"italic {EXT}")
            desc = Text(_clip(r.item.description, desc_w - 1), style=DIM) if r.item else \
                Text("external — chưa có trong library", style=f"italic {DIM}")
            cells = [name, desc] + [self._cell(r.states.get(a.id)) for a in agents]
            if single:
                st = r.states.get(agents[0].id)
                cells.append(self._status_text(st))
            table.add_row(*cells, key=r.name)

        if table.row_count:
            names = [r.name for r in rows]
            ri = names.index(prev_row) if prev_row in names else 0
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
                f"Thêm file vào {_tilde(d)} rồi nhấn r — hoặc chạy `agent-knowledge init --examples`.",
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
    def action_remote_install(self) -> None:
        if self._remote_busy:
            self.notify("Đang xử lý repository, vui lòng chờ.")
            return
        self._remote_busy = True
        self.run_worker(self._remote_install(), name="install-skills")

    async def _remote_install(self) -> None:
        try:
            repository = await self.push_screen_wait(PromptScreen("Install skills — GitHub owner/repo (vd anthropics/skills)"))
            if not repository:
                return
            self.notify(f"Đang tải {repository}…", timeout=5)
            with tempfile.TemporaryDirectory(prefix="ak-skills-") as temp:
                skills = await self._remote_io(remote.download, repository, Path(temp) / "repo")
                selected = await self.push_screen_wait(SkillSelectScreen(skills, self.mgr.home))
                if selected is None:
                    return
                count = 0
                for i in selected:
                    skill = skills[i]
                    overwrite = remote.exists(self.mgr.home, skill.name)
                    if overwrite and not await self.push_screen_wait(ConfirmScreen(
                        f"Ghi đè skill [{skill.name}] trong library?\nChỉnh sửa cũ sẽ mất; agent liên kết tới skill này cũng nhận bản mới."
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
            self._confirm(f"Gỡ [{row.name}] khỏi {aid}?\nItem này không do agent-knowledge tạo "
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
            self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name))

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
        results = [self.mgr.install(a.id, self.cur_scope, row.kind, row.name)
                   for a in self.mgr.agent_list()
                   if (row.states.get(a.id) or State("absent")).status in ("absent", "outdated")
                   and (row.states.get(a.id) or State("absent")).managed]
        ok = sum(r.ok for r in results)
        self.notify(f"Cài {row.name}: {ok}/{len(results)} agent thành công.",
                    severity="information" if ok == len(results) else "warning")
        self.rebuild()

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
        self._finish(self.mgr.adopt(aid, self.cur_scope, row.kind, row.name))

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
            self.rebuild()
        self.push_screen(PromptScreen("Thư mục project", str(self.mgr.project)), cb)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def run(mgr: Manager) -> None:
    AgentKnowledgeApp(mgr).run()
