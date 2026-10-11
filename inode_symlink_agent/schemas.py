"""OpenAI-style function-calling schemas for every tool.

Kept deliberately short: these are sent with every LLM request, and small free-tier
token budgets (e.g. 8000 tokens/minute) make every word count.
"""
import copy

_PATH = {"type": "string", "description": "Path; ~, $VARS and desktop/downloads/documents work."}
_REC = {"type": "boolean", "description": "Descend into subdirectories (default true)."}
_DEPTH = {"type": "integer", "minimum": 0, "maximum": 50,
          "description": "Max directory depth (default 8)."}
_DRY = {"type": "boolean", "description": "true = preview only; use only if the user asks "
                                          "to preview."}
_SCAN = {"path": _PATH, "recursive": _REC, "max_depth": _DEPTH}


def _fn(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


SCHEMAS = [
    _fn("find_broken_symlinks",
        "Read-only. List dangling symlinks (target missing) under a directory. Never follows "
        "links; skips /proc /sys /dev; bounded scan.", _SCAN, ["path"]),
    _fn("find_symlink_loops",
        "Read-only. Find symlinks that loop (ELOOP): self-links and cycles like A->B->A.",
        _SCAN, ["path"]),
    _fn("inspect_path",
        "Read-only. lstat details: type, inode, device, hard-link count, size, owner, "
        "permissions; for symlinks also raw and resolved target, broken or not, relative or "
        "absolute.", {"path": _PATH}, ["path"]),
    _fn("find_hard_links",
        "Read-only. Group regular files in the scanned tree sharing a (device, inode) with "
        "link count > 1. Names outside the tree can't be found; names_outside_scan counts them.",
        _SCAN, ["path"]),
    _fn("inode_usage",
        "Read-only. Total/used/free inodes and % used of the filesystem containing path "
        "(like df -i).", {"path": _PATH}, ["path"]),
    _fn("find_suspicious_symlinks",
        "Read-only. Flag symlinks pointing outside a boundary directory (default: the scanned "
        "path), into sensitive places (/etc, /root, ~/.ssh...), or sitting in world-writable "
        "directories; gives reasons.",
        dict(_SCAN, boundary={"type": "string", "description": "Directory links must stay in."}),
        ["path"]),
    _fn("audit_summary",
        "Read-only. One scan: counts of broken links, loops, suspicious links and hard-link "
        "groups, plus a few examples.", _SCAN, ["path"]),
    _fn("delete_broken_symlinks",
        "WRITE. Unlink broken symlinks (paths from find_broken_symlinks). Re-checked at "
        "deletion; files and directories are never removed. The program shows the preview "
        "and asks the user y/N.",
        {"paths": {"type": "array", "items": {"type": "string"}}, "dry_run": _DRY}, ["paths"]),
    _fn("create_symlink",
        "WRITE. Create symlink link_path -> target. Refuses existing paths and system dirs.",
        {"target": {"type": "string"}, "link_path": {"type": "string"}, "dry_run": _DRY},
        ["target", "link_path"]),
    _fn("create_hard_link",
        "WRITE. Hard-link new_path to an existing regular file you own (same filesystem).",
        {"existing_file": {"type": "string"}, "new_path": {"type": "string"},
         "dry_run": _DRY}, ["existing_file", "new_path"]),
    _fn("repoint_symlink",
        "WRITE. Atomically change an existing symlink's target.",
        {"link_path": {"type": "string"}, "new_target": {"type": "string"}, "dry_run": _DRY},
        ["link_path", "new_target"]),
]


def get_tool_schemas():
    """Return a fresh copy of all function-calling schemas."""
    return copy.deepcopy(SCHEMAS)
