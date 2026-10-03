"""Registry các agent CLI và vị trí file config của từng agent.

Đường dẫn dưới đây dựa trên docs/tài liệu cộng đồng tại thời điểm viết (10/2026).
Agent CLI đổi layout khá thường xuyên -> mọi đường dẫn đều override được qua
`<library>/agents.json` (xem README).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, fields, replace
from pathlib import Path


@dataclass
class AgentSpec:
    id: str
    label: str
    root: str                         # thư mục config global mặc định
    binary: str = ""
    root_env: str = ""                # biến môi trường override `root`
    skills_global: str = "{root}/skills"
    skills_project: str = ""
    instr_global: str = "{root}/AGENTS.md"
    instr_project: str = "AGENTS.md"
    mcp_global: str = ""
    mcp_project: str = ""
    mcp_format: str = "json"          # json | toml
    mcp_style: str = "generic"        # claude | generic | agy | codex
    notes: str = ""

    # ---------------------------------------------------------------- paths
    def root_dir(self) -> Path:
        env = os.environ.get(self.root_env) if self.root_env else None
        return Path(env or self.root).expanduser()

    def resolve(self, what: str, scope: str, project: Path) -> Path | None:
        """what: skills | instr | mcp ; scope: global | project."""
        if what == "mcp" and scope == "global" and self.id == "claude":
            cfg = os.environ.get("CLAUDE_CONFIG_DIR")
            if cfg:
                return Path(cfg).expanduser() / ".claude.json"
        raw = getattr(self, f"{what}_{scope}", "")
        if not raw:
            return None
        raw = raw.replace("{root}", str(self.root_dir()))
        p = Path(raw).expanduser()
        if scope == "project" and not p.is_absolute():
            p = Path(project) / p
        return p

    def detected(self) -> bool:
        return shutil.which(self.binary or self.id) is not None


DEFAULT_AGENTS: list[AgentSpec] = [
    AgentSpec(
        id="claude", label="claude", binary="claude",
        root="~/.claude", root_env="CLAUDE_CONFIG_DIR",
        skills_global="{root}/skills", skills_project=".claude/skills",
        instr_global="{root}/CLAUDE.md", instr_project="CLAUDE.md",
        mcp_global="~/.claude.json", mcp_project=".mcp.json",
        mcp_format="json", mcp_style="claude",
        notes="MCP global nằm trong ~/.claude.json (user scope)",
    ),
    AgentSpec(
        id="codex", label="codex", binary="codex",
        root="~/.codex", root_env="CODEX_HOME",
        skills_global="{root}/skills", skills_project=".agents/skills",
        instr_global="{root}/AGENTS.md", instr_project="AGENTS.md",
        mcp_global="{root}/config.toml", mcp_project=".codex/config.toml",
        mcp_format="toml", mcp_style="codex",
        notes="Project skills ở .agents/skills (dùng chung với agent khác); MCP project chỉ chạy khi project được trust",
    ),
    AgentSpec(
        id="pi", label="pi", binary="pi",
        root="~/.pi/agent", root_env="PI_CODING_AGENT_DIR",
        skills_global="{root}/skills", skills_project=".pi/skills",
        instr_global="{root}/AGENTS.md", instr_project="AGENTS.md",
        mcp_global="{root}/mcp.json", mcp_project=".pi/mcp.json",
        mcp_format="json", mcp_style="generic",
        notes="Pi không có MCP native: cần extension pi-mcp-adapter để đọc mcp.json",
    ),
    AgentSpec(
        id="omp", label="omp", binary="omp",
        root="~/.omp/agent",
        skills_global="{root}/skills", skills_project=".omp/skills",
        instr_global="{root}/AGENTS.md", instr_project="AGENTS.md",
        mcp_global="{root}/mcp.json", mcp_project=".omp/mcp.json",
        mcp_format="json", mcp_style="generic",
        notes="oh-my-pi",
    ),
    AgentSpec(
        id="agy", label="agy", binary="agy",
        root="~/.gemini/antigravity-cli",
        skills_global="{root}/skills", skills_project=".agents/skills",
        instr_global="~/.gemini/GEMINI.md", instr_project="AGENTS.md",
        mcp_global="{root}/mcp_config.json", mcp_project=".agents/mcp_config.json",
        mcp_format="json", mcp_style="agy",
        notes="Antigravity CLI; remote MCP dùng field serverUrl",
    ),
]


def load_agents(home: Path) -> dict[str, AgentSpec]:
    """Registry mặc định + override từ `<home>/agents.json`.

    {
      "disabled": ["agy"],
      "agents": {
        "claude": {"skills_global": "~/my/skills"},
        "cursor": {"label": "cursor", "root": "~/.cursor", "skills_global": "{root}/skills",
                   "mcp_global": "{root}/mcp.json", "mcp_format": "json"}
      }
    }
    """
    reg = {a.id: replace(a) for a in DEFAULT_AGENTS}
    cfg = Path(home) / "agents.json"
    if not cfg.is_file():
        return reg
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return reg
    known = {f.name for f in fields(AgentSpec)} - {"id"}
    for aid, over in (data.get("agents") or {}).items():
        over = {k: v for k, v in over.items() if k in known}
        if aid in reg:
            reg[aid] = replace(reg[aid], **over)
        elif "root" in over:
            over.setdefault("label", aid)
            reg[aid] = AgentSpec(id=aid, **over)
    for aid in data.get("disabled") or []:
        reg.pop(aid, None)
    return reg
