import asyncio
import json

import pytest

from agent_knowledge import mcp_marketplace
from agent_knowledge.ops import Manager, McpConflict


@pytest.fixture
def env(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR", "AGENT_KNOWLEDGE_HOME"):
        monkeypatch.delenv(key, raising=False)
    return Manager(tmp_path / "library", project), user, project


def test_marketplace_search_and_parse(monkeypatch):
    payload = {"items": [{"slug": "registry/context7", "displayName": "Context7", "sourceRepo": "upstash/context7",
                          "summary": "Docs", "installLabel": "@upstash/context7-mcp"},
                         {"slug": "../unsafe", "displayName": "Bad"}]}
    monkeypatch.setattr(mcp_marketplace, "_get", lambda path: json.dumps(payload).encode())
    found = mcp_marketplace.search("context7")
    assert [(x.slug, x.publisher) for x in found] == [("registry/context7", "upstash")]
    name, spec = mcp_marketplace.parse_command(
        "claude mcp add context7 --env CONTEXT7_API_KEY=YOUR_KEY -- npx -y @upstash/context7-mcp")
    assert name == "context7" and spec["command"] == "npx"
    assert spec["env"]["CONTEXT7_API_KEY"] == "YOUR_KEY"
    name, spec = mcp_marketplace.parse_command(
        "claude mcp add --transport http search https://example.com/mcp --header 'Authorization: Bearer token'")
    assert name == "search" and spec["headers"] == {"Authorization": "Bearer token"}


def test_marketplace_ignores_related_server_commands(monkeypatch):
    related = '9:["$","$L4c","other/server",{"server":{"installCommand":"claude mcp add wrong -- npx unrelated"}}]'
    page = '<script>self.__next_f.push([1,' + json.dumps(related) + '])</script>'
    monkeypatch.setattr(mcp_marketplace, "_get", lambda path: page.encode())
    with pytest.raises(ValueError, match="Không đọc được"):
        mcp_marketplace.configuration(mcp_marketplace.Listing("right/server", "Right", "", "right"))


def test_publisher_collision_and_cascade(env):
    mgr, user, project = env
    first = {"description": "First", "command": "npx", "args": ["one"]}
    second = {"description": "Second", "command": "npx", "args": ["two"]}
    assert mgr.save_mcp("alpha/tool", first).ok
    assert mgr.save_mcp("beta/tool", second).ok
    assert {i.publisher for i in mgr.lib.items("mcp")} == {"alpha", "beta"}
    assert mgr.install("claude", "global", "mcp", "alpha/tool").ok
    assert mgr.install("claude", "project", "mcp", "alpha/tool").ok
    assert mgr.install("codex", "global", "mcp", "alpha/tool").ok
    assert mgr.save_mcp("alpha/tool", {"description": "Updated", "command": "npx", "args": ["new"]}, overwrite=True).ok
    assert mgr.states("codex", "global", "mcp")["alpha/tool"].status == "outdated"
    assert not mgr.install("codex", "global", "mcp", "alpha/tool").ok
    assert mgr.install("codex", "global", "mcp", "alpha/tool", replace=True).ok
    assert not mgr.install("claude", "global", "mcp", "beta/tool").ok
    assert mgr.mcp_conflict("claude", "global", "beta/tool") == "alpha/tool"
    assert mgr.install("claude", "global", "mcp", "beta/tool", replace=True).ok
    assert mgr.states("claude", "global", "mcp")["beta/tool"].status == "installed"
    assert mgr.states("claude", "global", "mcp")["alpha/tool"].status == "absent"
    dependents = mgr.mcp_dependents("alpha/tool")
    assert len(dependents) == 2
    assert mgr.remove_mcp("alpha/tool").ok
    assert mgr.lib.get("mcp", "alpha/tool") is None
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"] == {}
    assert "[mcp_servers.tool]" not in (user / ".codex/config.toml").read_text()
    assert json.loads((user / ".claude.json").read_text())["mcpServers"]["tool"]["args"] == ["two"]


def test_external_sync_json_and_toml(env):
    mgr, user, _ = env
    claude = user / ".claude.json"
    claude.write_text(json.dumps({"other": 1, "mcpServers": {"mine": {"command": "node", "args": ["server.js"]}}}))
    codex = user / ".codex/config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('model = "x"\n[mcp_servers.local]\ncommand = "node"\nargs = ["local.js"]\n')
    found, errors = mgr.external_mcps()
    assert not errors
    assert {(x.name, x.agent_id) for x in found} == {("mine", "claude"), ("local", "codex")}
    assert mgr.adopt("claude", "global", "mcp", "mine").ok
    assert mgr.adopt("codex", "global", "mcp", "local").ok
    assert mgr.external_mcps()[0] == []
    assert mgr.states("claude", "global", "mcp")["mine"].status == "installed"
    assert "# >>> agent-knowledge:local >>>" in codex.read_text()
    assert mgr.remove_mcp("mine").ok
    assert json.loads(claude.read_text()) == {"other": 1, "mcpServers": {}}


def test_edit_and_delete_bundled_library_preserves_other_servers(env):
    mgr, _, _ = env
    bundle = mgr.home / "mcp/bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(json.dumps({"mcpServers": {
        "one": {"command": "node", "args": ["one.js"]},
        "two": {"command": "node", "args": ["two.js"]},
    }}))
    mgr.reload()
    assert mgr.save_mcp("one", {"command": "node", "args": ["updated.js"]}, overwrite=True).ok
    assert mgr.lib.get("mcp", "one").spec["args"] == ["updated.js"]
    assert mgr.remove_mcp("one").ok
    assert mgr.lib.get("mcp", "one") is None
    assert mgr.lib.get("mcp", "two") is not None
    assert json.loads(bundle.read_text())["mcpServers"]["two"]["args"] == ["two.js"]


def test_external_mcp_name_collision_requires_confirmation(env):
    mgr, user, _ = env
    assert mgr.save_mcp("mine", {"command": "node", "args": ["library.js"]}).ok
    config = user / ".claude.json"
    config.write_text(json.dumps({"mcpServers": {"mine": {"command": "node", "args": ["external.js"]}}}))
    with pytest.raises(McpConflict):
        mgr.adopt("claude", "global", "mcp", "mine")
    assert mgr.lib.get("mcp", "mine").spec["args"] == ["library.js"]
    assert mgr.adopt("claude", "global", "mcp", "mine", overwrite=True).ok
    assert mgr.lib.get("mcp", "mine").spec["args"] == ["external.js"]


def test_legacy_toml_marker_is_recorded_when_project_opened(env):
    mgr, _, project = env
    assert mgr.save_mcp("alpha/tool", {"command": "node", "args": ["server.js"]}).ok
    path = project / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    from agent_knowledge.ops import _tblock, render_toml
    path.write_text(_tblock("tool", render_toml("tool", mgr.lib.get("mcp", "alpha/tool").spec)))
    mgr.reload()
    assert mgr.mcp_dependents("alpha/tool") == [(path, "tool")]
    assert mgr.remove_mcp("alpha/tool").ok
    assert "mcp_servers.tool" not in path.read_text()


def test_tui_mcp_search_and_edit_modal(env, monkeypatch):
    from agent_knowledge.tui import AgentKnowledgeApp, McpSearchScreen, McpConfigScreen
    from textual.widgets import Input
    mgr, _, _ = env
    listing = mcp_marketplace.Listing("upstash/context7", "Context7", "Docs", "upstash")
    monkeypatch.setattr(mcp_marketplace, "search", lambda q: [listing])
    monkeypatch.setattr(mcp_marketplace, "configuration", lambda x: ("context7", {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]}))

    async def run():
        app = AgentKnowledgeApp(mgr)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("2", "i")
            await pilot.pause()
            await pilot.press("t", "e", "s", "t", "enter")
            for _ in range(50):
                await pilot.pause(0.01)
                if isinstance(app.screen, McpSearchScreen):
                    break
            assert isinstance(app.screen, McpSearchScreen)
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.01)
                if isinstance(app.screen, McpConfigScreen):
                    break
            assert isinstance(app.screen, McpConfigScreen)
            assert app.screen.query_one("#mcp-reference", Input).value == "upstash/context7"
            await pilot.press("ctrl+s")
            await app.workers.wait_for_complete()
            assert mgr.lib.get("mcp", "upstash/context7") is not None
    asyncio.run(run())
