"""Turn tool result dicts into readable text reports."""
import json


def _scan_notes(res):
    s = res.get("scan")
    if not s:
        return []
    lines = ["", "Scanned %d entries in %d directories (max depth %d)."
             % (s["entries_scanned"], s["dirs_scanned"], s["max_depth"])]
    if s["truncated"]:
        lines.append("WARNING: scan stopped early (%s); results may be incomplete."
                     % s["truncated_reason"])
    if s["depth_limited_dirs"]:
        lines.append("Note: %d directories were not entered because of the depth limit."
                     % s["depth_limited_dirs"])
    if s["skipped_count"]:
        lines.append("Skipped %d path(s):" % s["skipped_count"])
        lines += ["  - %s (%s)" % (x["path"], x["reason"]) for x in s["skipped"][:10]]
        if s["skipped_count"] > 10:
            lines.append("  ... and %d more" % (s["skipped_count"] - 10))
    return lines


def _more(res):
    return ["  ... list cut at 200 entries"] if res.get("list_truncated") else []


def _broken(r):
    lines = ["Broken symlinks under %s: %d" % (r["path"], r["count"])]
    lines += ["  %s -> %s  (%s, target missing)" % (i["path"], i["target"], i["target_type"])
              for i in r["broken_symlinks"]]
    return lines + _more(r) + _scan_notes(r)


def _loops(r):
    lines = ["Symlink loops under %s: %d" % (r["path"], r["count"])]
    for i in r["loops"]:
        lines.append("  %s [%s]: %s" % (i["path"], i["loop_kind"], " -> ".join(i["chain"])))
    return lines + _more(r) + _scan_notes(r)


def _hard(r):
    lines = ["Hard-link groups under %s: %d (%d with 2+ names in this tree)"
             % (r["path"], r["group_count"], r["groups_with_multiple_names_in_scan"])]
    for g in r["groups"]:
        lines.append("  inode %d, link count %d, %d name(s) found, %d outside the scan:"
                     % (g["inode"], g["link_count"], g["names_found_in_scan"],
                        g["names_outside_scan"]))
        lines += ["      " + p for p in g["paths"]]
    return lines + _more(r) + ["", "Note: " + r["note"]] + _scan_notes(r)


def _susp(r):
    lines = ["Suspicious symlinks under %s (boundary %s): %d"
             % (r["path"], r["boundary"], r["count"])]
    for i in r["suspicious_symlinks"]:
        lines.append("  [%s] %s -> %s" % (i["severity"].upper(), i["path"], i["target"]))
        lines += ["        why: " + why for why in i["reasons"]]
    return lines + _more(r) + _scan_notes(r)


def _inode(r):
    pct = "n/a" if r["inodes_used_percent"] is None else "%s%%" % r["inodes_used_percent"]
    lines = ["Inode usage for the filesystem containing %s:" % r["path"],
             "  total %d   used %d   free %d   used %s"
             % (r["inodes_total"], r["inodes_used"], r["inodes_free"], pct)]
    if r.get("note"):
        lines.append("  " + r["note"])
    return lines


def _inspect(r):
    lines = ["%s: %s" % (r["path"], r["type"]),
             "  inode %d on device %d, hard links %d, size %d bytes"
             % (r["inode"], r["device"], r["hard_link_count"], r["size_bytes"]),
             "  owner %s:%s, permissions %s (%s), modified %s"
             % (r["owner"], r["group"], r["permissions"], r["mode_octal"], r["modified"])]
    if "target" in r:
        lines += ["  target (raw): %s  [%s]" % (r["target"], r["target_type"]),
                  "  resolved:     %s" % r["resolved_target"],
                  "  status:       %s" % ("BROKEN" if r["broken"] else r["link_status"])]
    return lines


def _audit(r):
    c, e = r["counts"], r["examples"]
    lines = ["Audit of %s" % r["path"],
             "  files %d, directories %d, symlinks %d (healthy %d)"
             % (c["files"], c["directories_scanned"], c["symlinks"], c["healthy_symlinks"]),
             "  broken symlinks:     %d" % c["broken_symlinks"],
             "  symlink loops:       %d" % c["symlink_loops"],
             "  suspicious symlinks: %d" % c["suspicious_symlinks"],
             "  hard-link groups:    %d (%d with 2+ names in this tree)"
             % (c["hard_link_groups"], c["hard_link_groups_with_multiple_names_in_scan"])]
    for title, rows, fmt in (
            ("Broken examples", e["broken_symlinks"], lambda x: "%s -> %s" % (x["path"], x["target"])),
            ("Loop examples", e["symlink_loops"], lambda x: "%s [%s]" % (x["path"], x["loop_kind"])),
            ("Suspicious examples", e["suspicious_symlinks"],
             lambda x: "[%s] %s: %s" % (x["severity"], x["path"], "; ".join(x["reasons"]))),
            ("Hard-link examples", e["hard_link_groups"],
             lambda x: "inode %d (%d links): %s" % (x["inode"], x["link_count"], ", ".join(x["paths"])))):
        if rows:
            lines += ["", title + ":"] + ["  " + fmt(x) for x in rows]
    return lines + ["", "Note: " + r["hard_link_note"]] + _scan_notes(r)


def _write(r):
    lines = [r.get("preview_text", "")]
    if r.get("results"):
        lines.append("")
        for x in r["results"]:
            lines.append("  %s %s%s" % ("done:" if x["done"] else "FAILED:", x["path"],
                                        "" if x["done"] else " (%s)" % x.get("reason", "")))
    lines.append("")
    lines.append("[%s] %s" % (r.get("status", ""), r.get("message", "")))
    return lines


_FORMATTERS = {
    "find_broken_symlinks": _broken, "find_symlink_loops": _loops, "find_hard_links": _hard,
    "find_suspicious_symlinks": _susp, "inode_usage": _inode, "inspect_path": _inspect,
    "audit_summary": _audit, "delete_broken_symlinks": _write, "create_symlink": _write,
    "create_hard_link": _write, "repoint_symlink": _write,
}


def format_result(tool_name, result, as_json=False):
    if as_json:
        return json.dumps(result, indent=2, default=str)
    if not result.get("ok", True) and "preview_text" not in result:
        return "Error: %s" % result.get("error", "unknown error")
    fmt = _FORMATTERS.get(tool_name)
    return "\n".join(fmt(result)) if fmt else json.dumps(result, indent=2, default=str)
