"""Package version: pyproject.toml is the single source of truth."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import sys


def _version() -> str:
    # When running directly from the checkout without installing the package,
    # distribution metadata does not exist. Frozen binaries ship the metadata.
    source_project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not getattr(sys, "frozen", False) and source_project.is_file():
        text = source_project.read_text(encoding="utf-8")
        project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
        if project and (match := re.search(r'(?m)^version\s*=\s*"([^"]+)"', project.group())):
            return match.group(1)
    try:
        return version("agent-knowledge")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _version()
