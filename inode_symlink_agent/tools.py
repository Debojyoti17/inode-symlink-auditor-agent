"""The agent's tools: plain functions returning JSON-serialisable dicts.

Read-only tools use os.scandir / os.lstat / os.readlink / os.statvfs and NEVER follow
symlinks while walking. Write tools go through a dry-run / confirmation gate.
"""
import errno
import grp
import os
import pwd
import stat
import time
import uuid

from . import safety

DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_ENTRIES = 50000
DEFAULT_MAX_SECONDS = 30
HARD_MAX_DEPTH = 50
HARD_MAX_ENTRIES = 500000
MAX_LIST = 200                       # longest list returned from one tool call
EXCLUDED_DIRS = ("/proc", "/sys", "/dev")


# --------------------------------------------------------------------------- helpers
def _err(message):
    return {"ok": False, "error": message}


def _clamp(value, default, low, high):
    if value is None:
        return default
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _cap(items):
    return items[:MAX_LIST], len(items) > MAX_LIST


def _excluded(path):
    return any(safety.under(path, d) for d in EXCLUDED_DIRS)


def _type_name(mode):
    for test, name in ((stat.S_ISLNK, "symlink"), (stat.S_ISDIR, "directory"),
                       (stat.S_ISREG, "regular file"), (stat.S_ISBLK, "block device"),
                       (stat.S_ISCHR, "character device"), (stat.S_ISFIFO, "fifo"),
                       (stat.S_ISSOCK, "socket")):
        if test(mode):
            return name
    return "unknown"


def _sensitive_prefixes():
    home = os.path.expanduser("~")
    fixed = ["/etc", "/root", "/boot", "/proc", "/sys", "/dev", "/var/log", "/var/lib"]
    homes = [".ssh", ".gnupg", ".aws", ".kube", ".netrc", ".bash_history", ".config/gcloud"]
    return fixed + [os.path.join(home, h) for h in homes]


def link_info(path):
    """Classify one symlink. status: ok | broken | loop | unknown."""
    try:
        target = os.readlink(path)
    except OSError as exc:
        return {"path": path, "status": "unknown", "error": exc.strerror}
    parent = os.path.realpath(os.path.dirname(path))
    info = {
        "path": path,
        "target": target,
        "target_type": "absolute" if os.path.isabs(target) else "relative",
        "resolved_target": os.path.normpath(os.path.join(parent, target)),
        "status": "ok",
        "points_to": None,
    }
    try:
        info["points_to"] = _type_name(os.stat(path).st_mode)
        info["resolved_target"] = os.path.realpath(path)
    except (FileNotFoundError, NotADirectoryError):
        info["status"] = "broken"
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            info["status"] = "loop"
        else:
            info["status"] = "unknown"
            info["error"] = exc.strerror
    return info


def _trace_chain(path, limit=40):
    """Follow readlink hops by hand to describe a loop: returns (kind, chain)."""
    chain, seen, cur = [], set(), path
    while len(chain) < limit:
        if cur in seen:
            chain.append(cur)
            return ("self-link" if len(chain) == 2 else "cycle"), chain
        seen.add(cur)
        chain.append(cur)
        try:
            target = os.readlink(cur)
        except OSError:
            break
        cur = os.path.normpath(os.path.join(os.path.dirname(cur), target))
    return "cycle", chain


def _suspicion(info, dir_flags, boundary_real):
    reasons = []
    severity = "low"
    real = os.path.realpath(info["path"])
    lexical = info.get("resolved_target", "")
    for pre in _sensitive_prefixes():
        if safety.under(real, pre) or safety.under(lexical, pre):
            reasons.append("points into a sensitive location (%s)" % pre)
            severity = "high"
            break
    if not (safety.under(real, boundary_real) and safety.under(lexical, boundary_real)):
        reasons.append("points outside the boundary directory %s" % boundary_real)
        if severity == "low":
            severity = "medium"
    if dir_flags["world_writable"]:
        if dir_flags["sticky"]:
            reasons.append("lives in a world-writable directory (sticky bit set, so only "
                           "low risk)")
        else:
            reasons.append("lives in a world-writable directory without the sticky bit: "
                           "anyone could swap or replace it")
            if severity == "low":
                severity = "medium"
    return reasons, severity


# --------------------------------------------------------------------------- scanner
def _scan(path, recursive=True, max_depth=None, max_entries=None, want=(),
          boundary=None, max_seconds=DEFAULT_MAX_SECONDS):
    """One bounded walk that collects whatever `want` asks for.

    want may contain: broken, loops, hardlinks, suspicious.
    """
    try:
        root = safety.resolve_user_path(path)
    except ValueError as exc:
        return _err(str(exc))
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return _err("Path does not exist: %s" % root)
    except OSError as exc:
        return _err("Cannot access %s: %s" % (root, exc.strerror))
    resolved_from = None
    if stat.S_ISLNK(st.st_mode):
        real = os.path.realpath(root)
        if not os.path.isdir(real):
            return _err("%s is a symlink that does not lead to a directory; "
                        "use inspect_path on it instead." % root)
        resolved_from, root = root, real
    elif not stat.S_ISDIR(st.st_mode):
        return _err("%s is not a directory; use inspect_path on it instead." % root)
    if _excluded(root):
        return _err("Refusing to scan %s (/proc, /sys and /dev are always skipped)." % root)

    max_depth = 0 if not recursive else _clamp(max_depth, DEFAULT_MAX_DEPTH, 0, HARD_MAX_DEPTH)
    max_entries = _clamp(max_entries, DEFAULT_MAX_ENTRIES, 1, HARD_MAX_ENTRIES)
    boundary_real = None
    if "suspicious" in want:
        try:
            boundary_real = os.path.realpath(safety.resolve_user_path(boundary)) \
                if boundary else os.path.realpath(root)
        except ValueError as exc:
            return _err(str(exc))

    out = {"broken": [], "loops": [], "suspicious": [], "hard_groups": {}}
    stats = {"entries_scanned": 0, "dirs_scanned": 0, "files": 0, "symlinks": 0,
             "symlinks_ok": 0, "depth_limited_dirs": 0, "skipped": [],
             "truncated": False, "truncated_reason": None,
             "max_depth": max_depth, "max_entries": max_entries}
    deadline = time.monotonic() + max_seconds
    stack = [(root, 0)]
    stop = False
    while stack and not stop:
        d, depth = stack.pop()
        try:
            dst = os.lstat(d)
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            stats["skipped"].append({"path": d, "reason": exc.strerror or str(exc)})
            continue
        stats["dirs_scanned"] += 1
        flags = {"world_writable": bool(dst.st_mode & stat.S_IWOTH),
                 "sticky": bool(dst.st_mode & stat.S_ISVTX)}
        for entry in entries:
            if stats["entries_scanned"] >= max_entries:
                stats["truncated"], stats["truncated_reason"] = True, "max_entries reached"
                stop = True
                break
            if time.monotonic() > deadline:
                stats["truncated"], stats["truncated_reason"] = True, "time limit reached"
                stop = True
                break
            stats["entries_scanned"] += 1
            try:
                est = entry.stat(follow_symlinks=False)
            except OSError as exc:
                stats["skipped"].append({"path": entry.path, "reason": exc.strerror or str(exc)})
                continue
            mode = est.st_mode
            if stat.S_ISLNK(mode):
                stats["symlinks"] += 1
                info = link_info(entry.path)
                if info["status"] == "ok":
                    stats["symlinks_ok"] += 1
                elif info["status"] == "broken" and "broken" in want:
                    out["broken"].append(info)
                elif info["status"] == "loop" and "loops" in want:
                    kind, chain = _trace_chain(entry.path)
                    info["loop_kind"], info["chain"] = kind, chain
                    out["loops"].append(info)
                elif info["status"] == "unknown":
                    stats["skipped"].append({"path": entry.path,
                                             "reason": info.get("error", "cannot resolve")})
                if "suspicious" in want:
                    reasons, severity = _suspicion(info, flags, boundary_real)
                    if reasons:
                        out["suspicious"].append({
                            "path": entry.path, "target": info.get("target"),
                            "resolved_target": info.get("resolved_target"),
                            "severity": severity, "reasons": reasons})
            elif stat.S_ISDIR(mode):
                if _excluded(entry.path):
                    stats["skipped"].append({"path": entry.path,
                                             "reason": "excluded system directory"})
                elif depth < max_depth:
                    stack.append((entry.path, depth + 1))
                elif recursive:
                    stats["depth_limited_dirs"] += 1
            elif stat.S_ISREG(mode):
                stats["files"] += 1
                if "hardlinks" in want and est.st_nlink > 1:
                    grp_ = out["hard_groups"].setdefault(
                        (est.st_dev, est.st_ino),
                        {"device": est.st_dev, "inode": est.st_ino,
                         "link_count": est.st_nlink, "size": est.st_size, "paths": []})
                    grp_["paths"].append(entry.path)
    for key in ("broken", "loops", "suspicious"):
        out[key].sort(key=lambda r: r["path"])
    stats["skipped_count"] = len(stats["skipped"])
    stats["skipped"], _ = _cap(stats["skipped"])
    return {"ok": True, "root": root, "root_was_symlink": resolved_from,
            "boundary": boundary_real, "stats": stats, "found": out}


def _scan_view(scan):
    s = dict(scan["stats"])
    return {k: s[k] for k in ("entries_scanned", "dirs_scanned", "skipped_count", "skipped",
                              "truncated", "truncated_reason", "max_depth", "max_entries",
                              "depth_limited_dirs")}


def _hard_groups(found):
    groups = []
    for g in found["hard_groups"].values():
        g["paths"].sort()
        g["names_found_in_scan"] = len(g["paths"])
        g["names_outside_scan"] = max(0, g["link_count"] - len(g["paths"]))
        groups.append(g)
    groups.sort(key=lambda g: (-g["names_found_in_scan"], g["paths"][0]))
    return groups


HARDLINK_NOTE = ("Only names inside the scanned tree are found; names_outside_scan counts other "
                 "names elsewhere on the filesystem (a wider scan would be needed to find them).")


# --------------------------------------------------------------------------- read-only tools
def find_broken_symlinks(path, recursive=True, max_depth=None, max_entries=None):
    scan = _scan(path, recursive, max_depth, max_entries, want={"broken"})
    if not scan["ok"]:
        return scan
    items, cut = _cap([{k: i[k] for k in ("path", "target", "target_type", "resolved_target")}
                       for i in scan["found"]["broken"]])
    return {"ok": True, "path": scan["root"], "count": len(scan["found"]["broken"]),
            "broken_symlinks": items, "list_truncated": cut, "scan": _scan_view(scan)}


def find_symlink_loops(path, recursive=True, max_depth=None, max_entries=None):
    scan = _scan(path, recursive, max_depth, max_entries, want={"loops"})
    if not scan["ok"]:
        return scan
    items, cut = _cap([{k: i[k] for k in ("path", "target", "loop_kind", "chain")}
                       for i in scan["found"]["loops"]])
    return {"ok": True, "path": scan["root"], "count": len(scan["found"]["loops"]),
            "loops": items, "list_truncated": cut, "scan": _scan_view(scan)}


def find_hard_links(path, recursive=True, max_depth=None, max_entries=None):
    scan = _scan(path, recursive, max_depth, max_entries, want={"hardlinks"})
    if not scan["ok"]:
        return scan
    groups = _hard_groups(scan["found"])
    items, cut = _cap(groups)
    return {"ok": True, "path": scan["root"], "group_count": len(groups),
            "groups_with_multiple_names_in_scan": sum(1 for g in groups
                                                      if g["names_found_in_scan"] > 1),
            "groups": items, "list_truncated": cut, "note": HARDLINK_NOTE,
            "scan": _scan_view(scan)}


def find_suspicious_symlinks(path, boundary=None, recursive=True, max_depth=None,
                             max_entries=None):
    scan = _scan(path, recursive, max_depth, max_entries, want={"suspicious"},
                 boundary=boundary)
    if not scan["ok"]:
        return scan
    items, cut = _cap(scan["found"]["suspicious"])
    return {"ok": True, "path": scan["root"], "boundary": scan["boundary"],
            "count": len(scan["found"]["suspicious"]), "suspicious_symlinks": items,
            "list_truncated": cut, "scan": _scan_view(scan)}


def audit_summary(path, recursive=True, max_depth=None, max_entries=None):
    scan = _scan(path, recursive, max_depth, max_entries,
                 want={"broken", "loops", "hardlinks", "suspicious"})
    if not scan["ok"]:
        return scan
    f, s = scan["found"], scan["stats"]
    groups = _hard_groups(f)

    def sample(rows, keys):
        return [{k: r[k] for k in keys} for r in rows[:5]]

    return {
        "ok": True, "path": scan["root"],
        "counts": {
            "files": s["files"], "directories_scanned": s["dirs_scanned"],
            "symlinks": s["symlinks"], "healthy_symlinks": s["symlinks_ok"],
            "broken_symlinks": len(f["broken"]), "symlink_loops": len(f["loops"]),
            "suspicious_symlinks": len(f["suspicious"]),
            "hard_link_groups": len(groups),
            "hard_link_groups_with_multiple_names_in_scan":
                sum(1 for g in groups if g["names_found_in_scan"] > 1),
        },
        "examples": {
            "broken_symlinks": sample(f["broken"], ("path", "target")),
            "symlink_loops": sample(f["loops"], ("path", "loop_kind")),
            "suspicious_symlinks": sample(f["suspicious"], ("path", "severity", "reasons")),
            "hard_link_groups": sample(groups, ("inode", "link_count", "paths")),
        },
        "hard_link_note": HARDLINK_NOTE,
        "scan": _scan_view(scan),
    }


def inspect_path(path):
    try:
        p = safety.resolve_user_path(path)
        st = os.lstat(p)
    except ValueError as exc:
        return _err(str(exc))
    except FileNotFoundError:
        return {"ok": False, "exists": False, "error": "Path does not exist: %s" % p}
    except OSError as exc:
        return _err("Cannot access %s: %s" % (p, exc.strerror))
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    res = {
        "ok": True, "path": p, "type": _type_name(st.st_mode), "inode": st.st_ino,
        "device": st.st_dev, "hard_link_count": st.st_nlink, "size_bytes": st.st_size,
        "owner": owner, "uid": st.st_uid, "group": group, "gid": st.st_gid,
        "permissions": stat.filemode(st.st_mode), "mode_octal": oct(stat.S_IMODE(st.st_mode)),
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        "owned_by_me": safety.owned_by_me(st),
    }
    if stat.S_ISLNK(st.st_mode):
        info = link_info(p)
        res.update({"target": info.get("target"), "target_type": info.get("target_type"),
                    "resolved_target": info.get("resolved_target"),
                    "link_status": info["status"], "broken": info["status"] == "broken",
                    "points_to": info.get("points_to")})
    return res


def inode_usage(path):
    try:
        p = safety.resolve_user_path(path)
        vfs = os.statvfs(p)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err("Cannot read filesystem info for %s: %s" % (path, exc.strerror))
    total, free = vfs.f_files, vfs.f_ffree
    used = total - free
    res = {"ok": True, "path": p, "device": os.stat(p).st_dev, "inodes_total": total,
           "inodes_used": used, "inodes_free": free,
           "inodes_used_percent": round(used * 100.0 / total, 2) if total else None,
           "block_size": vfs.f_frsize,
           "bytes_total": vfs.f_blocks * vfs.f_frsize,
           "bytes_free": vfs.f_bavail * vfs.f_frsize}
    if not total:
        res["note"] = "This filesystem does not report a fixed inode count (e.g. btrfs/tmpfs)."
    return res


# --------------------------------------------------------------------------- write gate
def _write_flow(action, items, refused, apply_one, dry_run, confirm, auto_confirm):
    """Shared dry-run / confirm / apply / log logic for every write tool."""
    lines = ["%s: %d change(s) planned" % (action, len(items))]
    lines += ["  - " + i["description"] for i in items]
    lines += ["  ! refused %s: %s" % (r["path"], r["reason"]) for r in refused]
    text = "\n".join(lines)
    res = {"ok": True, "action": action, "dry_run": bool(dry_run), "would_change": items,
           "refused": refused, "preview_text": text}
    if not items:
        res["status"] = "refused" if refused else "nothing_to_do"
        res["ok"] = not refused
        if refused:
            res["error"] = "; ".join(r["reason"] for r in refused)
        res["message"] = "Nothing was changed."
        return res
    if dry_run:
        res["status"] = "dry_run"
        res["message"] = "Dry run: nothing was changed."
        return res
    approved, why, shown = safety.ask_confirmation(text, confirm, auto_confirm)
    res["preview_shown"] = shown   # already printed at the y/N prompt
    if not approved:
        res["status"], res["message"] = "not_confirmed", "Nothing was changed (%s)." % why
        safety.log_action(action, [i["description"] for i in items], "not confirmed: " + why)
        return res
    results = []
    for item in items:
        r = apply_one(item)
        results.append(r)
        safety.log_action(action, item["description"],
                          "ok" if r["done"] else "failed: " + r.get("reason", ""))
    done = sum(1 for r in results if r["done"])
    res["results"] = results
    res["status"] = "done" if done == len(results) else ("partial" if done else "failed")
    res["ok"] = done > 0
    res["message"] = "%d of %d change(s) applied." % (done, len(results))
    return res


# --------------------------------------------------------------------------- write tools
def delete_broken_symlinks(paths, dry_run=False, confirm=None, auto_confirm=False):
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, (list, tuple)):
        return _err("paths must be a list of symlink paths.")
    items, refused, seen = [], [], set()
    for raw in paths:
        try:
            p = safety.resolve_user_path(raw)
            st = os.lstat(p)
        except (ValueError, TypeError) as exc:
            refused.append({"path": str(raw), "reason": str(exc)})
            continue
        except OSError as exc:
            refused.append({"path": str(raw), "reason": "cannot access: %s" % exc.strerror})
            continue
        if p in seen:
            continue
        seen.add(p)
        if not stat.S_ISLNK(st.st_mode):
            refused.append({"path": p, "reason": "not a symlink (only symlinks are ever deleted)"})
            continue
        info = link_info(p)
        if info["status"] != "broken":
            refused.append({"path": p, "reason": "symlink is not broken (status: %s)"
                            % info["status"]})
            continue
        why = safety.check_existing_entry_allowed(p, st)
        if why:
            refused.append({"path": p, "reason": why})
            continue
        items.append({"path": p, "target": info["target"],
                      "description": "unlink %s (-> %s, target missing)" % (p, info["target"])})

    def apply_one(item):
        p = item["path"]
        try:  # re-check right before deleting: must STILL be a broken symlink
            st = os.lstat(p)
            if not stat.S_ISLNK(st.st_mode):
                return {"path": p, "done": False, "reason": "no longer a symlink; left alone"}
            if link_info(p)["status"] != "broken":
                return {"path": p, "done": False, "reason": "no longer broken; left alone"}
            os.unlink(p)
            return {"path": p, "done": True}
        except OSError as exc:
            return {"path": p, "done": False, "reason": exc.strerror or str(exc)}

    return _write_flow("delete_broken_symlinks", items, refused, apply_one,
                       dry_run, confirm, auto_confirm)


def create_symlink(target, link_path, dry_run=False, confirm=None, auto_confirm=False):
    if not isinstance(target, str) or not target:
        return _err("target is required.")
    try:
        lp = safety.resolve_user_path(link_path)
    except ValueError as exc:
        return _err(str(exc))
    tgt = os.path.expandvars(os.path.expanduser(target))
    refused, items = [], []
    why = safety.check_new_entry_allowed(lp)
    if why is None and os.path.lexists(lp):
        why = "something already exists at %s" % lp
    if why:
        refused.append({"path": lp, "reason": why})
    else:
        base = os.path.dirname(lp)
        exists = os.path.exists(tgt if os.path.isabs(tgt) else os.path.join(base, tgt))
        desc = "create symlink %s -> %s" % (lp, tgt)
        if not exists:
            desc += " (warning: target does not exist, the link will be broken)"
        items.append({"path": lp, "target": tgt, "target_exists": exists, "description": desc})

    def apply_one(item):
        try:
            os.symlink(item["target"], item["path"])
            return {"path": item["path"], "done": True}
        except OSError as exc:
            return {"path": item["path"], "done": False, "reason": exc.strerror}

    return _write_flow("create_symlink", items, refused, apply_one, dry_run, confirm,
                       auto_confirm)


def create_hard_link(existing_file, new_path, dry_run=False, confirm=None, auto_confirm=False):
    try:
        src = safety.resolve_user_path(existing_file)
        dst = safety.resolve_user_path(new_path)
    except ValueError as exc:
        return _err(str(exc))
    refused, items = [], []
    why = None
    try:
        sst = os.lstat(src)
        if not stat.S_ISREG(sst.st_mode):
            why = "%s is not a regular file (hard links to directories/symlinks are refused)" % src
        elif not safety.owned_by_me(sst):
            why = "%s is not owned by you" % src
    except OSError as exc:
        why = "cannot access %s: %s" % (src, exc.strerror)
    if why is None:
        why = safety.check_new_entry_allowed(dst)
    if why is None and os.path.lexists(dst):
        why = "something already exists at %s" % dst
    if why is None and os.stat(os.path.dirname(dst)).st_dev != sst.st_dev:
        why = "hard links cannot cross filesystems (source and destination differ)"
    if why:
        refused.append({"path": dst, "reason": why})
    else:
        items.append({"path": dst, "existing_file": src,
                      "description": "create hard link %s => same inode as %s (inode %d, "
                                     "link count %d -> %d)" % (dst, src, sst.st_ino,
                                                              sst.st_nlink, sst.st_nlink + 1)})

    def apply_one(item):
        try:
            os.link(item["existing_file"], item["path"], follow_symlinks=False)
            return {"path": item["path"], "done": True}
        except OSError as exc:
            return {"path": item["path"], "done": False, "reason": exc.strerror}

    return _write_flow("create_hard_link", items, refused, apply_one, dry_run, confirm,
                       auto_confirm)


def repoint_symlink(link_path, new_target, dry_run=False, confirm=None, auto_confirm=False):
    if not isinstance(new_target, str) or not new_target:
        return _err("new_target is required.")
    try:
        lp = safety.resolve_user_path(link_path)
    except ValueError as exc:
        return _err(str(exc))
    tgt = os.path.expandvars(os.path.expanduser(new_target))
    refused, items = [], []
    why = None
    try:
        st = os.lstat(lp)
        if not stat.S_ISLNK(st.st_mode):
            why = "%s is not a symlink" % lp
        else:
            why = safety.check_existing_entry_allowed(lp, st)
    except OSError as exc:
        why = "cannot access %s: %s" % (lp, exc.strerror)
    if why:
        refused.append({"path": lp, "reason": why})
    else:
        old = os.readlink(lp)
        if old == tgt:
            return {"ok": True, "action": "repoint_symlink", "status": "nothing_to_do",
                    "dry_run": bool(dry_run), "would_change": [], "refused": [],
                    "preview_text": "%s already points to %s" % (lp, tgt),
                    "message": "Nothing was changed: link already has that target."}
        base = os.path.dirname(lp)
        exists = os.path.exists(tgt if os.path.isabs(tgt) else os.path.join(base, tgt))
        desc = "repoint %s: %s -> %s (atomic replace)" % (lp, old, tgt)
        if not exists:
            desc += " (warning: new target does not exist, the link will stay broken)"
        items.append({"path": lp, "old_target": old, "new_target": tgt,
                      "target_exists": exists, "description": desc})

    def apply_one(item):
        p = item["path"]
        tmp = os.path.join(os.path.dirname(p), ".%s.tmp-%s" % (os.path.basename(p),
                                                              uuid.uuid4().hex[:8]))
        try:
            os.symlink(item["new_target"], tmp)
            os.replace(tmp, p)
            return {"path": p, "done": True}
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return {"path": p, "done": False, "reason": exc.strerror}

    return _write_flow("repoint_symlink", items, refused, apply_one, dry_run, confirm,
                       auto_confirm)


READ_TOOLS = {
    "find_broken_symlinks": find_broken_symlinks,
    "find_symlink_loops": find_symlink_loops,
    "inspect_path": inspect_path,
    "find_hard_links": find_hard_links,
    "inode_usage": inode_usage,
    "find_suspicious_symlinks": find_suspicious_symlinks,
    "audit_summary": audit_summary,
}
WRITE_TOOLS = {
    "delete_broken_symlinks": delete_broken_symlinks,
    "create_symlink": create_symlink,
    "create_hard_link": create_hard_link,
    "repoint_symlink": repoint_symlink,
}
TOOLS = dict(READ_TOOLS, **WRITE_TOOLS)
