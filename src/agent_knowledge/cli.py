"""`agent-knowledge` (alias `ak`): mở TUI, hoặc dùng subcommand để script hoá."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import __version__
from .catalog import KIND_ALIASES, KIND_LABEL, KINDS, default_home
from .ops import Manager
from .scaffold import init_library
from . import remote

SYMBOL = {"installed": "●", "outdated": "◐", "absent": "○", "external": "◌"}


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--home", help="thư mục library (mặc định ~/.agent-knowledge hoặc $AGENT_KNOWLEDGE_HOME)")
    p.add_argument("--project", help="thư mục project cho scope project (mặc định: thư mục hiện tại)")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(
        prog="agent-knowledge", parents=[common],
        description="Quản lý skills, MCP, instructions cho nhiều agent CLI từ một library duy nhất.",
    )
    ap.add_argument("--version", action="version", version=f"agent-knowledge {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", parents=[common], help="tạo thư mục library")
    sp.add_argument("--examples", action="store_true", help="kèm skill/mcp/instruction mẫu")

    sub.add_parser("agents", parents=[common], help="liệt kê agent + đường dẫn đang dùng")

    sp = sub.add_parser("list", parents=[common], help="liệt kê library và trạng thái cài đặt")
    sp.add_argument("kind", nargs="?", choices=sorted(set(KIND_ALIASES)), help="skill | mcp | instruction")
    sp.add_argument("-a", "--agent", action="append", help="chỉ hiện agent này (lặp được)")
    sp.add_argument("-s", "--scope", choices=("global", "project"), default="global")
    sp.add_argument("--json", action="store_true")

    for name, hlp in (("install", "cài item cho agent"), ("uninstall", "gỡ item khỏi agent")):
        sp = sub.add_parser(name, parents=[common], help=hlp)
        sp.add_argument("items", nargs="+", help="kind:name; install cũng nhận GitHub owner/repo để nhập skills vào library")
        sp.add_argument("-a", "--agent", action="append", help="agent đích (lặp được)")
        sp.add_argument("--all-agents", action="store_true", help="áp dụng cho mọi agent")
        sp.add_argument("-s", "--scope", choices=("global", "project"), default="global")
    return ap


def _manager(args) -> Manager:
    return Manager(default_home(args.home), Path(args.project).expanduser() if args.project else Path.cwd())


def _parse_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        raise SystemExit(f"'{ref}' sai định dạng, cần kind:name (vd skill:commit-helper)")
    k, n = ref.split(":", 1)
    if k not in KIND_ALIASES:
        raise SystemExit(f"kind '{k}' không hợp lệ (skill | mcp | instruction)")
    return KIND_ALIASES[k], n


def _targets(mgr: Manager, args) -> list[str]:
    if args.all_agents:
        return list(mgr.agents)
    if not args.agent:
        raise SystemExit("cần --agent <id> (lặp được) hoặc --all-agents")
    bad = [a for a in args.agent if a not in mgr.agents]
    if bad:
        raise SystemExit(f"agent không tồn tại: {', '.join(bad)}  (có: {', '.join(mgr.agents)})")
    return args.agent


def cmd_agents(mgr: Manager) -> int:
    for a in mgr.agent_list():
        print(f"{'✔' if a.detected() else '·'} {a.id:<7} {'(đã cài)' if a.detected() else '(không thấy binary)'}")
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
    print("\n● installed  ◐ outdated  ○ chưa cài  ◌ external (không có trong library)")
    for e in mgr.lib.errors:
        print(f"! {e}", file=sys.stderr)
    return 0


def cmd_apply(mgr: Manager, args, install: bool) -> int:
    fails = 0
    for aid in _targets(mgr, args):
        for ref in args.items:
            kind, name = _parse_ref(ref)
            r = mgr.install(aid, args.scope, kind, name) if install else mgr.uninstall(aid, args.scope, kind, name)
            print(f"[{aid}] {'✔' if r.ok else '✘'} {r.msg}")
            fails += not r.ok
    return 1 if fails else 0


def cmd_remote(mgr: Manager, repository: str) -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="ak-skills-") as temp:
            print(f"Đang tải {repository}…")
            skills = remote.download(repository, Path(temp) / "repo")
            for i, skill in enumerate(skills, 1):
                tag = " (đã có)" if remote.exists(mgr.home, skill.name) else ""
                print(f"{i}. {skill.name}{tag} — {skill.description}")
            while True:
                answer = input("Chọn số cách nhau bằng dấu phẩy (vd 1,3), all = tất cả; Enter = huỷ: ").strip()
                if not answer:
                    return 0
                try:
                    indices = list(range(len(skills))) if answer.lower() == "all" else list(dict.fromkeys(
                        int(v.strip()) - 1 for v in answer.split(",")))
                    if any(i < 0 or i >= len(skills) for i in indices):
                        raise ValueError
                    break
                except ValueError:
                    print("Lựa chọn không hợp lệ.")
            for i in indices:
                skill = skills[i]
                overwrite = remote.exists(mgr.home, skill.name)
                if overwrite and input(f"Ghi đè {skill.name}? Chỉnh sửa cũ sẽ mất. [y/N]: ").strip().lower() != "y":
                    print(f"Bỏ qua {skill.name}")
                    continue
                remote.install(mgr.home, skill, overwrite=overwrite)
                print(f"✔ {mgr.home / 'skills' / skill.name}")
        return 0
    except (ValueError, OSError) as exc:
        print(f"✘ {exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("Đã huỷ: cần nhập lựa chọn và xác nhận trong terminal.", file=sys.stderr)
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
            print("  (đã tồn tại, không có gì thay đổi)")
        return 0
    mgr = _manager(args)
    if args.cmd == "agents":
        return cmd_agents(mgr)
    if args.cmd == "list":
        return cmd_list(mgr, args)
    if args.cmd in ("install", "uninstall"):
        if args.cmd == "install" and any("/" in item for item in args.items):
            if len(args.items) != 1 or args.agent or args.all_agents or args.scope != "global":
                raise SystemExit("Dùng ak install owner/repo để nhập vào library; cài cho agent bằng kind:name riêng.")
            return cmd_remote(mgr, args.items[0])
        return cmd_apply(mgr, args, args.cmd == "install")
    from .tui import run
    run(mgr)
    return 0


def entry() -> int:
    try:
        return main()
    except BrokenPipeError:               # vd `ak list | head`
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(entry())
