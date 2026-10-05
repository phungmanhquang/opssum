"""Create a new .opssum directory with optional sample data."""
from __future__ import annotations

from pathlib import Path

README = """# .opssum

Single source of truth for skills, MCP servers, and instructions used by every agent CLI.

- `skills/<name>/SKILL.md`     Agent Skills (frontmatter `name`, `description`)
- `mcp/<name>.json`            1 MCP server / file
- `instructions/<name>.md`     AGENTS.md / CLAUDE.md fragment inserted into an agent memory file
- `agents.json`                optional per-agent path overrides

Run `opssum` to open the TUI.
"""

AGENTS_JSON = """{
  "_comment": "Override agent paths (remove the leading _ to enable). See the README customization section.",
  "_disabled": [],
  "_agents": {}
}
"""

EXAMPLES: dict[str, str] = {
    "skills/commit-helper/SKILL.md": """---
name: commit-helper
description: Write a Conventional Commit message from git diff when a user asks for a commit or message.
---

# Commit helper

1. Run `git diff --staged` to review the changes.
2. Choose a type: feat, fix, refactor, docs, chore, or test.
3. Write a first line of ≤ 72 characters in the form `type(scope): summary`.
4. For a large change, add a body explaining *why*.
""",
    "skills/vue2-conventions/SKILL.md": """---
name: vue2-conventions
description: Conventions for Vue 2 + Element UI components in a monorepo (naming, structure, and i18n).
---

# Vue 2 conventions

- Use PascalCase for components and keep `index.vue` in the component directory.
- Use Element UI for forms/tables; do not rewrite basic inputs.
- Route displayed text through i18n; do not hard-code it.
""",
    "skills/code-review/SKILL.md": """---
name: code-review
description: Review code for correctness, security, performance, and readability, grouping findings by severity.
---

# Code review

Read the diff and group findings as **blocker**, **should fix**, or **suggestion**. Include file:line and a fix for each finding.
""",
    "mcp/filesystem.json": """{
  "description": "Access files in the current directory (reference MCP server).",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
}
""",
    "mcp/context7.json": """{
  "description": "Look up the latest library documentation through Context7.",
  "command": "npx",
  "args": ["-y", "@upstash/context7-mcp"]
}
""",
    "mcp/deepwiki.json": """{
  "description": "Ask questions about GitHub repositories through DeepWiki (remote; no key required).",
  "url": "https://mcp.deepwiki.com/mcp"
}
""",
    "instructions/coding-style.md": """---
description: General coding conventions for every project
---

## Coding style

- Prefer small, reviewable changes; do not refactor outside the requested scope.
- Use English for variable and function names; comments should explain *why*, not *what*.
""",
    "instructions/git-workflow.md": """---
description: Git workflow (Gitea): branches, commits, and pull requests
---

## Git workflow

- Branches: `feature/<ticket>-<short-description>`, `fix/<ticket>-...`.
- Use Conventional Commits; do not push directly to `main`.
""",
}


def init_library(home: Path, examples: bool = False) -> list[str]:
    created: list[str] = []
    home.mkdir(parents=True, exist_ok=True)
    for sub in ("skills", "mcp", "instructions"):
        (home / sub).mkdir(exist_ok=True)
    files = {"README.md": README, "agents.json": AGENTS_JSON}
    if examples:
        files.update(EXAMPLES)
    for rel, content in files.items():
        p = home / rel
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        created.append(rel)
    return created
