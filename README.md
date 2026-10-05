# ✻ opssum

TUI quản lý **skills**, **MCP servers** và **instructions (AGENT file)** cho nhiều agent CLI
(`claude`, `codex`, `pi`, `omp`, `agy`, …) từ **một thư mục duy nhất** `~/.opssum`.

```
╭─ ✻ opssum  v0.1.0 ──────────────────────────────────────────────╮
│ ✻ Xin chào! Quản lý skills, MCP và instructions cho mọi agent CLI.       │
│   library  ~/.opssum                                            │
│   scope    global  (config người dùng)                                   │
│   items    3 skills · 3 mcp · 2 instructions                             │
╰──────────────────────────────────────────────────────────────────────────╯
 1 Skills (3)   2 MCP (3)   3 Instructions (2)
 agent [all] claude codex pi omp agy      chỉ đã cài: tắt

  Tên                Mô tả                  claude 3/3  codex 2/3  pi 1/3  omp 0/3  agy 0/3
  code-review        Review code theo ...       ●          ●        ○        ○        ○
  commit-helper      Viết commit message...     ●          ●        ●        ○        ○
  vue2-conventions   Quy ước Vue 2 + ...        ●          ○        ○        ○        ○
  my-own             external — chưa có...      ○          ◌        ○        ○        ○
```

- **Một nguồn sự thật**: thêm skill/MCP/instruction vào `~/.opssum` một lần, cài cho agent nào tuỳ bạn.
- **Ma trận item × agent**: nhìn một lần biết agent nào đang có gì (`claude 3/5`, `codex 2/5`…), bấm `space` để cài/gỡ.
- **Global hoặc project**: bấm `s` để đổi scope.
- **Xem riêng từng agent**: bấm `f` để chỉ hiện 1 agent, đúng như kịch bản "hôm sau vào thấy codex có 2 skill → gỡ bớt / cài thêm".
- **An toàn**: không ghi đè file bạn tự viết, có backup trước khi sửa config, chỉ gỡ những gì chính nó tạo.

> **Lưu ý:** logic cài/gỡ được kiểm thử tự động trong môi trường giả lập `HOME`,
> nhưng **chưa được kiểm chứng với binary thật** của từng agent trên máy bạn. Đường dẫn config của
> các agent đổi khá thường xuyên — xem mục [Đường dẫn từng agent](#đường-dẫn-từng-agent) và hãy chạy
> `opssum agents` để kiểm tra trước khi cài thật. Mọi đường dẫn đều override được.

---

## 1. Installation (Cài đặt)

Người dùng bản **standalone binary không cần cài Python**. Terminal cần hỗ trợ màu 256/truecolor và Unicode (Windows Terminal, iTerm2, Ghostty, Kitty, GNOME Terminal… đều ổn).

### Cài nhanh Linux/macOS

```sh
curl -fsSL https://github.com/OWNER/REPO/releases/latest/download/install.sh | sh
```

Thay `OWNER/REPO` bằng repo GitHub công khai trước khi dùng hoặc phát hành tài liệu.
Người phát hành cũng cần thay placeholder `OWNER/REPO` trong `install.sh` và
`install.ps1`. Workflow upload hai installer cùng binary lên mỗi GitHub Release;
lệnh này hoạt động sau khi có release đầu tiên.
Installer tự nhận Linux/macOS và x64/ARM64, tải binary mới nhất cùng SHA256 từ
GitHub Releases, xác minh rồi cài vào `~/.local/bin/opssum`.
Nếu `~/.local/bin` chưa có trong PATH, installer sẽ in dòng cần thêm vào shell profile.
Mở terminal mới sau khi cập nhật PATH.

```sh
opssum init --examples   # lần đầu, nếu muốn dữ liệu mẫu
opssum                   # mở TUI
```

Để **cập nhật**, chạy lại đúng lệnh `curl ... | sh` ở trên. Installer chỉ thay binary
sau khi tải, kiểm SHA256 và thử chạy `--version` thành công; không xóa library
`~/.opssum`.

Để **gỡ cài đặt** trên Linux/macOS:

```sh
rm "$HOME/.local/bin/opssum"
```

Không xóa `~/.opssum` nếu muốn giữ skills/MCP đã lưu. Có thể bỏ dòng PATH
khỏi shell profile nếu `~/.local/bin` không còn dùng cho công cụ nào khác.

### Windows PowerShell

```powershell
irm https://github.com/OWNER/REPO/releases/latest/download/install.ps1 | iex
```

Thay `OWNER/REPO` như trên. Chạy lại lệnh để cập nhật. Installer hỗ trợ Windows
x64, kiểm SHA256, lưu `opssum.exe` vào `$HOME\.local\bin`, tự thêm thư mục này
vào User PATH; mở PowerShell mới rồi chạy `opssum`. Để gỡ, xóa file này;
library vẫn được giữ:

```powershell
Remove-Item "$HOME\.local\bin\opssum.exe"
```

### Binary từ GitHub Releases (không cần Python)

Chọn file đúng OS/CPU trong GitHub Releases: `opssum-linux-x64`,
`opssum-linux-arm64`, `opssum-macos-x64`,
`opssum-macos-arm64` hoặc `opssum-windows-x64.exe`.
Tải kèm file `.sha256` tương ứng để kiểm tra checksum. Trên Linux/macOS,
chạy `chmod +x opssum-<os>-<arch>` sau khi tải file thô, rồi chạy trực tiếp.
Windows chạy file `.exe`. Các binary đã chứa Python runtime và dependencies;
chúng vẫn cần terminal và những agent CLI/MCP package mà bạn muốn sử dụng.

### Cài từ source (cần Python)

```bash
# pipx (khuyến nghị)
pipx install .

# hoặc uv
uv tool install .

# hoặc pip trong venv
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install .
```

### Chạy thử không cần cài

```bash
pip install textual
PYTHONPATH=src python -m opssum
```

Sau khi cài, chạy lệnh `opssum`.

### Phát hành phiên bản mới

Máy phát hành cần `git`, `python3`, quyền push lên GitHub và remote `origin`
trỏ tới GitHub. Commit toàn bộ thay đổi code/workflow trước, bảo đảm working tree sạch,
sau đó chạy:

```bash
./release.sh                    # chọn patch / minor / major khi được hỏi
./release.sh patch              # hoặc chọn trực tiếp
```

Script tăng `[project].version` trong `pyproject.toml`, commit, tạo annotated tag
`vX.Y.Z`, rồi push commit + tag theo một lệnh atomic. Nếu push lỗi, chạy lại
`./release.sh` để tiếp tục tag/commit đã tạo; chạy lại sau khi push thành công
không tạo release trùng. Tag kích hoạt [workflow release](.github/workflows/release.yml):
chạy test, build PyInstaller one-file trên 5 runner native, smoke-test từng binary,
tạo SHA256 và chỉ publish GitHub Release khi cả 5 bản thành công.
Workflow dùng `GITHUB_TOKEN` với quyền `contents: write`, không lưu token trong source.
Installer tải bản standalone, còn `pipx`/`uv` ở trên là lựa chọn cài từ source.

## 2. Bắt đầu nhanh

```bash
opssum init --examples  # tạo ~/.opssum kèm 3 skill, 3 MCP, 2 instruction mẫu
opssum                  # mở TUI
```

Muốn dùng thư mục khác: `export OPSSUM_HOME=~/dotfiles/opssum` hoặc `opssum --home <dir>`.
Có thể để thư mục này trong git (Gitea) để cả team dùng chung.

## 3. Cấu trúc thư mục `.opssum`

```
~/.opssum/
├── skills/
│   ├── anthropics/<skill-name>/SKILL.md  # nhóm theo GitHub owner
│   └── <skill-name>/SKILL.md            # skill cũ / nhập bằng m: other
├── mcp/
│   └── <server-name>.json             # 1 server / file
├── instructions/
│   └── <name>.md                      # mảnh AGENTS.md / CLAUDE.md
└── agents.json                        # (tuỳ chọn) override đường dẫn agent
```

### Skills

Thư mục chứa `SKILL.md` theo chuẩn Agent Skills (frontmatter `name`, `description`). Tên thư mục là tên skill.

```markdown
---
name: commit-helper
description: Viết commit message theo Conventional Commits từ git diff.
---
# Hướng dẫn…
```

### MCP servers

Mỗi file là một server. File `mcp/<server>.json` thuộc nhóm **other**;
`mcp/<publisher>/<server>.json` thuộc nhóm nhà phát hành và vẫn cài vào agent dưới tên `<server>`. Hai kiểu:

```jsonc
// mcp/context7.json — stdio
{
  "description": "Tra docs thư viện qua Context7",
  "command": "npx",
  "args": ["-y", "@upstash/context7-mcp"],
  "env": { "SOME_VAR": "value" }
}
```

```jsonc
// mcp/deepwiki.json — remote (streamable HTTP); "transport": "sse" nếu server dùng SSE
{
  "description": "Hỏi đáp repo GitHub",
  "url": "https://mcp.deepwiki.com/mcp",
  "headers": { "Authorization": "Bearer …" }
}
```

Cũng chấp nhận định dạng copy-paste từ README của MCP server (`{"mcpServers": {"tên": {...}}}`) — mỗi key thành một item.
Công cụ tự chuyển sang đúng định dạng của từng agent (JSON `mcpServers`, TOML `[mcp_servers.x]` của Codex, `serverUrl` của agy…).
Trong tab MCP, nhấn `i` để tìm trên [Claude Marketplaces](https://claudemarketplaces.com/mcp),
chọn kết quả bằng Enter, xem mô tả đầy đủ bằng `i`, rồi xem/sửa JSON cấu hình trong modal.
Nhóm nhà phát hành lấy owner GitHub của `sourceRepo` khi marketplace cung cấp,
nếu không thì dùng phần đầu slug trên marketplace; có thể sửa tên trong modal.
Điền env/API key/header cần thiết và nhấn Ctrl+S để lưu vào library. Nhấn `e` trên một
MCP đã lưu để chỉnh lại cấu hình; agent đang cài sẽ hiện “cần cập nhật”, nhấn `u`
để áp dụng. Công cụ **chỉ lưu cấu hình**, không chạy lệnh từ marketplace và không tải
package hay Docker image; người dùng tự cài các phụ thuộc đó. Nếu trang không cung cấp
cấu hình đọc được, modal cho phép nhập `command` + `args` hoặc `url` thủ công.
API key nhập trực tiếp được lưu dạng plaintext trong library và config của agent;
hãy bảo vệ các file này và tránh commit secrets vào Git.

### Instructions (AGENT file)

File `.md` thường (frontmatter `description:` tuỳ chọn). Khi cài, nội dung được **chèn thành một block có marker**
vào file memory của agent (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`):

```markdown
<!-- opssum:begin coding-style -->
…nội dung…
<!-- opssum:end coding-style -->
```

Phần còn lại của file (do bạn tự viết) **không bị đụng tới**; gỡ chỉ xoá đúng block đó.

## 4. Dùng TUI

### Bố cục

| Vùng | Ý nghĩa |
|---|---|
| Banner | library đang dùng, scope (global/project), số lượng item, cảnh báo lỗi library |
| Tab `1 2 3` | Skills / MCP / Instructions |
| Dòng `agent` | Bộ lọc agent (`f`) + bật/tắt "chỉ đã cài" (`o`) |
| Bảng | Hàng = item, cột = agent. Header hiện `đã cài/tổng` của agent đó (vd `claude 3/5`) |
| Panel chi tiết | Mô tả, nguồn, đường dẫn đích, cảnh báo của ô đang chọn |

### Ký hiệu

| | Ý nghĩa |
|---|---|
| `●` | đã cài (khớp library) |
| `◐` | đã cài nhưng **lệch** library (nội dung đổi / symlink hỏng) → bấm `u` để cập nhật |
| `○` | chưa cài |
| `◌` | **external**: có ở agent nhưng chưa có trong library (tự cài tay) |
| `*` sau tên agent | chưa thấy binary trong `PATH` (vẫn quản lý được) |

### Phím tắt

| Phím | Tác dụng |
|---|---|
| `↑ ↓ ← →` | chọn ô |
| `space` / `enter` | cài ↔ gỡ item cho agent ở cột đang chọn |
| `u` | cập nhật item lệch (`◐`) theo library |
| `A` / `X` | cài / gỡ item cho **tất cả** agent |
| `m` | nhập item external (`◌`) vào library (skill, MCP) |
| `i` | Tab Skills: nhập GitHub `owner/repo`; tab MCP: tìm trên Claude Marketplaces |
| `e` | sửa JSON cấu hình của MCP đã có trong library |
| `d` | xoá skill/MCP khỏi library, gỡ ở global và các project đã ghi nhận (có xác nhận) |
| `1` `2` `3` hoặc `[` `]` | đổi tab |
| `f` | lọc theo 1 agent (xoay vòng: all → claude → codex → …) |
| `o` | chỉ hiện item đã cài |
| `s` | đổi scope **global ↔ project** |
| `P` | đổi thư mục project |
| `r` | tải lại library + config agent |
| `?` | trợ giúp · `q` thoát |

### Ví dụ: 5 skill trong library, cài 3 cho claude và 2 cho codex

1. Mở `opssum`, tab `1 Skills`. Header hiện `claude 0/5 · codex 0/5`.
2. Di chuyển tới cột `claude`, đứng ở 3 skill, bấm `space` mỗi hàng. Sang cột `codex`, làm tương tự với 2 skill.
3. Hôm sau mở lại: header vẫn hiện `claude 3/5 · codex 2/5` (trạng thái đọc trực tiếp từ đĩa, không có file state riêng).
4. Bấm `f` cho tới khi chọn `codex` → chỉ còn cột codex + cột "Trạng thái", item đã cài xếp lên đầu. `space` để gỡ hoặc cài thêm skill/MCP khác.

### Scope global vs project

- **global**: config người dùng (`~/.claude/…`, `~/.codex/…`, …) — áp dụng mọi project.
- **project**: file trong thư mục project (`.claude/skills`, `.mcp.json`, `AGENTS.md`, …). Mặc định là thư mục bạn đứng khi chạy `opssum`;
  dùng `--project <dir>` hoặc phím `P` để đổi.

## 5. Cách mỗi loại được cài (và độ an toàn)

| Loại | Cơ chế | Gỡ |
|---|---|---|
| **Skill** | `symlink` từ thư mục agent → skill trong library (sửa 1 chỗ, mọi agent cùng thấy). Nếu OS không cho tạo symlink → báo lỗi và giữ bản cũ. Bản copy do tool cũ tạo được chuyển thành symlink khi cài lại | Chỉ gỡ symlink/copy do chính tool tạo. Thư mục skill bạn tự cài → hiện `◌`, **không** xoá |
| **Instruction** | Chèn block có marker vào file memory | Xoá đúng block |
| **MCP (JSON)** | Sửa key `mcpServers.<name>`, giữ nguyên mọi key khác; giữ quyền file (vd `~/.claude.json` mode 600); **backup** `<file>.opssum.bak` trước mỗi lần ghi; file JSON hỏng → từ chối ghi | Xoá key `<name>` do ứng dụng quản lý; server tự khai báo cần đồng bộ bằng `m` trước |
| **MCP (Codex TOML)** | Chèn block `# >>> opssum:<name> >>>` vào `config.toml`, không parse/ghi lại cả file | Xoá block. Server bạn khai báo tay trong `config.toml` chỉ đọc, không gỡ từ TUI |

Nguyên tắc chung: **trùng tên với thứ không do tool quản lý → hỏi xác nhận nếu có thể thay an toàn**, không ghi đè ngầm.

## 6. Đường dẫn từng agent

Tổng hợp từ docs và tài liệu cộng đồng tại thời điểm viết (10/2026). Kiểm tra bằng `opssum agents`.

| Agent | Skills (global / project) | Instructions (global / project) | MCP (global / project) |
|---|---|---|---|
| **claude** | `~/.claude/skills` / `.claude/skills` | `~/.claude/CLAUDE.md` / `CLAUDE.md` | `~/.claude.json` (`mcpServers`) / `.mcp.json` |
| **codex** | `~/.codex/skills` / `.agents/skills` | `~/.codex/AGENTS.md` / `AGENTS.md` | `~/.codex/config.toml` / `.codex/config.toml` |
| **pi** | `~/.pi/agent/skills` / `.pi/skills` | `~/.pi/agent/AGENTS.md` / `AGENTS.md` | `~/.pi/agent/mcp.json` / `.pi/mcp.json` ⚠ |
| **omp** | `~/.omp/agent/skills` / `.omp/skills` | `~/.omp/agent/AGENTS.md` / `AGENTS.md` | `~/.omp/agent/mcp.json` / `.omp/mcp.json` |
| **agy** | `~/.gemini/antigravity-cli/skills` / `.agents/skills` | `~/.gemini/GEMINI.md` / `AGENTS.md` | `~/.gemini/antigravity-cli/mcp_config.json` / `.agents/mcp_config.json` |

Biến môi trường được tôn trọng: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`.

Cần biết:

- ⚠ **Pi không có MCP native** — cần extension `pi-mcp-adapter` để đọc `mcp.json`. Tool vẫn ghi file, nhưng pi chỉ dùng được khi có extension.
- **Đường dẫn dùng chung giữa nhiều agent** (ở scope project): `AGENTS.md` được codex/pi/omp/agy cùng đọc, và `.agents/skills`
  được codex + agy dùng chung. Cài/gỡ ở agent này sẽ **ảnh hưởng cả agent kia**; panel chi tiết hiển thị cảnh báo `⚠ dùng chung đường dẫn với …`.
- **Codex** chỉ hỗ trợ MCP stdio + streamable HTTP (không SSE) → MCP kiểu `"transport": "sse"` sẽ bị từ chối cho codex.
- **Skills của codex** mặc định đặt ở `~/.codex/skills` để tách khỏi agent khác. Codex cũng đọc `~/.agents/skills` (thư mục này
  pi/omp/agy cũng đọc) — muốn dùng chung thì override trong `agents.json`.
- Định dạng MCP của **omp** và nhánh remote của **pi** được suy ra từ tài liệu bên thứ ba, chưa kiểm chứng chính thức.
- Nên **đóng agent đang chạy** khi sửa `~/.claude.json` (agent có thể ghi đè file khi thoát).

## 7. Tuỳ biến agent — `agents.json`

Đặt trong `~/.opssum/agents.json`: sửa đường dẫn agent có sẵn, tắt agent, hoặc thêm agent mới.

```jsonc
{
  "disabled": ["agy"],                         // ẩn agent không dùng
  "agents": {
    // override 1 vài đường dẫn của agent có sẵn
    "codex": { "skills_global": "~/.agents/skills" },

    // thêm agent mới (cần tối thiểu "root"); {root} sẽ được thay bằng root
    "cursor": {
      "label": "cursor",
      "root": "~/.cursor",
      "binary": "cursor-agent",
      "skills_global": "{root}/skills",
      "skills_project": ".cursor/skills",
      "instr_global": "{root}/AGENTS.md",
      "mcp_global": "{root}/mcp.json",
      "mcp_project": ".cursor/mcp.json",
      "mcp_format": "json",                    // json | toml
      "mcp_style": "generic"                   // claude | generic | agy | codex
    }
  }
}
```

Các field: `label, root, root_env, binary, skills_global, skills_project, instr_global, instr_project, mcp_global, mcp_project, mcp_format, mcp_style, notes`.
Đường dẫn project là đường dẫn tương đối so với thư mục project.

## 8. Dùng bằng dòng lệnh (script / CI)

```bash
opssum init [--examples]
opssum agents                                              # đường dẫn đang dùng cho từng agent
opssum list [skill|mcp|instruction] [-a claude] [-s project] [--json]

opssum install   anthropics/skills                                         # chọn skills từ GitHub vào library
opssum install   skill:commit-helper mcp:context7 -a claude -a codex          # global
opssum install   instruction:coding-style --all-agents -s project --project ~/work/myapp
opssum uninstall skill:commit-helper -a codex
opssum install   skill:anthropics/pdf -a codex               # liên kết skill của một nhà phát hành
opssum uninstall skill:anthropics/pdf --library              # xoá khỏi library và gỡ mọi liên kết đã ghi nhận
opssum install   mcp:upstash/context7 -a claude -a codex     # cài cấu hình MCP đã tìm/lưu trong TUI
opssum uninstall mcp:upstash/context7 --library               # xoá MCP và gỡ khỏi agent/project đã ghi nhận
```

Mã thoát `1` nếu có thao tác thất bại.

### Cài skills từ GitHub

Trong TUI, nhấn **`i` — Install skills**, nhập `anthropics/skills` hoặc repository
GitHub khác theo dạng `owner/repo`. Dùng ↑ ↓ và Space để chọn nhiều skill, Enter
để cài, Esc để huỷ. Checkbox có cột riêng; mô tả màu xám được rút gọn trong danh sách.
Nhấn `i` ở skill đang chọn để đọc toàn bộ mô tả, dùng ↑ ↓ để cuộn, Esc hoặc `i`
để quay lại và giữ nguyên lựa chọn. Skill đã có trong library sẽ được hỏi xác nhận trước khi ghi đè.

CLI: `opssum install anthropics/skills`, sau đó nhập các số cách nhau bằng dấu phẩy
(ví dụ `1,3`) hoặc `all`. Enter khi chưa nhập gì sẽ huỷ. Khi hỏi ghi đè, nhập `y`
để đồng ý; các câu trả lời khác sẽ bỏ qua skill đó.

Cần Git và kết nối GitHub. Tool tải nhánh mặc định, tìm các thư mục có `SKILL.md`
và chép cả tài nguyên đi kèm vào `~/.opssum/skills/<owner>/<name>`
(hoặc library từ `--home` / `OPSSUM_HOME`). Sau đó dùng thao tác cài/gỡ
hiện có để liên kết skill với agent. Ghi đè library cũng cập nhật nội dung mà các
agent đang liên kết bằng symlink sử dụng. Repository có tên skill trùng nhau hoặc
skill chứa symlink sẽ báo lỗi.

### Nhóm nhà phát hành, đồng bộ và gỡ skills

Owner GitHub là tên nhóm: `anthropics/skills` và các repository khác của `anthropics`
cùng lưu dưới `skills/anthropics/`. Bảng Skills có divider phân nhóm nhà phát hành.
Skill cũ và skill nhập bằng `m` giữ đường dẫn `skills/<name>` và thuộc nhóm
**other**; tool không tự đoán nhà phát hành hoặc di chuyển skill cũ.
Các skill chưa được library quản lý nằm riêng trong khối **External**, kể cả khi
trùng tên với skill đã có trong library.

Hai nhà phát hành có thể có skill trùng tên. Trong CLI dùng tên đầy đủ như
`skill:anthropics/pdf`. Agent nhận symlink `<thư-mục-skills-agent>/pdf` trỏ thẳng
vào bản đã chọn trong library. Khi đổi sang nhà phát hành khác có cùng tên skill,
TUI và CLI hỏi xác nhận trước khi thay symlink. Cập nhật trong library sẽ được
mọi agent đang liên kết tới bản đó sử dụng ngay.

Phím **`m`** trên skill external chuyển thư mục gốc vào library và thay vị trí cũ
bằng symlink. Nếu nguồn vốn là symlink, tool copy nội dung và tài nguyên đi kèm
vào library, rồi thay symlink ở agent bằng symlink mới trỏ tới library; thư mục
nguồn thật được giữ nguyên. Nếu thao tác thất bại, tool khôi phục bản cũ.
MCP tự khai báo cũng được phát hiện ở global và project lúc mở TUI, cho phép nhập
vào library bằng modal đồng bộ hoặc phím `m`. Library MCP được nhóm theo nhà phát hành;
nhóm **other** dành cho MCP cũ/nhập tay, và **External** cho MCP chưa quản lý.
Khi xoá MCP khỏi library bằng `d`, ứng dụng gỡ server trong tất cả file cấu hình đã
ghi nhận và lưu bản sao vào `.trash`. Nếu hai nhà phát hành có server trùng tên,
phải xác nhận trước khi thay bản đang dùng trong một agent.

Mỗi lần mở TUI, ứng dụng quét skills và MCP global của agent cùng các cấu hình
trong project hiện tại. Nếu có item chưa được quản lý, hộp thoại đồng bộ
hiện bốn khối **Global skills**, **Project skills**, **Global MCP**, **Project MCP**, mặc định chọn tất cả.
Nhấn Space để chọn/bỏ từng dòng, Enter để đồng bộ các dòng đã chọn, Esc để đóng
mà không import. Đường dẫn dùng chung giữa nhiều agent chỉ xuất hiện một lần
trong mỗi khối. Ứng dụng không quét các project khác ở bước này.

Các bản trùng tên và giống nội dung dùng chung bản trong library. Nếu nội dung
khác, ứng dụng hỏi trước khi ghi đè; từ chối sẽ giữ nguyên nguồn đó và tiếp tục
các dòng khác. Skill đã liên kết với library không được hỏi lại ở lần mở sau.

Phím **`d`** trên skill trong library hoặc `opssum uninstall skill:owner/name --library`
hiện các đường dẫn bị ảnh hưởng và hỏi xác nhận. Đồng ý sẽ gỡ các liên kết ở global
và các project đã ghi nhận, rồi chuyển skill cùng bản sao các liên kết vào
`<library>/.trash/<id>/`. `manifest.json` tại đó lưu đường dẫn cũ để khôi phục thủ công.
Skill của nhà phát hành khác và thư mục external cùng tên được giữ nguyên.

Ứng dụng lưu các thư mục đã liên kết trong `<library>/.skill-locations.json`.
Project từng cài bằng phiên bản cũ cần được mở lại một lần (hoặc chọn bằng `P`)
để ghi nhận symlink. Ứng dụng không tự quét toàn bộ ổ đĩa để tìm project chưa biết.

## 9. Secrets (token, API key)

MCP modal cho nhập token/API key trực tiếp theo yêu cầu; các giá trị này được lưu
trong JSON library và file agent, không mã hoá. File MCP mới dùng quyền `0600`
trên hệ thống POSIX. **Không commit library hoặc bản backup chứa token lên Git.**
Nếu không muốn lưu token trong library, dùng placeholder và cấu hình biến môi trường
theo hướng dẫn riêng của agent/server.

## 10. Windows

- Tạo symlink cần **Developer Mode** (Settings → For developers) hoặc quyền admin.
  Nếu không tạo được symlink, tool báo lỗi và giữ bản cũ; hãy bật quyền rồi cài lại.
- Dùng Windows Terminal để hiển thị Unicode (`●○◐◌`) đúng.
- `~` được hiểu là `%USERPROFILE%`.

## 11. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| `✘ …JSON không hợp lệ…` | File config của agent bị hỏng/có comment. Sửa tay (tool từ chối ghi để không mất dữ liệu). Bản backup: `<file>.opssum.bak` |
| Ô hiện `◌` và `space` báo "không thể gỡ" | Thứ đó không do tool tạo. Bấm `m` để nhập vào library (skill/MCP), hoặc xoá tay |
| Agent không thấy skill vừa cài | Khởi động lại agent; kiểm tra đường dẫn bằng `opssum agents`; kiểm tra agent có nằm trong bảng ở mục 6 / override ở mục 7 |
| Item không hiện trong TUI | Kiểm tra banner có `⚠ n lỗi`; skill cần `SKILL.md`; tên chỉ gồm `A-Z a-z 0-9 . _ -` |
| Thêm file vào library lúc TUI đang mở | Bấm `r` |
| Bảng bị cắt ngang | Mở rộng cửa sổ terminal (≥ 110 cột là đẹp) hoặc dùng `f` để xem từng agent |

## 12. Phát triển & test

```bash
pip install -e ".[dev]"
pytest -q            # ops, nhóm skill, symlink, khôi phục khi lỗi và TUI bằng Textual Pilot
```

Cấu trúc code:

```
src/opssum/
├── catalog.py    # đọc library (skills/mcp/instructions)
├── agents.py     # registry agent + đường dẫn + agents.json
├── ops.py        # state / install / uninstall / adopt + Manager
├── scaffold.py   # opssum init
├── cli.py        # lệnh opssum
├── tui.py        # giao diện Textual
└── tui.tcss      # theme (cam #d97757, viền bo tròn, nền tối ấm)
```

## 13. Chưa có / ý tưởng tiếp theo

- Subagent definitions (`.claude/agents/*.md`, `~/.omp/agent/agents`), slash commands, hooks.
- Tạo/sửa item ngay trong TUI (hiện thêm file bằng editor bên ngoài, bấm `r`).
- `opssum sync` (cài lại toàn bộ theo manifest trong git để dựng máy mới).
- Nhập item `instruction` từ agent; kiểm tra MCP có chạy được (`doctor`).

MIT License.
