"""OpenAI-style function-calling schemas for every tool."""
import copy

_PATH = {"type": "string", "description": "Directory or file path. Accepts ~, $VARS and the "
         "words desktop / downloads / documents (mapped to the home folders)."}
_REC = {"type": "boolean", "description": "Descend into sub-directories (default true).",
        "default": True}
_DEPTH = {"type": "integer", "minimum": 0, "maximum": 50,
          "description": "Maximum directory depth below the path (default 8)."}
_MAXE = {"type": "integer", "minimum": 1, "maximum": 500000,
         "description": "Maximum number of entries to examine (default 50000)."}
_DRY = {"type": "boolean", "default": False,
        "description": "If true, only preview what would change and write nothing. Call with "
                       "true first, then false; the user is asked to confirm real writes."}


def _fn(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


SCHEMAS = [
    _fn("find_broken_symlinks",
        "Read-only. Find dangling (broken) symbolic links under a directory: links whose target "
        "no longer exists. Never follows symlinks while walking; skips /proc, /sys, /dev; "
        "bounded by depth and entry limits. Returns each link's path, raw target and resolved "
        "target. Use this before deleting anything.",
        {"path": _PATH, "recursive": _REC, "max_depth": _DEPTH, "max_entries": _MAXE}, ["path"]),
    _fn("find_symlink_loops",
        "Read-only. Find symlinks whose resolution loops (ELOOP): self-links (a -> a) and cycles "
        "such as A -> B -> A. Reports the chain for each loop.",
        {"path": _PATH, "recursive": _REC, "max_depth": _DEPTH, "max_entries": _MAXE}, ["path"]),
    _fn("inspect_path",
        "Read-only. Show details of one path using lstat: file type, inode number, device, "
        "hard-link count, size, owner, permissions. For symlinks also the raw target, the "
        "resolved absolute target, whether it is broken, and relative vs absolute.",
        {"path": _PATH}, ["path"]),
    _fn("find_hard_links",
        "Read-only. Group regular files in the scanned tree that share the same (device, inode) "
        "and have link count > 1, i.e. hard links to the same data. Only names inside the "
        "scanned tree are found; other names elsewhere on the filesystem need a wider scan "
        "(the result reports how many names are outside).",
        {"path": _PATH, "recursive": _REC, "max_depth": _DEPTH, "max_entries": _MAXE}, ["path"]),
    _fn("inode_usage",
        "Read-only. Total/used/free inodes and percent used for the filesystem containing the "
        "path (like `df -i`).",
        {"path": _PATH}, ["path"]),
    _fn("find_suspicious_symlinks",
        "Read-only. Flag symlinks that point outside a boundary directory (default: the scanned "
        "directory), point into sensitive places (/etc, /root, ~/.ssh, ...), or sit inside "
        "world-writable directories. Each result says why it was flagged.",
        dict({"path": _PATH}, boundary={"type": "string", "description":
              "Directory links must stay inside. Defaults to the scanned path."},
             recursive=_REC, max_depth=_DEPTH, max_entries=_MAXE), ["path"]),
    _fn("audit_summary",
        "Read-only. One scan returning counts of every issue type (broken symlinks, loops, "
        "suspicious links, hard-link groups) plus a few examples. Use for 'audit my projects "
        "folder' style requests.",
        {"path": _PATH, "recursive": _REC, "max_depth": _DEPTH, "max_entries": _MAXE}, ["path"]),
    _fn("delete_broken_symlinks",
        "WRITE. Unlink broken symlinks. Each path is re-checked right before deletion and is "
        "only removed if it is still a symlink and still broken; regular files and directories "
        "are never deleted. Pass paths exactly as returned by find_broken_symlinks. Always call "
        "with dry_run=true first to preview; the user then confirms the real run.",
        {"paths": {"type": "array", "items": {"type": "string"},
                   "description": "Symlink paths to delete."}, "dry_run": _DRY}, ["paths"]),
    _fn("create_symlink",
        "WRITE. Create a symbolic link at link_path pointing to target. Refuses if link_path "
        "exists or is in a protected system path. Preview with dry_run=true first.",
        {"target": {"type": "string", "description": "What the link should point to."},
         "link_path": {"type": "string", "description": "Where to create the link."},
         "dry_run": _DRY}, ["target", "link_path"]),
    _fn("create_hard_link",
        "WRITE. Create a hard link new_path to an existing regular file (same filesystem, owned "
        "by the user). Preview with dry_run=true first.",
        {"existing_file": {"type": "string", "description": "Existing regular file."},
         "new_path": {"type": "string", "description": "New name for the same inode."},
         "dry_run": _DRY}, ["existing_file", "new_path"]),
    _fn("repoint_symlink",
        "WRITE. Change where an existing symlink points, atomically (temporary link + rename), "
        "so the link is never missing. Preview with dry_run=true first.",
        {"link_path": {"type": "string", "description": "Existing symlink to fix."},
         "new_target": {"type": "string", "description": "New target for the link."},
         "dry_run": _DRY}, ["link_path", "new_target"]),
]


def get_tool_schemas():
    """Return a fresh copy of all function-calling schemas."""
    return copy.deepcopy(SCHEMAS)
