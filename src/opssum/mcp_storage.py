"""Remember MCP installations across global and previously opened projects."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

REGISTRY = ".mcp-locations.json"


def bindings(home: Path) -> dict[str, dict[str, str]]:
    path = home / REGISTRY
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError(f"MCP registry must not be a symlink: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid MCP registry: {path}")
    for filename, names in data.items():
        if not isinstance(filename, str) or not Path(filename).is_absolute() or not isinstance(names, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in names.items()
        ):
            raise ValueError(f"Invalid MCP registry: {path}")
    return data


def _write(home: Path, data: dict[str, dict[str, str]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    path = home / REGISTRY
    if path.is_symlink():
        raise ValueError(f"MCP registry must not be a symlink: {path}")
    with tempfile.TemporaryDirectory(prefix=".mcp-locations-", dir=home) as temp:
        stage = Path(temp) / "registry.json"
        stage.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, path)


def record(home: Path, config: Path, server: str, reference: str) -> None:
    data = bindings(home)
    key = str(config.resolve())
    data.setdefault(key, {})[server] = reference
    _write(home, data)


def forget(home: Path, config: Path, server: str) -> None:
    data = bindings(home)
    key = str(config.resolve())
    if key not in data or server not in data[key]:
        return
    del data[key][server]
    if not data[key]:
        del data[key]
    _write(home, data)
