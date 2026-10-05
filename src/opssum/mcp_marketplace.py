"""Read-only Claude Marketplaces discovery and configuration extraction.

Marketplace install commands are parsed as data; they are never executed.
"""
from __future__ import annotations

import html
import json
import re
import shlex
from dataclasses import dataclass
from urllib.parse import quote
from urllib.request import Request, urlopen

from .catalog import NAME_RE, normalize_mcp

BASE = "https://claudemarketplaces.com"
MAX_RESPONSE = 4_000_000


@dataclass(frozen=True)
class Listing:
    slug: str
    name: str
    description: str
    publisher: str
    install_label: str = ""


def _get(path: str) -> bytes:
    request = Request(BASE + path, headers={"User-Agent": "opssum-tui/1.0", "Accept": "text/html,application/json"})
    with urlopen(request, timeout=15) as response:
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("The Claude Marketplaces response is too large.")
    return data


def search(query: str) -> list[Listing]:
    query = query.strip()
    if not query:
        raise ValueError("Enter an MCP search term.")
    raw = json.loads(_get("/api/listing-search?type=mcp&q=" + quote(query)).decode("utf-8"))
    items = raw.get("items", [])
    if not isinstance(items, list):
        raise ValueError("The MCP marketplace returned an invalid list.")
    found = []
    for row in items:
        if not isinstance(row, dict):
            continue
        slug = row.get("slug", "")
        if not isinstance(slug, str) or len(slug.split("/")) != 2 or not all(NAME_RE.fullmatch(part) for part in slug.split("/")):
            continue
        source_repo = row.get("sourceRepo")
        repo_owner = source_repo.split("/", 1)[0] if isinstance(source_repo, str) and "/" in source_repo else ""
        publisher = repo_owner if NAME_RE.fullmatch(repo_owner) else slug.split("/", 1)[0]
        name = row.get("displayName") or row.get("name") or slug.rsplit("/", 1)[-1]
        found.append(Listing(slug, str(name), str(row.get("summary") or ""), publisher,
                             str(row.get("installLabel") or "")))
    return found


def parse_command(command: str) -> tuple[str, dict]:
    """Parse `claude mcp add` syntax without invoking a shell."""
    words = shlex.split(command)
    if words[:3] != ["claude", "mcp", "add"]:
        raise ValueError("The marketplace did not provide a valid `claude mcp add` command.")
    args = words[3:]
    if "--" in args:
        split = args.index("--")
        options, executable = args[:split], args[split + 1:]
    else:
        options, executable = args, []
    transport, url, name = "stdio", "", ""
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    i = 0
    while i < len(options):
        word = options[i]
        if word in ("--transport", "-t", "--env", "-e", "--header", "-H", "--scope", "-s"):
            if i + 1 >= len(options):
                    raise ValueError(f"Missing value for {word}.")
            value = options[i + 1]
            i += 2
            if word in ("--transport", "-t"):
                transport = value.lower()
            elif word in ("--env", "-e"):
                key, sep, val = value.partition("=")
                if not sep or not key:
                    raise ValueError("Invalid MCP environment variable.")
                env[key] = val
            elif word in ("--header", "-H"):
                key, sep, val = value.partition(":")
                if not sep or not key.strip():
                    raise ValueError("Invalid MCP HTTP header.")
                headers[key.strip()] = val.strip()
            continue
        if word.startswith("-"):
            raise ValueError(f"Unsupported MCP option: {word}")
        if not name:
            name = word
        elif not url:
            url = word
        else:
            raise ValueError("The MCP command contains an unrecognized argument.")
        i += 1
    if not NAME_RE.fullmatch(name):
        raise ValueError("Invalid MCP server name.")
    if executable:
        raw = {"command": executable[0], "args": executable[1:], "env": env}
    elif url and transport in ("http", "sse"):
        raw = {"url": url, "transport": transport, "headers": headers}
    else:
        raise ValueError("Could not find an MCP command or URL; enter the configuration manually.")
    spec, _ = normalize_mcp(raw)
    return name, spec


def configuration(listing: Listing) -> tuple[str, dict]:
    page = _get("/mcp/" + quote(listing.slug, safe="/")).decode("utf-8", errors="replace")
    # Next.js serializes this page's server object in an RSC script. Related
    # listings also have install commands, so only inspect the object keyed by
    # the selected slug. Never use the first command found elsewhere on page.
    for match in re.finditer(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', page):
        try:
            chunk = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        marker = '"' + listing.slug + '",{"server":'
        if marker not in chunk:
            continue
        command_match = re.search(r'"installCommand":"((?:\\.|[^"\\])*)"', chunk)
        if command_match:
            command = json.loads('"' + command_match.group(1) + '"')
            if command.startswith("claude mcp add"):
                return parse_command(command)
        break
    # Older pages may render only a visible code block; in that case code text
    # is safe to parse because it is not part of unrelated RSC suggestions.
    for match in re.finditer(r'<code[^>]*>(claude mcp add.*?)</code>', page, re.S):
        try:
            return parse_command(html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip())
        except ValueError:
            continue
    raise ValueError("Could not read MCP installation configuration from this page; enter JSON manually in the dialog.")
