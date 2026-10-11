"""Command line: natural-language mode and direct (no LLM) mode."""
import argparse
import sys

from . import agent, report, safety, tools
from .llm import ChatClient, LLMNotConfigured

COMMANDS = ("scan", "broken", "loops", "hardlinks", "inodes", "suspicious", "inspect",
            "delete-broken")
_VALUE_OPTS = ("--boundary", "--max-depth", "--max-entries")

USAGE = """Inode & Symlink Auditor (Agent 10)

Chat mode (needs an LLM key, see README):
  python3 -m inode_symlink_agent             (type 'exit' to quit)

Natural language, one request (needs an LLM key, see README):
  python3 -m inode_symlink_agent "find broken shortcuts on my desktop and delete them"

Direct mode (no LLM needed):
  python3 -m inode_symlink_agent scan|broken|loops|hardlinks|inodes|inspect|delete-broken PATH
  python3 -m inode_symlink_agent suspicious PATH [--boundary DIR]

Options: --json  --yes  --verbose  --no-recursive  --max-depth N  --max-entries N
"""


def _is_direct(argv):
    if not argv or argv[0] not in COMMANDS:
        return False
    positionals, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in _VALUE_OPTS:
            skip = True
        elif not a.startswith("-"):
            positionals.append(a)
    return len(positionals) == 2


def _build_parser():
    p = argparse.ArgumentParser(prog="inode_symlink_agent", epilog=USAGE,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                description="Inode & Symlink Auditor (Agent 10)")
    p.add_argument("words", nargs="*", help="a COMMAND and PATH, or a plain-English request")
    p.add_argument("--json", action="store_true", help="print JSON instead of a report")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="print approximate tokens sent per LLM request")
    p.add_argument("--yes", "-y", action="store_true", help="auto-confirm writes")
    p.add_argument("--boundary", help="suspicious: directory links must stay inside")
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--max-depth", type=int)
    p.add_argument("--max-entries", type=int)
    return p


def _scan_kwargs(a):
    return {"recursive": not a.no_recursive, "max_depth": a.max_depth,
            "max_entries": a.max_entries}


def _direct(a):
    cmd, path = a.words
    kw = _scan_kwargs(a)
    if cmd == "inodes":
        name, res = "inode_usage", tools.inode_usage(path)
    elif cmd == "inspect":
        name, res = "inspect_path", tools.inspect_path(path)
    elif cmd == "suspicious":
        name = "find_suspicious_symlinks"
        res = tools.find_suspicious_symlinks(path, boundary=a.boundary, **kw)
    elif cmd == "delete-broken":
        return _delete_broken(a, path, kw)
    else:
        name = {"scan": "audit_summary", "broken": "find_broken_symlinks",
                "loops": "find_symlink_loops", "hardlinks": "find_hard_links"}[cmd]
        res = tools.TOOLS[name](path, **kw)
    print(report.format_result(name, res, a.json))
    return 0 if res.get("ok") else 1


def _delete_broken(a, path, kw):
    try:
        resolved = safety.resolve_user_path(path)
    except ValueError as exc:
        print("Error: %s" % exc)
        return 1
    import os
    if os.path.islink(resolved):
        found, paths = None, [resolved]
    else:
        found = tools.find_broken_symlinks(path, **kw)
        if not found["ok"]:
            print(report.format_result("find_broken_symlinks", found, a.json))
            return 1
        paths = [i["path"] for i in found["broken_symlinks"]]
        if found["count"] > len(paths):
            print("Too many broken links (%d); only the first %d are handled per run."
                  % (found["count"], len(paths)))
    if not paths:
        print("No broken symlinks found under %s." % resolved)
        return 0
    res = tools.delete_broken_symlinks(paths, auto_confirm=a.yes)
    if res["status"] == "not_confirmed" and not safety.is_interactive() and not a.yes:
        res["message"] += " Re-run with --yes to delete."
    print(report.format_result("delete_broken_symlinks", res, a.json))
    return 0 if res.get("ok") or res["status"] in ("dry_run", "not_confirmed") else 1


def _trace(name, args):
    print("[tool] %s %s" % (name, args), file=sys.stderr)


def run_chat(client=None, input_fn=input, auto_confirm=False, verbose=False):
    """Interactive loop. Conversation history is kept between turns; every write still
    goes through the y/N confirmation gate."""
    if client is None:
        try:
            client = ChatClient.from_env(verbose=verbose)
        except LLMNotConfigured as exc:
            print(exc)
            return 1
    try:
        import readline  # noqa: F401  (gives arrow keys / history to input())
    except ImportError:
        pass
    print("Inode & Symlink Auditor (Agent 10). Ask in plain English, e.g. 'find broken "
          "shortcuts in ~/Desktop'.\nType 'exit' or press Ctrl+D to quit.")
    history = agent.new_history()
    while True:
        try:
            line = input_fn("agent10> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("exit", "quit", ":q"):
            return 0
        try:
            print(agent.handle(line, client=client, auto_confirm=auto_confirm,
                               on_tool_call=_trace, history=history))
        except KeyboardInterrupt:
            print("\n(interrupted)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    a = _build_parser().parse_args(argv)
    if not a.words:
        return run_chat(auto_confirm=a.yes, verbose=a.verbose)
    if _is_direct(argv):
        return _direct(a)
    prompt = " ".join(a.words)
    print(agent.handle(prompt, auto_confirm=a.yes, on_tool_call=_trace, verbose=a.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
