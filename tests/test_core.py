import asyncio
import json
import os
from pathlib import Path

import pytest

from agent_knowledge.ops import Manager
from agent_knowledge.scaffold import init_library


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for v in ("CODEX_HOME", "PI_CODING_AGENT_DIR", "CLAUDE_CONFIG_DIR", "AGENT_KNOWLEDGE_HOME"):
        monkeypatch.delenv(v, raising=False)
    lib = home / ".agent-knowledge"
    init_library(lib, examples=True)
    return Manager(lib, proj), home, proj


def test_library_scan(env):
    m, _, _ = env
    assert [i.name for i in m.lib.items("skill")] == ["code-review", "commit-helper", "vue2-conventions"]
    assert {i.name for i in m.lib.items("mcp")} == {"context7", "deepwiki", "filesystem"}
    assert len(m.lib.items("instruction")) == 2
    assert not m.lib.errors


def test_skill_install_uninstall_per_agent(env):
    m, home, _ = env
    for s in ("commit-helper", "code-review", "vue2-conventions"):
        assert m.install("claude", "global", "skill", s).ok
    for s in ("commit-helper", "code-review"):
        assert m.install("codex", "global", "skill", s).ok
    assert (home / ".claude/skills/commit-helper").is_symlink()
    c = m.states("claude", "global", "skill")
    x = m.states("codex", "global", "skill")
    assert sum(s.status == "installed" for s in c.values()) == 3
    assert sum(s.status == "installed" for s in x.values()) == 2
    # gỡ 1 skill của codex, claude không bị ảnh hưởng
    assert m.uninstall("codex", "global", "skill", "code-review").ok
    assert m.states("codex", "global", "skill")["code-review"].status == "absent"
    assert m.states("claude", "global", "skill")["code-review"].status == "installed"


def test_skill_external_not_touched(env):
    m, home, _ = env
    mine = home / ".codex/skills/my-own"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: my-own\ndescription: x\n---\n")
    st = m.states("codex", "global", "skill")["my-own"]
    assert st.status == "external" and not st.removable
    assert not m.uninstall("codex", "global", "skill", "my-own").ok
    assert mine.exists()
    # adopt vào library
    assert m.adopt("codex", "global", "skill", "my-own").ok
    assert m.lib.get("skill", "my-own")


def test_skill_name_clash_refused(env):
    m, home, _ = env
    d = home / ".claude/skills/commit-helper"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: commit-helper\ndescription: mine\n---\n")
    assert not m.install("claude", "global", "skill", "commit-helper").ok
    assert d.is_dir() and not d.is_symlink()


def test_skill_copy_mode(env):
    m, home, _ = env
    # Legacy managed copies can still be removed or converted to a live symlink.
    import shutil
    t = home / ".pi/agent/skills/code-review"
    source = m.lib.get("skill", "code-review").path
    shutil.copytree(source, t)
    (t / ".agent-knowledge").write_text(str(source))
    assert m.install("pi", "global", "skill", "code-review").ok
    assert t.is_symlink() and t.resolve() == source.resolve()
    assert m.uninstall("pi", "global", "skill", "code-review").ok and not t.exists()


def test_symlink_failure_preserves_legacy_copy(env, monkeypatch):
    import shutil
    m, home, _ = env
    source = m.lib.get("skill", "code-review").path
    target = home / ".pi/agent/skills/code-review"
    shutil.copytree(source, target)
    (target / ".agent-knowledge").write_text(str(source))
    (target / "SKILL.md").write_text("local edits")

    def denied(*args, **kwargs):
        raise PermissionError("symlinks unavailable")

    monkeypatch.setattr(os, "symlink", denied)
    result = m.install("pi", "global", "skill", "code-review")
    assert not result.ok
    assert not target.is_symlink()
    assert (target / "SKILL.md").read_text() == "local edits"


def test_instruction_block_roundtrip(env):
    m, home, proj = env
    f = proj / "CLAUDE.md"
    f.write_text("# Của tôi\n\nđừng xoá dòng này\n")
    assert m.install("claude", "project", "instruction", "coding-style").ok
    assert m.install("claude", "project", "instruction", "git-workflow").ok
    text = f.read_text()
    assert "đừng xoá dòng này" in text and "agent-knowledge:begin coding-style" in text
    assert m.states("claude", "project", "instruction")["coding-style"].status == "installed"
    assert m.uninstall("claude", "project", "instruction", "coding-style").ok
    text = f.read_text()
    assert "coding-style" not in text and "git-workflow" in text and "đừng xoá dòng này" in text
    # library đổi -> outdated
    lib_file = m.lib.get("instruction", "git-workflow").path
    lib_file.write_text(lib_file.read_text() + "\n- thêm dòng mới\n")
    m.reload()
    assert m.states("claude", "project", "instruction")["git-workflow"].status == "outdated"
    assert m.install("claude", "project", "instruction", "git-workflow").ok
    assert m.states("claude", "project", "instruction")["git-workflow"].status == "installed"


def test_mcp_json_preserves_other_keys(env):
    m, home, _ = env
    cj = home / ".claude.json"
    cj.write_text(json.dumps({"numStartups": 7, "mcpServers": {"mine": {"command": "x"}}}))
    os.chmod(cj, 0o600)
    assert m.install("claude", "global", "mcp", "context7").ok
    assert m.install("claude", "global", "mcp", "deepwiki").ok
    data = json.loads(cj.read_text())
    assert data["numStartups"] == 7 and "mine" in data["mcpServers"]
    assert data["mcpServers"]["context7"]["type"] == "stdio"
    assert data["mcpServers"]["deepwiki"] == {"type": "http", "url": "https://mcp.deepwiki.com/mcp"}
    assert oct(cj.stat().st_mode & 0o777) == "0o600"
    assert (home / ".claude.json.agent-knowledge.bak").exists()
    st = m.states("claude", "global", "mcp")
    assert st["mine"].status == "external" and st["context7"].status == "installed"
    assert m.uninstall("claude", "global", "mcp", "context7").ok
    assert "context7" not in json.loads(cj.read_text())["mcpServers"]
    assert m.adopt("claude", "global", "mcp", "mine").ok
    assert m.lib.get("mcp", "mine").spec["command"] == "x"


def test_mcp_invalid_json_not_overwritten(env):
    m, home, _ = env
    p = home / ".pi/agent/mcp.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ not json")
    r = m.install("pi", "global", "mcp", "context7")
    assert not r.ok and p.read_text() == "{ not json"


def test_mcp_codex_toml(env):
    m, home, _ = env
    cfg = home / ".codex/config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('model = "gpt-5"\n\n[mcp_servers.mine]\ncommand = "foo"\n')
    assert m.install("codex", "global", "mcp", "context7").ok
    assert m.install("codex", "global", "mcp", "deepwiki").ok
    text = cfg.read_text()
    assert 'model = "gpt-5"' in text and "[mcp_servers.mine]" in text
    assert "[mcp_servers.context7]" in text and 'url = "https://mcp.deepwiki.com/mcp"' in text
    try:
        import tomllib
        d = tomllib.loads(text)
        assert d["mcp_servers"]["context7"]["command"] == "npx"
    except ImportError:
        pass
    st = m.states("codex", "global", "mcp")
    assert st["mine"].status == "external" and not st["mine"].removable
    assert m.uninstall("codex", "global", "mcp", "context7").ok
    assert "context7" not in cfg.read_text() and "[mcp_servers.mine]" in cfg.read_text()


def test_agy_remote_uses_serverUrl(env):
    m, home, _ = env
    assert m.install("agy", "global", "mcp", "deepwiki").ok
    data = json.loads((home / ".gemini/antigravity-cli/mcp_config.json").read_text())
    assert data["mcpServers"]["deepwiki"] == {"serverUrl": "https://mcp.deepwiki.com/mcp"}


def test_shared_project_paths(env):
    m, _, _ = env
    assert set(m.shared_with("codex", "project", "skill")) == {"agy"}
    assert "pi" in m.shared_with("omp", "project", "instruction")


def test_tui_smoke(env):
    from agent_knowledge.tui import AgentKnowledgeApp
    m, home, _ = env

    async def run():
        app = AgentKnowledgeApp(m)
        async with app.run_test(size=(150, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#matrix")
            assert table.row_count == 4  # 3 skills + divider Chưa phân nhóm
            # cursor bắt đầu ở cột agent đầu tiên (claude)
            await pilot.press("space")
            await pilot.pause()
            assert (home / ".claude/skills/code-review").is_symlink()
            await pilot.press("right", "down", "space")   # codex, commit-helper
            await pilot.pause()
            assert (home / ".codex/skills/commit-helper").is_symlink()
            await pilot.press("A")
            await pilot.pause()
            assert (home / ".pi/agent/skills/commit-helper").is_symlink()
            await pilot.press("down", "left", "enter")   # vue2-conventions, cột claude: Enter cũng toggle
            await pilot.pause()
            assert (home / ".claude/skills/vue2-conventions").is_symlink()
            await pilot.press("up", "X", "y")
            await pilot.pause()
            assert not (home / ".pi/agent/skills/commit-helper").exists()
            await pilot.press("2")
            await pilot.pause()
            assert app.cur_kind == "mcp" and app.query_one("#matrix").row_count == 4
            await pilot.press("f")
            await pilot.pause()
            assert app.agent_filter == "claude"
            app.save_screenshot(str(home / "shot.svg"))
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.press("q")

    asyncio.run(run())
