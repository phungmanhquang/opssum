import asyncio
import json
import os
from pathlib import Path

import pytest

from opssum import remote
from opssum.catalog import Library
from opssum.cli import main
from opssum.ops import Manager


@pytest.fixture
def setup(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR", "OPSSUM_HOME"):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: pdf\ndescription: Work with PDFs\n---\noriginal")
    home = tmp_path / "library"
    project = tmp_path / "project"
    project.mkdir()
    return Manager(home, project), source, user


def add(mgr, source, publisher="anthropics", name="pdf"):
    skill = remote.RemoteSkill(name, source, "Work with PDFs", publisher)
    remote.install(mgr.home, skill)
    mgr.reload()
    return skill


def test_grouped_install_update_and_collision(setup):
    mgr, source, user = setup
    first = add(mgr, source)
    add(mgr, source, "other")
    add(mgr, source, "anthropics", "xlsx")
    add(mgr, source, "", "legacy")
    assert {i.name for i in mgr.lib.items("skill")} == {"anthropics/pdf", "anthropics/xlsx", "other/pdf", "legacy"}
    assert mgr.lib.get("skill", "anthropics/pdf").publisher == "anthropics"
    assert mgr.install("codex", "global", "skill", "anthropics/pdf").ok
    assert mgr.install("claude", "project", "skill", "anthropics/pdf").ok
    link = user / ".codex/skills/pdf"
    assert link.resolve() == mgr.home / "skills/anthropics/pdf"
    assert mgr.states("codex", "global", "skill")["other/pdf"].status == "absent"
    assert not mgr.install("codex", "global", "skill", "other/pdf").ok
    assert mgr.skill_conflict("codex", "global", "other/pdf") == "anthropics/pdf"
    assert mgr.uninstall("codex", "global", "skill", "other/pdf").ok
    assert link.resolve() == mgr.home / "skills/anthropics/pdf"
    (source / "SKILL.md").write_text("updated")
    remote.install(mgr.home, first, overwrite=True)
    assert (link / "SKILL.md").read_text() == "updated"
    assert (mgr.project / ".claude/skills/pdf/SKILL.md").read_text() == "updated"
    assert mgr.install("codex", "global", "skill", "other/pdf", replace=True).ok
    assert mgr.states("codex", "global", "skill")["other/pdf"].status == "installed"
    assert mgr.states("codex", "global", "skill")["anthropics/pdf"].status == "absent"


def test_move_to_library_and_rollback(setup, monkeypatch):
    mgr, source, user = setup
    original = user / ".codex/skills/local"
    original.mkdir(parents=True)
    (original / "SKILL.md").write_text("local content")
    assert mgr.adopt("codex", "global", "skill", "local").ok
    assert original.is_symlink()
    assert original.resolve() == mgr.home / "skills/local"
    assert mgr.states("codex", "global", "skill")["local"].status == "installed"
    assert mgr.install("claude", "project", "skill", "local").ok
    (mgr.home / "skills/local/SKILL.md").write_text("new version")
    assert (original / "SKILL.md").read_text() == "new version"

    second = user / ".codex/skills/second"
    second.mkdir()
    (second / "SKILL.md").write_text("preserve me")
    rename = Path.rename

    def fail_link(path, target):
        if path.name == "link":
            raise OSError("link replacement failed")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_link)
    assert not mgr.adopt("codex", "global", "skill", "second").ok
    assert not second.is_symlink()
    assert (second / "SKILL.md").read_text() == "preserve me"
    assert not (mgr.home / "skills/second").exists()


def test_delete_across_projects_and_preserve_other_publisher(setup, tmp_path):
    mgr, source, user = setup
    add(mgr, source)
    add(mgr, source, "other")
    assert mgr.install("codex", "global", "skill", "anthropics/pdf").ok
    assert mgr.install("claude", "project", "skill", "anthropics/pdf").ok
    first_project = mgr.project
    second = Manager(mgr.home, tmp_path / "second")
    assert second.install("codex", "project", "skill", "anthropics/pdf").ok
    assert second.install("claude", "global", "skill", "other/pdf").ok
    # An unrelated skill folder of the same name must survive library removal.
    external = user / ".pi/agent/skills/pdf"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("external")
    fresh = Manager(mgr.home, tmp_path / "third")
    assert len(fresh.skill_dependents("anthropics/pdf")) == 3
    result = fresh.remove_skill("anthropics/pdf")
    assert result.ok, result.msg
    assert not os.path.lexists(user / ".codex/skills/pdf")
    assert not os.path.lexists(first_project / ".claude/skills/pdf")
    assert not os.path.lexists(second.project / ".agents/skills/pdf")
    assert (user / ".claude/skills/pdf").resolve() == mgr.home / "skills/other/pdf"
    assert (external / "SKILL.md").read_text() == "external"
    assert fresh.lib.get("skill", "anthropics/pdf") is None
    backup = next((mgr.home / ".trash").iterdir())
    assert (backup / "skill/SKILL.md").read_text().endswith("original")
    assert len(json.loads((backup / "manifest.json").read_text())["links"]) == 3


def test_open_legacy_project_records_symlinks(setup, tmp_path):
    mgr, source, user = setup
    add(mgr, source)
    old_project = tmp_path / "old-project"
    link = old_project / ".claude/skills/pdf"
    link.parent.mkdir(parents=True)
    link.symlink_to(mgr.home / "skills/anthropics/pdf", target_is_directory=True)
    Manager(mgr.home, old_project)
    fresh = Manager(mgr.home, tmp_path / "elsewhere")
    assert fresh.remove_skill("anthropics/pdf").ok
    assert not os.path.lexists(link)


def test_delete_failure_rolls_back_links(setup, monkeypatch):
    mgr, source, user = setup
    add(mgr, source)
    assert mgr.install("codex", "global", "skill", "anthropics/pdf").ok
    original = Path.rename

    def fail_source(path, target):
        if path == mgr.home / "skills/anthropics/pdf":
            raise OSError("cannot move skill")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", fail_source)
    result = mgr.remove_skill("anthropics/pdf")
    assert not result.ok
    assert (user / ".codex/skills/pdf/SKILL.md").is_file()
    assert (mgr.home / "skills/anthropics/pdf/SKILL.md").is_file()


def test_group_folder_cannot_be_overwritten(setup):
    mgr, source, _ = setup
    add(mgr, source)
    with pytest.raises(ValueError, match="thư mục nhóm"):
        remote.install(mgr.home, remote.RemoteSkill("anthropics", source, ""), overwrite=True)
    assert (mgr.home / "skills/anthropics/pdf/SKILL.md").is_file()


def test_adopt_external_same_name_and_external_symlink(setup):
    mgr, source, user = setup
    add(mgr, source)
    external = user / ".codex/skills/pdf"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("my pdf skill")
    states = mgr.states("codex", "global", "skill")
    assert states["pdf"].status == "external"
    assert mgr.adopt("codex", "global", "skill", "pdf").ok
    assert external.resolve() == mgr.home / "skills/pdf"
    assert (mgr.home / "skills/anthropics/pdf/SKILL.md").read_text().endswith("original")
    foreign = user / ".codex/skills/foreign"
    foreign.symlink_to(source, target_is_directory=True)
    assert mgr.adopt("codex", "global", "skill", "foreign").ok
    assert foreign.resolve() == mgr.home / "skills/foreign"
    assert (source / "SKILL.md").is_file()


def test_corrupt_registry_prevents_removal(setup):
    mgr, source, user = setup
    add(mgr, source)
    assert mgr.install("codex", "global", "skill", "anthropics/pdf").ok
    (mgr.home / ".skill-locations.json").write_text("not json")
    mgr.reload()
    assert mgr.lib.errors
    assert not mgr.remove_skill("anthropics/pdf").ok
    assert (user / ".codex/skills/pdf/SKILL.md").is_file()


def test_cli_repository_installs_in_publisher_directory(setup, monkeypatch):
    mgr, source, user = setup
    def clone(args, **kwargs):
        path = Path(args[-1]) / "skills/pdf"
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("description")
    monkeypatch.setattr(remote.subprocess, "run", clone)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert main(["install", "Anthropics/skills", "--home", str(mgr.home)]) == 0
    assert (mgr.home / "skills/anthropics/pdf/SKILL.md").is_file()
    assert Library(mgr.home).get("skill", "anthropics/pdf") is not None


def test_tui_adopt_confirmation(setup):
    from opssum.tui import OpssumApp, ConfirmScreen
    from textual.widgets import DataTable
    mgr, source, user = setup
    external = user / ".claude/skills/local"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("local source")
    app = OpssumApp(mgr)

    async def run():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            await pilot.press("escape")  # close startup sync to exercise manual m
            await pilot.press("m")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("n")
            assert not external.is_symlink()
            await pilot.press("m", "y")
            await app.workers.wait_for_complete()
            assert external.is_symlink()
            assert external.resolve() == mgr.home / "skills/local"
            assert mgr.lib.get("skill", "local") is not None

    asyncio.run(run())


def test_cli_qualified_skill_and_library_remove(setup, monkeypatch):
    mgr, source, user = setup
    add(mgr, source)
    assert main(["install", "skill:anthropics/pdf", "-a", "codex", "--home", str(mgr.home)]) == 0
    monkeypatch.setattr("builtins.input", lambda _: "n")
    args = ["uninstall", "skill:anthropics/pdf", "--library", "--home", str(mgr.home)]
    assert main(args) == 0
    assert (user / ".codex/skills/pdf").is_symlink()
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert main(args) == 0
    assert not os.path.lexists(user / ".codex/skills/pdf")


def test_tui_groups_switch_and_delete(setup):
    from opssum.tui import OpssumApp, ConfirmScreen
    from textual.widgets import DataTable
    mgr, source, user = setup
    add(mgr, source)
    add(mgr, source, "other")
    add(mgr, source, "", "legacy")
    assert mgr.install("codex", "global", "skill", "anthropics/pdf").ok
    app = OpssumApp(mgr)

    async def run():
        async with app.run_test(size=(150, 40)) as pilot:
            table = app.query_one("#matrix", DataTable)
            keys = [key.value for key in table.rows]
            assert keys == ["@publisher:anthropics", "anthropics/pdf", "@publisher:other", "other/pdf", "@publisher:", "legacy"]
            table.move_cursor(row=keys.index("other/pdf"), column=3)
            await pilot.press("space")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("n")
            assert (user / ".codex/skills/pdf").resolve() == mgr.home / "skills/anthropics/pdf"
            await pilot.press("space", "y")
            assert (user / ".codex/skills/pdf").resolve() == mgr.home / "skills/other/pdf"
            await pilot.press("d", "n")
            assert (mgr.home / "skills/other/pdf").exists()
            await pilot.press("d", "y")
            assert not os.path.lexists(user / ".codex/skills/pdf")
            assert (mgr.home / "skills/anthropics/pdf").exists()
            assert "@publisher:other" not in [key.value for key in table.rows]

    asyncio.run(run())
