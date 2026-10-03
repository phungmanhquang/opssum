"""Absolute-import entry point for PyInstaller's one-file executable."""

from agent_knowledge.cli import entry


if __name__ == "__main__":
    raise SystemExit(entry())
