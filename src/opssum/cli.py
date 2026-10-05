"""`opssum`: launch the TUI or use scriptable subcommands."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import __version__
from .catalog import KIND_ALIASES, KIND_LABEL, KINDS, default_home
from .ops import Manager, OpsError
from .scaffold import init_library
from . import remote

SYMBOL = {"installed": "●", "outdated": "◐", "absent": "○", "external": "◌"}


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--home", help="library directory (default: ~/.opssum or $OPSSUM_HOME)")
    p.add_argument("--project", help="project directory for project scope (default: current directory)")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(
        prog="opssum", parents=[common],
        description="Manage skills, MCP servers, and instructions for multiple agent CLIs from one library.",
    )
    ap.add_argument("--version", action="version", version=f"opssum {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", parents=[common], help="create the library directory")
    sp.add_argument("--examples", action="store_true", help="include sample skills, MCP servers, and instructions")

    sub.add_parser("agents", parents=[common], help="list agents and their active paths")

    sp = sub.add_parser("list", parents=[common], help="list library items and installation status")
    sp.add_argument("kind", nargs="?", choices=sorted(set(KIND_ALIASES)), help="skill | mcp | instruction")
    sp.add_argument("-a", "--agent", action="append", help="show only this agent (repeatable)")
    sp.add_argument("-s", "--scope", choices=("global", "project"), default="global")
    sp.add_argument("--json", action="store_true")

    for name, hlp in (("install", "install an item for an agent"), ("uninstall", "uninstall an item from an agent")):
        sp = sub.add_parser(name, parents=[common], help=hlp)
        sp.add_argument("items", nargs="+", help="kind:name; install also accepts a GitHub owner/repo to import skills")
        sp.add_argument("-a", "--agent", action="append", help="target agent (repeatable)")
        sp.add_argument("--all-agents", action="store_true", help="apply to every agent")
        sp.add_argument("-s", "--scope", choices=("global", "project"), default="global")
        if name == "uninstall":
            sp.add_argument("--library", action="store_true", help="remove a skill/MCP from the library and all recorded agents")
    return ap


def _manager(args) -> Manager:
    return Manager(default_home(args.home), Path(args.project).expanduser() if args.project else Path.cwd())


def _parse_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        raise SystemExit(f"'{ref}' has an invalid format; expected kind:name (for example skill:commit-helper)")
    k, n = ref.split(":", 1)
    if k not in KIND_ALIASES:
        raise SystemExit(f"invalid kind '{k}' (expected skill | mcp | instruction)")
    return KIND_ALIASES[k], n


def _targets(mgr: Manager, args) -> list[str]:
    if args.all_agents:
        return list(mgr.agents)
    if not args.agent:
        raise SystemExit("provide --agent <id> (repeatable) or --all-agents")
    bad = [a for a in args.agent if a not in mgr.agents]
    if bad:
        raise SystemExit(f"unknown agent: {', '.join(bad)}  (available: {', '.join(mgr.agents)})")
    return args.agent


def cmd_agents(mgr: Manager) -> int:
    for a in mgr.agent_list():
        print(f"{'✔' if a.detected() else '·'} {a.id:<7} {'(installed)' if a.detected() else '(binary not found)'}")
        for scope in ("global", "project"):
            for what, lab in (("skills", "skills"), ("instr", "instr "), ("mcp", "mcp   ")):
                p = a.resolve(what, scope, mgr.project)
                print(f"    {scope:<7} {lab}  {p if p else '—'}")
        if a.notes:
            print(f"    note: {a.notes}")
    return 0


def cmd_list(mgr: Manager, args) -> int:
    kinds = [KIND_ALIASES[args.kind]] if args.kind else list(KINDS)
    agents = args.agent or list(mgr.agents)
    data: dict = {}
    for kind in kinds:
        per = {a: mgr.states(a, args.scope, kind) for a in agents}
        names = sorted({i.name for i in mgr.lib.items(kind)} | {n for s in per.values() for n in s})
        rows = []
        for n in names:
            it = mgr.lib.get(kind, n)
            rows.append({"name": n, "in_library": it is not None, "description": it.description if it else "",
                         "agents": {a: per[a][n].status if n in per[a] else "absent" for a in agents}})
        data[kind] = rows
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print(f"library: {mgr.home}   scope: {args.scope}" + (f" ({mgr.project})" if args.scope == "project" else ""))
    for kind, rows in data.items():
        print(f"\n{KIND_LABEL[kind]} ({sum(r['in_library'] for r in rows)})")
        print("  " + " " * 28 + " ".join(f"{a:^7}" for a in agents))
        for r in rows:
            cells = " ".join(f"{SYMBOL[r['agents'][a]]:^7}" for a in agents)
            tag = "" if r["in_library"] else "  (external)"
            print(f"  {r['name']:<28}{cells}{tag}")
    print("\n● installed  ◐ outdated  ○ not installed  ◌ external (not in library)")
    for e in mgr.lib.errors:
        print(f"! {e}", file=sys.stderr)
    return 0


def cmd_apply(mgr: Manager, args, install: bool) -> int:
    fails = 0
    for aid in _targets(mgr, args):
        for ref in args.items:
            kind, name = _parse_ref(ref)
            replace = False
            if install and kind == "skill" and (conflict := mgr.skill_conflict(aid, args.scope, name)):
                try:
                    replace = input(f"{aid} is using {conflict}. Replace it with {name}? [y/N]: ").strip().lower() == "y"
                except EOFError:
                    replace = False
                if not replace:
                    print(f"[{aid}] Skipping {name}")
                    continue
            if install and kind == "mcp":
                state = mgr.states(aid, args.scope, "mcp").get(name)
                conflict = mgr.mcp_conflict(aid, args.scope, name)
                if conflict or (state and state.status == "outdated"):
                    try:
                        replace = input(f"{aid} already has MCP {conflict or name}. Replace its configuration with the library version? [y/N]: ").strip().lower() == "y"
                    except EOFError:
                        replace = False
                    if not replace:
                        print(f"[{aid}] Skipping {name}")
                        continue
            r = mgr.install(aid, args.scope, kind, name, replace=replace) if install else mgr.uninstall(aid, args.scope, kind, name)
            print(f"[{aid}] {'✔' if r.ok else '✘'} {r.msg}")
            fails += not r.ok
    return 1 if fails else 0


def cmd_remove(mgr: Manager, args) -> int:
    if args.agent or args.all_agents:
        raise SystemExit("--library removes all recorded links; do not combine it with --agent/--all-agents.")
    names = []
    for ref in args.items:
        kind, name = _parse_ref(ref)
        if kind not in ("skill", "mcp"):
            raise SystemExit("--library supports skills and MCP servers only.")
        names.append((kind, name))
    fails = 0
    for kind, name in names:
        try:
            paths = mgr.skill_dependents(name) if kind == "skill" else mgr.mcp_dependents(name)
            print(f"Remove {name} from the library and uninstall it from {len(paths)} agent locations:")
            for path in paths:
                print(f"  {path if kind == 'skill' else path[0]}")
            if input("A recovery copy will be saved in library/.trash. Continue? [y/N]: ").strip().lower() != "y":
                continue
            result = mgr.remove_skill(name) if kind == "skill" else mgr.remove_mcp(name)
            print(result.msg)
            fails += not result.ok
        except EOFError:
            return 1
        except (OSError, ValueError, OpsError) as exc:
            print(str(exc), file=sys.stderr)
            fails += 1
    return int(bool(fails))


def cmd_remote(mgr: Manager, repository: str) -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="opssum-skills-") as temp:
            print(f"Downloading {repository}…")
            skills = remote.download(repository, Path(temp) / "repo")
            for i, skill in enumerate(skills, 1):
                tag = " (already in library)" if remote.exists(mgr.home, skill) else ""
                print(f"{i}. {skill.reference}{tag} — {skill.description}")
            while True:
                answer = input("Enter comma-separated numbers (for example 1,3), all = everything; Enter = cancel: ").strip()
                if not answer:
                    return 0
                try:
                    indices = list(range(len(skills))) if answer.lower() == "all" else list(dict.fromkeys(
                        int(v.strip()) - 1 for v in answer.split(",")))
                    if any(i < 0 or i >= len(skills) for i in indices):
                        raise ValueError
                    break
                except ValueError:
                    print("Invalid selection.")
            for i in indices:
                skill = skills[i]
                overwrite = remote.exists(mgr.home, skill)
                if overwrite and input(f"Overwrite {skill.name}? Existing edits will be lost. [y/N]: ").strip().lower() != "y":
                    print(f"Skipping {skill.name}")
                    continue
                remote.install(mgr.home, skill, overwrite=overwrite)
                print(f"✔ {mgr.home / 'skills' / skill.reference}")
        return 0
    except (ValueError, OSError) as exc:
        print(f"✘ {exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("Cancelled: selection and confirmation are required in a terminal.", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = default_home(args.home)
    if args.cmd == "init":
        created = init_library(home, args.examples)
        print(f"Library: {home}")
        for c in created:
            print(f"  + {c}")
        if not created:
            print("  (already exists; nothing changed)")
        return 0
    mgr = _manager(args)
    if args.cmd == "agents":
        return cmd_agents(mgr)
    if args.cmd == "list":
        return cmd_list(mgr, args)
    if args.cmd in ("install", "uninstall"):
        if args.cmd == "uninstall" and args.library:
            return cmd_remove(mgr, args)
        if args.cmd == "install" and any("/" in item and ":" not in item for item in args.items):
            if len(args.items) != 1 or args.agent or args.all_agents or args.scope != "global":
                raise SystemExit("Use opssum install owner/repo to import into the library; install for agents with kind:name references.")
            return cmd_remote(mgr, args.items[0])
        return cmd_apply(mgr, args, args.cmd == "install")
    from .tui import run
    run(mgr)
    return 0


def entry() -> int:
    try:
        return main()
    except BrokenPipeError:               # vd `opssum list | head`
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(entry())
