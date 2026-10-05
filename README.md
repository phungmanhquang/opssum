# ✻ opssum

opssum is a Textual TUI and CLI for managing **skills**, **MCP servers**, and
**instructions** for multiple agent CLIs (`claude`, `codex`, `pi`, `omp`, `agy`)
from one shared library: `~/.opssum`.

The library is the single source of truth. Install an item for one agent, every
agent, or a specific project. Skills are symlinked into agent directories, so an
update in the library is immediately visible everywhere.

## Installation

### Standalone binary (no Python required)

Linux/macOS:

```sh
curl -fsSL https://github.com/phungmanhquang/opssum/releases/latest/download/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://github.com/phungmanhquang/opssum/releases/latest/download/install.ps1 | iex
```

The installer detects the operating system and CPU, downloads the latest
standalone binary and SHA256 checksum, verifies the download, and installs
`opssum` into `~/.local/bin` (or `$HOME\.local\bin` on Windows). It adds the
directory to the user PATH when possible. Open a new terminal after a PATH change.

Run the application:

```sh
opssum init --examples   # optional sample library
opssum
```

Run the installer again to update. It replaces the binary only after checksum and
`--version` validation succeed. It never removes `~/.opssum`.

Uninstall on Linux/macOS:

```sh
rm "$HOME/.local/bin/opssum"
```

Uninstall on Windows:

```powershell
Remove-Item "$HOME\.local\bin\opssum.exe"
```

The library is preserved. Remove `~/.opssum` separately only if you want to delete
your skills, MCP configurations, and instructions.

### Install from source

Requires Python 3.10 or newer:

```sh
pipx install .
# or
uv tool install .
# or
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

### Local development install

To run the current checkout without replacing the standalone release binary:

```sh
./install-dev.sh
opssum-dev --version
opssum-dev init --examples
```

The script creates `~/.local/share/opssum-dev/venv`, installs the checkout in
editable mode, and links `opssum-dev` into `~/.local/bin`. Run it again after
dependency changes. Run tests with:

```sh
~/.local/share/opssum-dev/venv/bin/python -m pytest -q
```

Remove the development installation without touching the library:

```sh
./install-dev.sh --uninstall
```

## Quick start

```sh
opssum init --examples
opssum agents
opssum list --json
```

Use another library location with `OPSSUM_HOME` or `--home`:

```sh
OPSSUM_HOME=~/dotfiles/opssum opssum
opssum --home ~/dotfiles/opssum
```

## Library layout

```text
~/.opssum/
├── skills/
│   ├── anthropics/<skill-name>/SKILL.md   # grouped by GitHub owner
│   └── <skill-name>/SKILL.md              # legacy/manual skill: other
├── mcp/
│   ├── <server-name>.json                  # other
│   └── <publisher>/<server-name>.json
├── instructions/
│   └── <name>.md
└── agents.json                             # optional path overrides
```

Skills follow the Agent Skills format with `name` and `description` front matter.
MCP files may contain one normalized server or a copied `mcpServers` object.
Instructions are Markdown fragments inserted into agent memory files.

## TUI

Start `opssum` with no subcommand. The matrix shows items as rows and agents as
columns. The banner displays the active library, scope, item counts, and warnings.

| Key | Action |
| --- | --- |
| `↑ ↓ ← →` | Move through the matrix |
| `space` / `enter` | Install or uninstall the selected item |
| `A` / `X` | Install or uninstall for all agents |
| `u` | Update an outdated item from the library |
| `i` | Search/install GitHub skills or search Claude Marketplaces for MCP |
| `e` | Edit an MCP configuration in the library |
| `m` | Import an external item into the library |
| `d` | Remove a library item and recorded links |
| `f` | Filter to one agent |
| `o` | Show installed items only |
| `s` | Switch global/project scope |
| `P` | Change the project directory |
| `r` | Reload library and agent configuration |
| `?` | Show help |
| `q` | Quit |

Statuses are `● installed`, `◐ outdated`, `○ not installed`, and `◌ external`
(present on an agent but not managed by the library).

### Global and project scope

`global` writes to user configuration directories such as `~/.claude` and
`~/.codex`. `project` writes to the current project, such as `.claude/skills`,
`.mcp.json`, or `AGENTS.md`. Press `P` or use `--project <directory>` to change
the project.

## Skills

Search and import skills from GitHub with the `i` key, or use the CLI:

```sh
opssum install anthropics/skills
```

The TUI lists discovered skills grouped by GitHub owner. Descriptions are shown
in muted text; press `i` for the full description. Use Space to select multiple
skills and Enter to install them. Overwriting an existing library skill always
requires confirmation.

Publisher-qualified references avoid collisions:

```sh
opssum install skill:anthropics/pdf -a codex
opssum uninstall skill:anthropics/pdf --library
```

Skills are symlinked from agent directories into the library. Updating a library
directory therefore updates every linked agent. External skills can be imported
with `m`; if the source is a symlink, opssum copies the real directory into the
library and replaces the agent entry with a new library symlink.

## MCP servers

MCP search uses [Claude Marketplaces](https://claudemarketplaces.com/mcp). opssum
stores configuration only; it does not download packages or Docker images. The
user installs those dependencies separately.

Search with `i` on the MCP tab, select a result, review its description with `i`,
and edit the JSON configuration before saving. Press `e` on a library MCP to edit
it later. Publisher grouping uses the GitHub owner from `sourceRepo` when
available, otherwise the marketplace slug owner.

CLI examples:

```sh
opssum install mcp:upstash/context7 -a claude -a codex
opssum uninstall mcp:upstash/context7 --library
```

MCP JSON preserves unrelated keys and file permissions. Codex TOML uses marked
blocks without rewriting unrelated configuration. Invalid JSON is never
overwritten.

## Instructions

Instruction Markdown files in the library are inserted as marked blocks into
`CLAUDE.md`, `AGENTS.md`, or `GEMINI.md`. Removing an instruction deletes only its
own block; the rest of the file is preserved.

## Agent paths

The built-in registry currently covers:

| Agent | Global skills | Project skills | Global MCP |
| --- | --- | --- | --- |
| Claude | `~/.claude/skills` | `.claude/skills` | `~/.claude.json` |
| Codex | `~/.codex/skills` | `.agents/skills` | `~/.codex/config.toml` |
| Pi | `~/.pi/agent/skills` | `.pi/skills` | `~/.pi/agent/mcp.json` |
| omp | `~/.omp/agent/skills` | `.omp/skills` | `~/.omp/agent/mcp.json` |
| agy | `~/.gemini/antigravity-cli/skills` | `.agents/skills` | `~/.gemini/antigravity-cli/mcp_config.json` |

Override paths, disable agents, or add an agent in `~/.opssum/agents.json`.
Supported environment overrides include `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and
`PI_CODING_AGENT_DIR`.

## CLI reference

```sh
opssum init [--examples]
opssum agents
opssum list [skill|mcp|instruction] [-a agent] [-s global|project] [--json]
opssum install owner/repo
opssum install skill:name mcp:name -a claude -a codex
opssum install instruction:name --all-agents -s project --project ~/work/app
opssum uninstall skill:name -a codex
opssum uninstall skill:name --library
```

Commands return exit code `1` when an operation fails.

## Safety and secrets

opssum does not overwrite manually managed skill directories or MCP entries
without confirmation. It records linked locations so removing a library item can
uninstall it from all recorded global and project paths. Removed items and
configuration backups are stored for recovery under `.trash` or with an
`.opssum.bak` suffix.

MCP API keys and environment values are stored as plaintext in the library and
agent configuration files. Protect those files and never commit secrets.

On Windows, creating symlinks may require Developer Mode or administrator rights.

## Release workflow

Commit code with a clean working tree, then run:

```sh
./release.sh          # prompts for patch, minor, or major
./release.sh patch    # choose directly
```

The script updates `[project].version`, commits it, creates `vX.Y.Z`, and pushes
the commit and tag. The GitHub Actions workflow runs on `v*.*.*` tags, validates
the version, tests the project, and builds native PyInstaller binaries for Linux
x64/ARM64, macOS Intel/Apple Silicon, and Windows x64. It generates SHA256 files
and publishes one GitHub Release containing all binaries and both installers.

## Development and tests

```sh
./install-dev.sh
~/.local/share/opssum-dev/venv/bin/python -m pytest -q
```

The package source is under `src/opssum/`. `pyproject.toml` is the version source
of truth. The standalone build uses `opssum.spec` and must run on a native runner
for each target OS/architecture.

MIT License.
