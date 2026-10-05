"""Tạo thư mục .agent-knowledge mới (+ dữ liệu mẫu để thử TUI)."""
from __future__ import annotations

from pathlib import Path

README = """# .agent-knowledge

Nguồn sự thật (single source of truth) cho skills, MCP và instructions của mọi agent CLI.

- `skills/<name>/SKILL.md`     Agent Skills (frontmatter `name`, `description`)
- `mcp/<name>.json`            1 MCP server / file
- `instructions/<name>.md`     Mảnh AGENTS.md / CLAUDE.md, được chèn vào file memory của agent
- `agents.json`                (tuỳ chọn) override đường dẫn của từng agent

Chạy `agent-knowledge` để mở TUI.
"""

AGENTS_JSON = """{
  "_comment": "Override đường dẫn agent (xoá dấu _ để bật). Xem README mục 'Tuỳ biến agent'.",
  "_disabled": [],
  "_agents": {}
}
"""

EXAMPLES: dict[str, str] = {
    "skills/commit-helper/SKILL.md": """---
name: commit-helper
description: Viết commit message theo Conventional Commits từ git diff. Dùng khi người dùng nhờ commit hoặc soạn message.
---

# Commit helper

1. Chạy `git diff --staged` để xem thay đổi.
2. Chọn type: feat, fix, refactor, docs, chore, test.
3. Viết dòng đầu ≤ 72 ký tự, dạng `type(scope): mô tả`.
4. Nếu thay đổi lớn, thêm body giải thích *vì sao*.
""",
    "skills/vue2-conventions/SKILL.md": """---
name: vue2-conventions
description: Quy ước viết component Vue 2 + Element UI trong monorepo (đặt tên, cấu trúc thư mục, i18n).
---

# Vue 2 conventions

- Component PascalCase, file `index.vue` trong thư mục cùng tên.
- Dùng Element UI cho form/table; không tự viết lại input cơ bản.
- Text hiển thị phải qua i18n, không hard-code.
""",
    "skills/code-review/SKILL.md": """---
name: code-review
description: Review code theo checklist (correctness, security, performance, readability) và trả về danh sách vấn đề theo mức độ.
---

# Code review

Đọc diff, nhóm nhận xét theo: **blocker**, **nên sửa**, **gợi ý**. Mỗi nhận xét nêu file:dòng và cách sửa.
""",
    "mcp/filesystem.json": """{
  "description": "Truy cập file trong thư mục hiện tại (server tham chiếu của MCP).",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
}
""",
    "mcp/context7.json": """{
  "description": "Tra docs thư viện mới nhất qua Context7.",
  "command": "npx",
  "args": ["-y", "@upstash/context7-mcp"]
}
""",
    "mcp/deepwiki.json": """{
  "description": "Hỏi đáp về repo GitHub qua DeepWiki (remote, không cần key).",
  "url": "https://mcp.deepwiki.com/mcp"
}
""",
    "instructions/coding-style.md": """---
description: Quy ước code chung cho mọi project
---

## Coding style

- Ưu tiên thay đổi nhỏ, dễ review; không refactor ngoài phạm vi yêu cầu.
- Tên biến/hàm bằng tiếng Anh; comment giải thích *vì sao* chứ không *cái gì*.
""",
    "instructions/git-workflow.md": """---
description: Quy trình git (Gitea): branch, commit, PR
---

## Git workflow

- Branch: `feature/<ticket>-<mô-tả-ngắn>`, `fix/<ticket>-...`.
- Commit theo Conventional Commits; không push thẳng vào `main`.
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
