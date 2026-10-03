import asyncio
import os
from pathlib import Path

import pytest

from agent_knowledge.ops import Manager, SkillConflict
from agent_knowledge.tui import AgentKnowledgeApp, SyncSkillsScreen, ConfirmScreen


@pytest.fixture
def env(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR", "AGENT_KNOWLEDGE_HOME"):
        monkeypatch.delenv(key, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    return Manager(tmp_path / "library", project), user


def skill(path, text="original"):
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(text)
    (path / "scripts").mkdir()
    (path / "scripts/helper.py").write_text("print('helper')")
    return path


async def startup(app, pilot):
    from textual.widgets import DataTable
    for _ in range(100):
        await pilot.pause(0.01)
        if isinstance(app.screen, SyncSkillsScreen) and app.screen.query_one(DataTable).row_count:
            return app.screen
    raise AssertionError("startup sync modal missing")


def test_symlink_copy_is_independent_and_rollback(env, tmp_path, monkeypatch):
    mgr, user = env
    source = skill(tmp_path / "shared/pdf")
    link = user / ".codex/skills/pdf"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(source, link.parent), target_is_directory=True)
    original_target = os.readlink(link)
    rename = Path.rename

    def deny_new_link(path, target):
        if path.name == "link":
            raise OSError("replacement failed")
        return rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", deny_new_link)
        assert not mgr.adopt("codex", "global", "skill", "pdf").ok
    assert os.readlink(link) == original_target
    assert not (mgr.home / "skills/pdf").exists()
    assert (source / "SKILL.md").read_text() == "original"
    assert mgr.adopt("codex", "global", "skill", "pdf").ok
    assert link.resolve() == mgr.home / "skills/pdf"
    assert (link / "scripts/helper.py").is_file()
    (source / "SKILL.md").write_text("outside changed")
    assert (link / "SKILL.md").read_text() == "original"
    (link / "SKILL.md").write_text("library changed")
    assert (source / "SKILL.md").read_text() == "outside changed"


def test_scan_scopes_deduplicates_shared_agent_directory(env, tmp_path):
    mgr, user = env
    skill(user / ".claude/skills/global-one")
    skill(mgr.project / ".agents/skills/project-one")
    skill(tmp_path / "another-project/.claude/skills/ignored")
    skill(mgr.home / "skills/managed")
    mgr.reload()
    assert mgr.install("codex", "global", "skill", "managed").ok
    items, errors = mgr.external_skills()
    assert not errors
    assert [(s.scope, s.name) for s in items] == [("global", "global-one"), ("project", "project-one")]
    assert set(items[1].agents) == {"codex", "agy"}


def test_identical_skills_reuse_library_and_conflicts_require_confirmation(env):
    mgr, user = env
    one = skill(user / ".claude/skills/pdf")
    two = skill(user / ".codex/skills/pdf")
    assert mgr.adopt("claude", "global", "skill", "pdf").ok
    assert mgr.adopt("codex", "global", "skill", "pdf").ok
    assert one.resolve() == two.resolve() == mgr.home / "skills/pdf"
    three = skill(user / ".pi/agent/skills/pdf", "different")
    with pytest.raises(SkillConflict):
        mgr.adopt("pi", "global", "skill", "pdf")
    assert not three.is_symlink()
    assert (one / "SKILL.md").read_text() == "original"
    assert mgr.adopt("pi", "global", "skill", "pdf", overwrite=True).ok
    assert (one / "SKILL.md").read_text() == "different"


def test_startup_escape_and_external_other_groups(env):
    from textual.widgets import DataTable
    mgr, user = env
    skill(mgr.home / "skills/pdf", "library")
    outside = skill(user / ".claude/skills/pdf", "external")
    mgr.reload()

    async def run():
        for _ in range(2):  # every opening asks again after Esc
            app = AgentKnowledgeApp(mgr)
            async with app.run_test(size=(120, 36)) as pilot:
                modal = await startup(app, pilot)
                assert modal.selected == {0}
                assert {k.value for k in modal.query_one(DataTable).rows} == {"scope:global", "scope:project", "0"}
                await pilot.press("escape")
                await app.workers.wait_for_complete()
                table = app.query_one("#matrix", DataTable)
                assert [k.value for k in table.rows] == ["@publisher:", "pdf", "@publisher:@external", "@external:pdf"]
                assert table.get_cell("@publisher:", "name").plain == "── other"
                assert table.get_cell("@publisher:@external", "name").plain == "── External"
                assert not outside.is_symlink()
                assert (outside / "SKILL.md").read_text() == "external"

    asyncio.run(run())


def test_startup_enter_imports_global_project_and_leaves_real_source(env, tmp_path):
    mgr, user = env
    shared = skill(tmp_path / "shared/pdf")
    global_link = user / ".claude/skills/pdf"
    global_link.parent.mkdir(parents=True)
    global_link.symlink_to(shared, target_is_directory=True)
    project_link = mgr.project / ".agents/skills/pdf"
    project_link.parent.mkdir(parents=True)
    project_link.symlink_to(shared, target_is_directory=True)
    regular = skill(mgr.project / ".pi/skills/local")

    async def run():
        app = AgentKnowledgeApp(mgr)
        async with app.run_test(size=(120, 36)) as pilot:
            modal = await startup(app, pilot)
            assert len(modal.selected) == 3
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert global_link.resolve() == project_link.resolve() == mgr.home / "skills/pdf"
            assert regular.is_symlink()
            assert (shared / "SKILL.md").read_text() == "original"
            assert not mgr.external_skills()[0]
        fresh = AgentKnowledgeApp(Manager(mgr.home, mgr.project))
        async with fresh.run_test() as pilot:
            await fresh.workers.wait_for_complete()
            assert not isinstance(fresh.screen, SyncSkillsScreen)

    asyncio.run(run())


@pytest.mark.parametrize("answer", ["n", "y"])
def test_startup_deselect_and_confirm_overwrite(env, answer):
    mgr, user = env
    skill(mgr.home / "skills/pdf", "library")
    external = skill(user / ".claude/skills/pdf", "different")
    skipped = skill(mgr.project / ".agents/skills/skip")
    mgr.reload()

    async def run():
        app = AgentKnowledgeApp(mgr)
        async with app.run_test(size=(120, 36)) as pilot:
            modal = await startup(app, pilot)
            from textual.widgets import DataTable
            table = modal.query_one(DataTable)
            table.move_cursor(row=3)  # project item, after two section headers
            await pilot.press("space")
            assert modal.selected == {0}
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.01)
                if isinstance(app.screen, ConfirmScreen):
                    break
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press(answer)
            await app.workers.wait_for_complete()
            assert external.is_symlink() == (answer == "y")
            assert not skipped.is_symlink()
            assert (mgr.home / "skills/pdf/SKILL.md").read_text() == ("different" if answer == "y" else "library")

    asyncio.run(run())


def test_rollback_failure_retains_original_backup(env, monkeypatch):
    mgr, user = env
    source = skill(user / ".claude/skills/pdf", "must survive")
    rename = Path.rename

    def fail_link_and_restore(path, target):
        if path.name in ("link", "old") and path.parent.name.startswith(".ak-adopt-"):
            raise OSError("filesystem failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_link_and_restore)
    result = mgr.adopt("claude", "global", "skill", "pdf")
    assert not result.ok
    assert "Dữ liệu được giữ tại" in result.msg
    backups = list(source.parent.glob(".ak-adopt-*/old/SKILL.md"))
    assert len(backups) == 1
    assert backups[0].read_text() == "must survive"
