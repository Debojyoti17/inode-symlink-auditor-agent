"""Safety helpers: path resolution, protected paths, ownership, confirmation, logging.

Everything here uses only the standard library.
"""
import datetime
import json
import os
import re
import stat
import sys

# Directories we never write inside (and "/" itself).
SYSTEM_PREFIXES = ("/bin", "/boot", "/dev", "/etc", "/proc", "/sbin", "/sys", "/usr", "/var")
_LIB_RE = re.compile(r"^/lib[^/]*(/|$)")

# Friendly names a user may say -> (XDG variable, folder name in the home directory)
FOLDER_ALIASES = {
    "desktop": ("XDG_DESKTOP_DIR", "Desktop"),
    "downloads": ("XDG_DOWNLOAD_DIR", "Downloads"),
    "documents": ("XDG_DOCUMENTS_DIR", "Documents"),
}


def default_log_path():
    override = os.environ.get("AGENT_ACTION_LOG")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".local", "state",
                        "inode_symlink_agent", "actions.log")


def _xdg_dirs(home):
    """Parse ~/.config/user-dirs.dirs (used by Ubuntu for localised folder names)."""
    found = {}
    cfg = os.path.join(home, ".config", "user-dirs.dirs")
    try:
        with open(cfg, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*(XDG_\w+_DIR)="([^"]*)"', line)
                if m:
                    found[m.group(1)] = m.group(2).replace("$HOME", home)
    except OSError:
        pass
    return found


def _home_folder(alias):
    """Return the real path of ~/Desktop etc. if it exists, else None."""
    home = os.path.expanduser("~")
    var, name = FOLDER_ALIASES[alias]
    candidates = []
    xdg = _xdg_dirs(home).get(var)
    if xdg:
        candidates.append(xdg)
    candidates.append(os.path.join(home, name))
    try:  # case-insensitive match, e.g. ~/desktop
        for entry in os.listdir(home):
            if entry.lower() == alias:
                candidates.append(os.path.join(home, entry))
    except OSError:
        pass
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return None


def resolve_user_path(path):
    """Expand ~ and $VARS, map desktop/downloads/documents, return an absolute path.

    The final path component is NOT dereferenced (so a symlink stays a symlink).
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("A path is required.")
    p = path.strip()
    p = re.sub(r"^(my|the)\s+", "", p, flags=re.I)
    p = os.path.expandvars(os.path.expanduser(p))
    home = os.path.expanduser("~")
    # Alias applies to a relative path, or to ~/desktop typed in the wrong case.
    rel = None
    if not os.path.isabs(p):
        rel = p
    elif p.startswith(home.rstrip("/") + "/") and not os.path.lexists(p):
        rel = p[len(home.rstrip("/")) + 1:]
    if rel:
        parts = rel.split("/", 1)
        alias = parts[0].lower()
        if alias in FOLDER_ALIASES:
            folder = _home_folder(alias)
            if folder:
                p = os.path.join(folder, parts[1]) if len(parts) > 1 else folder
    return os.path.abspath(p)


def under(path, base):
    """True if path equals base or lies inside it (purely lexical)."""
    base = base.rstrip("/")
    return base == "" or path == base or path.startswith(base + "/")


def is_system_path(path):
    """True for "/", anything directly in "/", and the protected system trees."""
    p = os.path.abspath(path)
    forms = {p, os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))}
    for c in forms:
        if c == "/" or os.path.dirname(c) == "/":
            return True
        if any(under(c, pre) for pre in SYSTEM_PREFIXES) or _LIB_RE.match(c):
            return True
    return False


def owned_by_me(st):
    return st.st_uid == os.geteuid()


def check_new_entry_allowed(path):
    """Return None if we may create `path`, else a human-readable refusal reason."""
    if is_system_path(path):
        return "refusing to write inside a protected system path"
    parent = os.path.dirname(os.path.abspath(path))
    try:
        pst = os.stat(parent)
    except OSError:
        return "parent directory does not exist or is not accessible: %s" % parent
    if not stat.S_ISDIR(pst.st_mode):
        return "parent is not a directory: %s" % parent
    sticky_public = bool(pst.st_mode & stat.S_IWOTH) and bool(pst.st_mode & stat.S_ISVTX)
    if not owned_by_me(pst) and not sticky_public:
        return "parent directory is not owned by you: %s" % parent
    if not os.access(parent, os.W_OK | os.X_OK):
        return "no write permission in parent directory: %s" % parent
    return None


def check_existing_entry_allowed(path, st):
    """Same as above for modifying/removing an existing entry (st = its lstat)."""
    if is_system_path(path):
        return "refusing to write inside a protected system path"
    if not owned_by_me(st):
        return "not owned by you"
    parent = os.path.dirname(os.path.abspath(path))
    if not os.access(parent, os.W_OK | os.X_OK):
        return "no write permission in parent directory: %s" % parent
    return None


def is_interactive():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def ask_confirmation(preview_text, confirm=None, auto_confirm=False):
    """Decide whether a write may go ahead. Returns (approved, reason).

    Order: auto_confirm=True -> yes; confirm callback -> its answer;
    interactive terminal -> ask y/N; otherwise NO (never write silently).
    """
    if auto_confirm:
        return True, "auto-confirmed by caller"
    if confirm is not None:
        try:
            ok = bool(confirm(preview_text))
        except Exception as exc:  # a broken callback must never allow a write
            return False, "confirm callback failed: %s" % exc
        return ok, "confirmed by callback" if ok else "declined by callback"
    if is_interactive():
        print(preview_text)
        try:
            answer = input("Apply these changes? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        ok = answer in ("y", "yes")
        return ok, "confirmed at prompt" if ok else "declined at prompt"
    return False, ("no confirmation available: not an interactive terminal, and no confirm "
                   "callback or auto_confirm was given")


def log_action(action, details, result):
    """Append one JSON line to the actions log. Logging problems never raise."""
    entry = {
        "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "user": os.geteuid(),
        "action": action,
        "details": details,
        "result": result,
    }
    path = default_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass
