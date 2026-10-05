"""Textual TUI: an item-by-agent matrix inspired by Claude Code."""
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
STATUS_TEXT = {"installed": "installed", "outdated": "needs update", "absent": "not installed", "external": "external"}


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
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("Confirm", style=f"bold {ACCENT}"))
            with VerticalScroll(id="confirm-message"):
                yield Static(Text(self.message))
            yield Static(Text("[y] yes    [n] cancel", style=DIM))

    def on_mount(self) -> None:
        self.query_one("#confirm-message", VerticalScroll).focus()

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PromptScreen(ModalScreen["str | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self.title_text = title
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.title_text, style=f"bold {ACCENT}"))
            yield Input(value=self.value)
            yield Static(Text("Enter to confirm · Esc to cancel", style=DIM))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SkillDescriptionScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,i", "close", "Back")]

    def __init__(self, skill: remote.RemoteSkill):
        super().__init__()
        self.skill = skill

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-description-dialog"):
            yield Static(Text(self.skill.name, style="bold #e5e5e5"))
            with VerticalScroll(id="skill-description-scroll"):
                yield Static(Text(self.skill.description or "This skill has no description.", style="#a3a3a3"))
            yield Static("↑ ↓ scroll · Esc / i back", classes="skill-hint")

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class SkillSelectScreen(ModalScreen[list[int] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"),
                Binding("enter", "submit", "Install selected", priority=True),
                Binding("space", "toggle_skill", "Select", priority=True),
                Binding("i", "description", "Description", priority=True)]

    def __init__(self, skills: list[remote.RemoteSkill], home: Path):
        super().__init__()
        self.skills = skills
        self.home = home
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-select-dialog"):
            yield Static(Text("Install skills", style="bold #e5e5e5"))
            publisher = self.skills[0].publisher if self.skills else ""
            yield Static(Text(f"Publisher: {publisher or 'other'}"), classes="skill-hint")
            yield DataTable(id="remote-skills", cursor_type="row", show_row_labels=False,
                            cursor_foreground_priority="renderable")
            yield Static(id="skill-selection-count", classes="skill-hint")
            yield Static("Space select/deselect · i description · Enter install · Esc cancel", classes="skill-hint")

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
        table.add_column("Description", key="description", width=max(8, width - name_width - 13))
        for i, skill in enumerate(self.skills):
            name = Text(skill.name, style="bold #d4d4d4")
            if remote.exists(self.home, skill):
                name.append(" (already in library)", style="#a3a3a3")
            check = Text("[✓]", style="bold #e5e5e5") if i in self.selected else Text("[ ]", style="#737373")
            table.add_row(check, name,
                          Text(skill.description, style="#a3a3a3", no_wrap=True, overflow="ellipsis"),
                          key=str(i), height=1)
        self._update_count()
        table.move_cursor(row=cursor)
        table.focus()

    def _update_count(self) -> None:
        self.query_one("#skill-selection-count", Static).update(f"Selected {len(self.selected)} / {len(self.skills)} skills")

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
            self.notify("Select at least one skill with Space.", severity="warning")
            return
        self.dismiss(sorted(self.selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


class McpSearchScreen(ModalScreen[int | None]):
    BINDINGS = [Binding("escape", "cancel", "Close"),
                Binding("i", "description", "Description", priority=True)]

    def __init__(self, items: list[mcp_marketplace.Listing]):
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-search-dialog"):
            yield Static("MCP results · Claude Marketplaces", classes="sync-title")
            yield DataTable(id="mcp-results", cursor_type="row")
            yield Static("Enter configure · i view description · Esc close", classes="skill-hint")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("MCP", width=26)
        table.add_column("Publisher", width=18)
        table.add_column("Description", width=52)
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
    BINDINGS = [Binding("escape,i", "close", "Back")]

    def __init__(self, item: mcp_marketplace.Listing):
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-description-dialog"):
            yield Static(self.item.name, classes="sync-title")
            with VerticalScroll(id="skill-description-scroll"):
                yield Static(Text(self.item.description or "No description available.", style=DIM))
                yield Static(f"Source: {mcp_marketplace.BASE}/mcp/{self.item.slug}", classes="skill-hint")
            yield Static("Esc / i back", classes="skill-hint")

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class McpConfigScreen(ModalScreen[tuple[str, dict] | None]):
    BINDINGS = [Binding("ctrl+s", "save", "Save", priority=True), Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, reference: str, config: dict, title: str = "MCP configuration"):
        super().__init__()
        self.reference = reference
        self.config = config
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-config-dialog"):
            yield Static(self.title_text, classes="sync-title")
            yield Static("Library name: publisher/server or server", classes="skill-hint")
            yield Input(value=self.reference, id="mcp-reference")
            yield Static("Edit the JSON below; enter API keys, env vars, or headers before saving.", classes="skill-hint")
            yield TextArea(json.dumps(self.config, indent=2, ensure_ascii=False), id="mcp-json", language="json")
            yield Static("Ctrl+S save · Esc cancel · Configuration only; packages/Docker are not downloaded", classes="skill-hint")

    def action_save(self) -> None:
        ref = self.query_one("#mcp-reference", Input).value.strip()
        try:
            config = json.loads(self.query_one("#mcp-json", TextArea).text)
            from .catalog import NAME_RE, normalize_mcp
            if len(ref.split("/")) not in (1, 2) or any(not NAME_RE.fullmatch(p) for p in ref.split("/")):
                raise ValueError("Name must be a valid server or publisher/server.")
            normalize_mcp(config)
        except (ValueError, TypeError) as exc:
            self.notify(str(exc), severity="error", timeout=6)
            return
        self.dismiss((ref, config))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SyncSkillsScreen(ModalScreen[list[int] | None]):
    BINDINGS = [Binding("escape", "cancel", "Close", priority=True),
                Binding("enter", "submit", "Synchronize", priority=True),
                Binding("space", "toggle", "Select/deselect", priority=True)]

    def __init__(self, skills: list[ExternalSkill | ExternalMcp], project: Path):
        super().__init__()
        self.skills = skills
        self.project = project
        self.selected = set(range(len(skills)))

    def compose(self) -> ComposeResult:
        with Vertical(id="sync-dialog"):
            yield Static("Synchronize skills and MCP servers into opssum?", classes="sync-title")
            yield Static(Text(f"Project: {_tilde(self.project)}"), classes="skill-hint")
            yield Static("Skills become symlinks; MCP servers are stored as configuration. Packages and Docker images are not downloaded.", classes="skill-hint")
            yield DataTable(id="sync-skills", cursor_type="row", cursor_foreground_priority="renderable")
            yield Static(id="sync-count", classes="skill-hint")
            yield Static("Space select/deselect · Enter synchronize selected · Esc close", classes="skill-hint")

    def on_mount(self) -> None:
        self.call_after_refresh(self._populate)

    def _populate(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", key="check", width=3)
        table.add_column("Item", key="name", width=24)
        width = self.query_one("#sync-dialog").content_size.width
        table.add_column("Agent / path", key="source", width=max(12, width - 38))
        first = None
        for kind, scope, label in (("skill", "global", "Global skills"),
                                    ("skill", "project", "Project skills"),
                                    ("mcp", "global", "Global MCP"),
                                    ("mcp", "project", "Project MCP")):
            indices = [i for i, skill in enumerate(self.skills)
                       if skill.scope == scope and ("skill" if isinstance(skill, ExternalSkill) else "mcp") == kind]
            table.add_row("", Text(f"{label} ({len(indices)})", style="bold #e5e5e5"),
                          "" if indices else "No unmanaged items", key=f"scope:{kind}:{scope}")
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
        self.query_one("#sync-count", Static).update(f"Selected {len(self.selected)} / {len(self.skills)} items")

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
            yield Static("Synchronizing skills…", id="sync-progress")


HELP = [
    ("i", "Skills: GitHub owner/repo · MCP: search Claude Marketplaces"),
    ("e", "edit MCP configuration in the library"),
    ("Move", "↑ ↓ ← →  select a cell (row = item, column = agent)"),
    ("space / enter", "install ↔ uninstall the item for the selected agent"),
    ("u", "update an outdated item (◐) from the library"),
    ("A / X", "install / uninstall the item for ALL agents"),
    ("m", "import an external item (◌) into the library"),
    ("d", "remove a skill/MCP from the library and recorded agents"),
    ("1 2 3  [ ]", "switch Skills / MCP / Instructions"),
    ("f", "filter to one agent (view and manage it separately)"),
    ("o", "show installed items only"),
    ("s", "switch global ↔ project scope"),
    ("P", "change the project directory"),
    ("r", "reload the library and agent configuration"),
    ("q", "quit"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark,enter", "close", "Close")]

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("Keyboard shortcuts\n\n", style=f"bold {ACCENT}")
        for k, v in HELP:
            t.append(f"{k:<14}", style="bold")
            t.append(f"{v}\n")
        t.append("\n")
        for sym, label in (("installed", "installed"), ("outdated", "differs from library"),
                           ("absent", "not installed"), ("external", "on an agent but not in the library")):
            s, c = SYMBOL[sym]
            t.append(f"{s} ", style=c)
            t.append(f"{label}   ", style=DIM)
        t.append("\n\n* after an agent name = binary not found on PATH", style=DIM)
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
        Binding("i", "remote_install", "Search/install"),
        Binding("e", "edit_mcp", "Edit MCP", show=False),
        Binding("space", "toggle", "install/uninstall", key_display="space"),
        Binding("enter", "toggle", "install/uninstall", show=False),
        Binding("u", "update", "Update", show=False),
        Binding("A", "install_all", "Install all", show=False),
        Binding("X", "uninstall_all", "Uninstall all", show=False),
        Binding("m", "adopt", "Import", show=True),
        Binding("d", "remove_skill", "Remove from library"),
        Binding("f", "filter", "agent"),
        Binding("s", "scope", "scope"),
        Binding("o", "only", "Installed only"),
        Binding("1", "tab('skill')", "Skills", show=False),
        Binding("2", "tab('mcp')", "MCP", show=False),
        Binding("3", "tab('instruction')", "Instructions", show=False),
        Binding("left_square_bracket", "prev_tab", "prev", show=False),
        Binding("right_square_bracket", "next_tab", "next", show=False),
        Binding("r", "reload", "Reload", show=False),
        Binding("P", "project_path", "project", show=False),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit"),
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

        # filter / sort
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

        # column widths
        lib_total = len(self.mgr.lib.items(self.cur_kind))
        agent_w = 12
        name_w = 22
        status_w = 14 if single else 0
        ncols = 2 + len(agents) + (1 if single else 0)
        avail = max(self.size.width, 80) - 8 - 2 * ncols - name_w - agent_w * len(agents) - status_w
        desc_w = max(14, min(52, avail))

        table.clear(columns=True)
        self.col_keys = ["name", "desc"]
        table.add_column("Name", key="name", width=name_w)
        table.add_column("Description", key="desc", width=desc_w)
        for a in agents:
            done = sum(1 for n, s in per[a.id].items()
                       if s.status in ("installed", "outdated") and self.mgr.lib.get(self.cur_kind, n))
            label = a.label + ("" if a.detected() else "*")
            if lib_total:
                label += f" {done}/{lib_total}"
            table.add_column(Text(label, justify="center"), key=a.id, width=agent_w)
            self.col_keys.append(a.id)
        if single:
            table.add_column("Status", key="status", width=status_w)
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
                Text("external — not in library", style=f"italic {DIM}")
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
        b.append("Welcome! Manage skills, MCP servers, and instructions for every agent CLI.\n", style="bold")
        b.append("  library  ", style=DIM)
        b.append(_tilde(mgr.home) + "\n")
        b.append("  scope    ", style=DIM)
        if self.cur_scope == "global":
            b.append("global", style=f"bold {ACCENT}")
            b.append("  (user configuration)\n", style=DIM)
        else:
            b.append("project", style=f"bold {ACCENT}")
            b.append(f"  {_tilde(mgr.project)}\n", style=DIM)
        b.append("  items    ", style=DIM)
        b.append(counts)
        if mgr.lib.errors:
            b.append(f"   ⚠ {len(mgr.lib.errors)} errors: {mgr.lib.errors[0]}", style=WARN)
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
        f.append("   installed only: ", style=DIM)
        f.append("on" if self.only_installed else "off", style=f"bold {ACCENT}" if self.only_installed else DIM)
        self.query_one("#filters", Static).update(f)

    def update_detail(self) -> None:
        box = self.query_one("#detail", Static)
        cur = self._current()
        if cur is None:
            d = self.mgr.lib.dir_for(self.cur_kind)
            box.update(Text(
                f"No {KIND_LABEL[self.cur_kind]} found.\n"
                f"Add files to {_tilde(d)} and press r — or run `opssum init --examples`.",
                style=DIM))
            return
        row, aid = cur
        t = Text()
        t.append(row.name, style=f"bold {ACCENT}")
        t.append(f"   {KIND_LABEL[row.kind]}", style=DIM)
        if row.item is None:
            t.append("   external — not in library", style=EXT)
        t.append("\n")
        if row.item and row.item.description:
            t.append(_clip(row.item.description, 220) + "\n")
        if row.item and row.item.path:
            t.append(f"source    {_tilde(row.item.path)}\n", style=DIM)
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
                t.append(f"         ⚠ shares a path with {', '.join(shared)} — install/uninstall affects these agents too\n",
                         style=WARN)
            err = self.mgr.errors.get((aid, self.cur_scope, row.kind))
            if err:
                t.append(f"         ✘ {err}\n", style="#e5534b")
        else:
            t.append("← → select an agent column, then press Space to install/uninstall\n", style=DIM)
        box.update(t)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if self._loaded:
            self.update_detail()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        self.action_toggle()          # Enter on a cell = install/uninstall (DataTable handles Enter first)

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
                progress.query_one("#sync-progress", Static).update(Text(f"Synchronizing {index}/{len(skills)}: {skill.name}\n{skill.path}"))
                try:
                    kind = "skill" if isinstance(skill, ExternalSkill) else "mcp"
                    result = await self._remote_io(self.mgr.adopt, skill.agent_id, skill.scope, kind, skill.name)
                except SkillConflict:
                    if not await self.push_screen_wait(ConfirmScreen(
                        f"The library already contains {skill.name} with different content.\nOverwrite it with {skill.path}?\n"
                        "Agents linked to the library version will receive the new content too."
                    )):
                        continue
                    result = await self._remote_io(self.mgr.adopt, skill.agent_id, skill.scope, "skill", skill.name, overwrite=True)
                except McpConflict:
                    if not await self.push_screen_wait(ConfirmScreen(
                        f"The library already contains MCP {skill.name} with a different configuration.\n"
                        f"Overwrite it with {skill.path}? Other agents will need their configuration updated.")):
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
        self.notify(f"Synchronized {count}/{len(skills)} items.", timeout=5)
        for error in errors:
            self.notify(error, severity="error", timeout=10)

    def action_remote_install(self) -> None:
        if self._remote_busy:
            self.notify("A remote operation is already in progress; please wait.")
            return
        self._remote_busy = True
        if self.cur_kind == "mcp":
            self.run_worker(self._remote_mcp(), name="search-mcp")
        elif self.cur_kind == "skill":
            self.run_worker(self._remote_install(), name="install-skills")
        else:
            self._remote_busy = False
            self.notify("Select the Skills or MCP tab to search and install.", severity="warning")

    async def _remote_mcp(self) -> None:
        try:
            query = await self.push_screen_wait(PromptScreen("Search MCP on Claude Marketplaces"))
            if not query:
                return
            self.notify(f"Searching MCP: {query}…", timeout=5)
            listings = await self._remote_io(mcp_marketplace.search, query)
            if not listings:
                self.notify("No matching MCP servers found.", severity="warning")
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
            edited = await self.push_screen_wait(McpConfigScreen(reference, config, f"MCP configuration · {listing.name}"))
            if edited is None:
                return
            reference, config = edited
            overwrite = self.mgr.lib.get("mcp", reference) is not None
            if overwrite and not await self.push_screen_wait(ConfirmScreen(
                f"Overwrite MCP {reference} in the library?\nAgents managing this MCP will need their configuration updated.")):
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
            self.notify("Select an MCP in the library to edit its configuration.", severity="warning")
            return
        item = cur[0].item
        async def edit() -> None:
            config = {"description": item.description, **item.spec}
            edited = await self.push_screen_wait(McpConfigScreen(item.name, config, f"Edit MCP · {item.name}"))
            if edited is None:
                return
            reference, body = edited
            if reference != item.name:
                self.notify("Renaming is not supported while editing; install a new item for a different name.", severity="warning")
                return
            self._finish(self.mgr.save_mcp(reference, body, overwrite=True))
        self.run_worker(edit(), name="edit-mcp")

    async def _remote_install(self) -> None:
        try:
            repository = await self.push_screen_wait(PromptScreen("Install skills — GitHub owner/repo (for example anthropics/skills)"))
            if not repository:
                return
            self.notify(f"Downloading {repository}…", timeout=5)
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
                        f"Overwrite skill [{skill.reference}] in the library?\nExisting edits will be lost; linked agents will receive the new version."
                    )):
                        continue
                    await self._remote_io(remote.install, self.mgr.home, skill, overwrite=overwrite)
                    count += 1
                self.notify(f"Installed {count}/{len(selected)} skills into the library.")
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
            self.notify("Use ← → to select an agent column, then press Space.", severity="warning")
            return
        st = row.states.get(aid)
        if st is None:
            return
        if st.status == "absent":
            conflict = (self.mgr.skill_conflict(aid, self.cur_scope, row.name) if row.kind == "skill" else
                        self.mgr.mcp_conflict(aid, self.cur_scope, row.name) if row.kind == "mcp" else None)
            if conflict:
                self._confirm(f"{aid} is using {conflict}.\nReplace it with {row.name}?", lambda:
                              self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name, replace=True)))
            else:
                self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name))
            return
        if st.status == "external" and not st.removable:
            self.notify(st.detail or "This item cannot be uninstalled from here.", severity="warning")
            return

        def do() -> None:
            self._finish(self.mgr.uninstall(aid, self.cur_scope, row.kind, row.name))

        if st.managed:
            do()
        else:
            self._confirm(f"Uninstall [{row.name}] from {aid}?\nThis item was not created by opssum "
                          f"(the config file will be backed up before editing).", do)

    def action_update(self) -> None:
        cur = self._current()
        if cur is None or cur[1] is None or cur[0].item is None:
            return
        row, aid = cur
        st = row.states.get(aid)
        if st is None or st.status != "outdated":
            self.notify("This item does not need an update.", severity="information")
            return

        def do() -> None:
            self._finish(self.mgr.install(aid, self.cur_scope, row.kind, row.name,
                                          replace=row.kind == "mcp" and not st.managed))

        if st.managed:
            do()
        else:
            self._confirm(f"Overwrite [{row.name}] for {aid} with the library version?\n"
                          f"Manual edits will be lost (a backup will be created).", do)

    def action_install_all(self) -> None:
        cur = self._current()
        if cur is None or cur[0].item is None:
            self.notify("Only items in the library can be installed.", severity="warning")
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
            self.notify(f"Install {row.name}: {ok}/{len(results)} agents succeeded.",
                        severity="information" if ok == len(results) else "warning")
            self.rebuild()

        if conflicts:
            self._confirm(f"Replace the following versions with {row.name}?\n" + "\n".join(conflicts), do)
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
            self.notify("No agent has this item installed.", severity="information")
            return

        def do() -> None:
            res = [self.mgr.uninstall(a, self.cur_scope, row.kind, row.name) for a in targets]
            ok = sum(r.ok for r in res)
            self.notify(f"Uninstall {row.name}: {ok}/{len(res)} agents succeeded.",
                        severity="information" if ok == len(res) else "warning")
            self.rebuild()

        self._confirm(f"Uninstall [{row.name}] from: {', '.join(targets)}?", do)

    def action_adopt(self) -> None:
        cur = self._current()
        if cur is None or cur[1] is None:
            return
        row, aid = cur
        st = row.states.get(aid)
        if row.item is not None or st is None or st.status != "external":
            self.notify("Only external items (◌) that are not in the library can be imported.", severity="warning")
            return
        if row.kind == "skill":
            candidate = ExternalSkill(row.name, aid, self.cur_scope,
                                      self.mgr.agents[aid].resolve("skills", self.cur_scope, self.mgr.project) / row.name)
            self._confirm(f"Synchronize {row.name} into the library?\nThe original directory will be moved; if it is a symlink, the real source is preserved.", lambda:
                          self.run_worker(self._sync_candidates([candidate]), name="import-skill"))
        else:
            self._confirm(f"Synchronize MCP {row.name} into the library?\nThe agent's original configuration will be managed by opssum.",
                          lambda: self.run_worker(self._sync_candidates([
                              ExternalMcp(row.name, aid, self.cur_scope,
                                          self.mgr.agents[aid].resolve("mcp", self.cur_scope, self.mgr.project))
                          ]), name="import-mcp"))

    def action_remove_skill(self) -> None:
        cur = self._current()
        if cur is None or cur[0].kind not in ("skill", "mcp") or cur[0].item is None:
            self.notify("Select a skill or MCP server in the library to remove it.", severity="warning")
            return
        row = cur[0]
        try:
            paths = self.mgr.skill_dependents(row.name) if row.kind == "skill" else self.mgr.mcp_dependents(row.name)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        message = f"Remove {row.name} from the library and uninstall it from {len(paths)} agent locations?\n"
        message += "\n".join(_tilde(p if isinstance(p, Path) else p[0]) for p in paths)
        message += "\nA recovery copy will be saved in library/.trash."
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
            self.notify(f"Project scope: {_tilde(self.mgr.project)} (press P to change the directory)", timeout=4)
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
        self.notify("Reloaded the library and agent configuration.", timeout=2)

    def action_project_path(self) -> None:
        def cb(value: str | None) -> None:
            if not value:
                return
            p = Path(value).expanduser()
            if not p.is_dir():
                self.notify(f"{value} is not a directory.", severity="error")
                return
            self.mgr.project = p.resolve()
            self.mgr.remember_skill_locations()
            self.mgr.remember_mcp_locations()
            self.rebuild()
        self.push_screen(PromptScreen("Project directory", str(self.mgr.project)), cb)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def run(mgr: Manager) -> None:
    OpssumApp(mgr).run()
