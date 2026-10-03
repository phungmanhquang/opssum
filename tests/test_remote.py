import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_knowledge import remote
from agent_knowledge.cli import main
from agent_knowledge.ops import Manager


def make_skill(root, name, body="new"):
    path = root / "skills" / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test skill\n---\n{body}")
    (path / "scripts").mkdir()
    (path / "scripts" / "helper.txt").write_text("resource")
    return path


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "source"
    make_skill(root, "alpha")
    make_skill(root, "beta")
    monkeypatch.setattr(remote, "download", lambda *args: remote.discover(root))
    return root


def test_discover_and_overwrite(repository, tmp_path):
    skills = remote.discover(repository)
    assert [s.name for s in skills] == ["alpha", "beta"]
    home = tmp_path / "library"
    remote.install(home, skills[0])
    target = home / "skills/alpha/SKILL.md"
    target.write_text("local edits")
    with pytest.raises(FileExistsError):
        remote.install(home, skills[0])
    assert target.read_text() == "local edits"
    remote.install(home, skills[0], overwrite=True)
    assert target.read_text().endswith("new")
    assert (target.parent / "scripts/helper.txt").read_text() == "resource"
    assert not (home / "skills/beta").exists()


def test_discovery_rejects_duplicates_and_symlinks(tmp_path):
    first = make_skill(tmp_path / "one", "alpha")
    make_skill(tmp_path / "two", "alpha")
    with pytest.raises(ValueError, match="trùng tên"):
        remote.discover(tmp_path)
    (first / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        remote.discover(tmp_path / "one")


@pytest.mark.parametrize("value", ["../oops", "https://github.com/a/b", "a/b/extra", "-a/b", "a/b;ls"])
def test_invalid_repository(value, tmp_path):
    with pytest.raises(ValueError, match="owner/repo"):
        remote.download(value, tmp_path / "repo")


def test_download_failure(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(remote.subprocess, "run", fail)
    with pytest.raises(ValueError, match="Không tải được"):
        remote.download("anthropics/skills", tmp_path / "repo")


def test_download_root_skill(tmp_path, monkeypatch):
    def clone(args, **kwargs):
        root = Path(args[-1])
        root.mkdir()
        (root / "SKILL.md").write_text("root skill")
        assert args[-2] == "https://github.com/owner/my-skill.git"
        assert kwargs["timeout"] == 120
    monkeypatch.setattr(remote.subprocess, "run", clone)
    skills = remote.download("owner/my-skill", tmp_path / "repo")
    assert [s.name for s in skills] == ["my-skill"]


def test_empty_repository(tmp_path):
    with pytest.raises(ValueError, match="không có skill"):
        remote.discover(tmp_path)


def test_rollback(repository, tmp_path, monkeypatch):
    home = tmp_path / "library"
    target = make_skill(home, "alpha", "local edits")
    original = Path.rename
    def rename(path, dest):
        if path.name == "new":
            raise OSError("simulated failure")
        return original(path, dest)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError):
        remote.install(home, remote.discover(repository)[0], overwrite=True)
    assert (target / "SKILL.md").read_text().endswith("local edits")


@pytest.mark.parametrize("answer, expected", [("n", "local edits"), ("y", "new")])
def test_cli_selection_and_confirmation(repository, tmp_path, monkeypatch, answer, expected):
    home = tmp_path / "library"
    target = make_skill(home, "alpha", "local edits")
    replies = iter(["99", "1", answer])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    assert main(["install", "anthropics/skills", "--home", str(home)]) == 0
    assert (target / "SKILL.md").read_text().endswith(expected)
    assert not (home / "skills/beta").exists()


@pytest.mark.parametrize("answer, expected", [("n", "local edits"), ("y", "new")])
def test_tui_install(repository, tmp_path, monkeypatch, answer, expected):
    from agent_knowledge.tui import AgentKnowledgeApp, SkillSelectScreen, ConfirmScreen
    from textual.widgets import Input
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    home = tmp_path / "library"
    target = make_skill(home, "alpha", "local edits")
    app = AgentKnowledgeApp(Manager(home, tmp_path))

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("i")
            await pilot.pause()
            app.screen.query_one(Input).value = "anthropics/skills"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.02)
                if isinstance(app.screen, SkillSelectScreen):
                    break
            assert isinstance(app.screen, SkillSelectScreen)
            await pilot.press("space", "down", "space", "enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press(answer)
            for _ in range(50):
                await pilot.pause(0.02)
                if not app._remote_busy:
                    break
            assert not app._remote_busy
            assert (target / "SKILL.md").read_text().endswith(expected)
            assert (home / "skills/beta/SKILL.md").is_file()
            assert app.mgr.lib.get("skill", "alpha")
            await pilot.press("i", "escape")
            await pilot.pause()
            assert not app._remote_busy

    asyncio.run(run())
