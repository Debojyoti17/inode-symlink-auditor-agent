"""A tiny "session context" note that survives history trimming.

It remembers the current folder, the last few actions (one plain-English line each) and the
items found by the last search, so words like "there", "that folder" and "them" still
resolve after older turns have been dropped. It is re-rendered into the system message
before every request.
"""
import json
import os

MAX_ACTIONS = 3
MAX_ITEMS = 8


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


class SessionContext:
    def __init__(self):
        self.folder = None
        self.actions = []
        self.items = []          # full paths from the last search, for "delete them"
        self.items_more = 0

    # -- updating -------------------------------------------------------------------
    def update(self, name, args, result):
        """Record one finished tool call. Never raises."""
        try:
            self._update(name, args if isinstance(args, dict) else {}, result)
        except Exception:
            pass

    def _update(self, name, args, result):
        if not isinstance(result, dict):
            return
        if not result.get("ok", True) and "status" not in result:
            self._add("tried %s but it failed: %s" % (_LABELS.get(name, name),
                                                       str(result.get("error", ""))[:80]))
            return
        path = result.get("path")
        if name in _SCAN_TOOLS and path:
            self.folder = path
        if name in ("find_broken_symlinks", "find_symlink_loops"):
            key, noun = (("broken_symlinks", "broken link") if name == "find_broken_symlinks"
                         else ("loops", "link loop"))
            rows = [r.get("path") for r in result.get(key, []) if r.get("path")]
            self._set_items(rows, result.get("count", len(rows)))
            self._add("searched for %ss in %s: found %d" % (noun, path, result.get("count", 0)))
        elif name == "find_suspicious_symlinks":
            rows = [r["path"] for r in result.get("suspicious_symlinks", []) if r.get("path")]
            self._set_items(rows, result.get("count", len(rows)))
            self._add("checked for suspicious links in %s: %d flagged" % (path,
                                                                           result.get("count", 0)))
        elif name == "find_hard_links":
            self._add("searched for hard links in %s: %s" % (
                path, _plural(result.get("group_count", 0), "group")))
        elif name == "audit_summary":
            c = result.get("counts", {})
            self._add("audited %s: %d broken, %d loops, %d suspicious, %s of hard links" % (
                path, c.get("broken_symlinks", 0), c.get("symlink_loops", 0),
                c.get("suspicious_symlinks", 0), _plural(c.get("hard_link_groups", 0), "group")))
        elif name == "inspect_path":
            self._add("looked at %s (%s)" % (path, result.get("type", "?")))
        elif name == "inode_usage":
            self._add("checked inode usage for %s: %s%% used" % (
                path, result.get("inodes_used_percent")))
        elif name in _WRITE_LABELS:
            self._update_write(name, result)

    def _update_write(self, name, result):
        verb = _WRITE_LABELS[name]
        status = result.get("status")
        n = len(result.get("would_change", []))
        if status == "done" or status == "partial":
            done = [r["path"] for r in result.get("results", []) if r.get("done")]
            self._add("%s %d item(s) (%s)" % (verb[0], len(done), status))
            if name == "delete_broken_symlinks":
                self.items = [p for p in self.items if p not in done]
        elif status == "not_confirmed":
            self._add("%s not done: the user %s" % (
                verb[1], "declined" if result.get("declined") else "could not be asked"))
        elif status == "dry_run":
            self._add("previewed %s of %d item(s); nothing changed" % (verb[1], n))
        elif status == "refused":
            self._add("%s was refused: %s" % (verb[1], str(result.get("error", ""))[:80]))
        if self.folder is None and result.get("would_change"):
            self.folder = os.path.dirname(result["would_change"][0].get("path", "")) or None

    def _set_items(self, rows, total):
        self.items = rows[:MAX_ITEMS]
        self.items_more = max(0, total - len(self.items))

    def _add(self, text):
        self.actions.append(text)
        del self.actions[:-MAX_ACTIONS]

    # -- rendering ------------------------------------------------------------------
    def render(self):
        if not (self.folder or self.actions or self.items):
            return ""
        lines = ["", "Session context (earlier turns may have been dropped; use this to "
                 "resolve \"there\", \"that folder\", \"them\" and \"it\", and do not ask "
                 "again for a folder that is set):"]
        if self.folder:
            lines.append("- Current folder: %s" % self.folder)
        if self.actions:
            lines.append("- Recent actions, oldest first: " + "; ".join(self.actions))
        if self.items:
            more = " (+%d more)" % self.items_more if self.items_more else ""
            lines.append("- Items from the last search: %s%s" % (", ".join(self.items), more))
        return "\n".join(lines)

    def to_json(self):
        return json.dumps({"folder": self.folder, "actions": self.actions, "items": self.items})


_SCAN_TOOLS = ("find_broken_symlinks", "find_symlink_loops", "find_hard_links",
               "find_suspicious_symlinks", "audit_summary", "inode_usage")
_WRITE_LABELS = {   # name -> (past-tense "did", noun phrase for "X not done"/"previewed X")
    "delete_broken_symlinks": ("deleted", "the deletion"),
    "create_symlink": ("created", "creating the link"),
    "create_hard_link": ("created", "creating the hard link"),
    "repoint_symlink": ("changed", "changing the link"),
}
_LABELS = {"find_broken_symlinks": "the broken-link search", "find_symlink_loops": "the loop check",
           "inspect_path": "the inspection", "find_hard_links": "the hard-link search",
           "inode_usage": "the inode check", "find_suspicious_symlinks": "the suspicious-link check",
           "audit_summary": "the audit", "delete_broken_symlinks": "the deletion",
           "create_symlink": "creating the link", "create_hard_link": "creating the hard link",
           "repoint_symlink": "changing the link"}
