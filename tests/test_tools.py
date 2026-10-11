import os
import stat
import unittest
from unittest import mock

from helpers import TempTree, tools


class ReadToolTests(TempTree):
    def test_broken_vs_good_links_relative_and_absolute(self):
        good = self.write("real.txt")
        self.link("real.txt", "rel_ok")
        self.link(good, "abs_ok")
        self.link("missing.txt", "rel_broken")
        self.link(self.p("gone", "x"), "abs_broken")
        res = tools.find_broken_symlinks(self.tree)
        self.assertTrue(res["ok"])
        found = {os.path.basename(i["path"]): i for i in res["broken_symlinks"]}
        self.assertEqual(set(found), {"rel_broken", "abs_broken"})
        self.assertEqual(found["rel_broken"]["target_type"], "relative")
        self.assertEqual(found["abs_broken"]["target_type"], "absolute")
        self.assertEqual(found["rel_broken"]["resolved_target"], self.p("missing.txt"))

    def test_link_to_directory_is_not_broken_and_not_followed(self):
        os.makedirs(self.p("realdir", "sub"))
        self.link("realdir", "dirlink")
        self.link("nothing", "realdir", "sub", "inner_broken")
        res = tools.find_broken_symlinks(self.tree)
        # inner_broken is found exactly once: dirlink was not descended into
        self.assertEqual([os.path.basename(i["path"]) for i in res["broken_symlinks"]],
                         ["inner_broken"])
        info = tools.inspect_path(self.p("dirlink"))
        self.assertEqual(info["points_to"], "directory")
        self.assertFalse(info["broken"])

    def test_directory_cycle_does_not_hang(self):
        os.makedirs(self.p("a"))
        self.link("..", "a", "up")          # a/up -> tree  (directory cycle if followed)
        res = tools.find_broken_symlinks(self.tree)
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["scan"]["dirs_scanned"], 2)

    def test_loops_self_and_two_link(self):
        self.link("selfie", "selfie")
        self.link("b", "a")
        self.link("a", "b")
        self.link("nothing", "plain_broken")
        res = tools.find_symlink_loops(self.tree)
        kinds = {os.path.basename(i["path"]): i["loop_kind"] for i in res["loops"]}
        self.assertEqual(kinds, {"selfie": "self-link", "a": "cycle", "b": "cycle"})
        # loops are not reported as plain broken links
        self.assertEqual([os.path.basename(i["path"])
                          for i in tools.find_broken_symlinks(self.tree)["broken_symlinks"]],
                         ["plain_broken"])

    def test_hard_links_grouped_and_outside_note(self):
        a = self.write("a.txt")
        os.link(a, self.p("b.txt"))
        os.makedirs(self.p("sub"))
        os.link(a, self.p("sub", "c.txt"))
        self.write("lonely.txt")
        outside = os.path.join(self.root, "outside_name")
        os.link(a, outside)
        res = tools.find_hard_links(self.tree)
        self.assertEqual(res["group_count"], 1)
        g = res["groups"][0]
        self.assertEqual(g["link_count"], 4)
        self.assertEqual(g["names_found_in_scan"], 3)
        self.assertEqual(g["names_outside_scan"], 1)
        self.assertEqual(len(g["paths"]), 3)
        self.assertIn("scanned tree", res["note"])

    def test_inspect_regular_and_symlink(self):
        f = self.write("f.txt", text="hello")
        info = tools.inspect_path(f)
        self.assertEqual(info["type"], "regular file")
        self.assertEqual(info["size_bytes"], 5)
        self.assertEqual(info["hard_link_count"], 1)
        self.assertEqual(info["inode"], os.stat(f).st_ino)
        l = self.link("nowhere", "dangling")
        info = tools.inspect_path(l)
        self.assertEqual(info["type"], "symlink")
        self.assertTrue(info["broken"])
        self.assertEqual(info["target"], "nowhere")
        self.assertEqual(info["target_type"], "relative")
        self.assertFalse(tools.inspect_path(self.p("nope"))["ok"])

    def test_inode_usage(self):
        res = tools.inode_usage(self.tree)
        self.assertTrue(res["ok"])
        self.assertEqual(res["inodes_used"], res["inodes_total"] - res["inodes_free"])
        self.assertFalse(tools.inode_usage(self.p("nope"))["ok"])

    def test_suspicious_links(self):
        self.write("inside.txt")
        self.link("inside.txt", "fine")
        self.link("/etc/passwd", "to_etc")
        self.link(os.path.join(self.root, "elsewhere"), "outside")
        os.makedirs(self.p("open"))
        os.chmod(self.p("open"), 0o777)
        self.link("../inside.txt", "open", "in_ww")
        res = tools.find_suspicious_symlinks(self.tree)
        by = {os.path.basename(i["path"]): i for i in res["suspicious_symlinks"]}
        self.assertNotIn("fine", by)
        self.assertEqual(by["to_etc"]["severity"], "high")
        self.assertTrue(any("sensitive" in r for r in by["to_etc"]["reasons"]))
        self.assertTrue(any("outside the boundary" in r for r in by["outside"]["reasons"]))
        self.assertTrue(any("world-writable" in r for r in by["in_ww"]["reasons"]))

    def test_suspicious_custom_boundary(self):
        self.write("shared", "x.txt")
        self.link("../shared/x.txt", "work", "l")
        self.assertEqual(tools.find_suspicious_symlinks(self.p("work"))["count"], 1)
        self.assertEqual(tools.find_suspicious_symlinks(
            self.p("work"), boundary=self.tree)["count"], 0)

    def test_audit_summary_counts(self):
        a = self.write("a")
        os.link(a, self.p("a2"))
        self.link("nothing", "b")
        self.link("loop", "loop")
        self.link("/etc/hosts", "s")
        c = tools.audit_summary(self.tree)["counts"]
        self.assertEqual(c["broken_symlinks"], 1)
        self.assertEqual(c["symlink_loops"], 1)
        self.assertEqual(c["hard_link_groups"], 1)
        self.assertEqual(c["symlinks"], 3)
        self.assertGreaterEqual(c["suspicious_symlinks"], 1)

    def test_limits_truncate(self):
        for i in range(20):
            self.write("f%02d" % i)
        res = tools.find_broken_symlinks(self.tree, max_entries=5)
        self.assertTrue(res["scan"]["truncated"])
        os.makedirs(self.p("d1", "d2", "d3"))
        self.link("zz", "d1", "d2", "d3", "deep")
        self.assertEqual(tools.find_broken_symlinks(self.tree, max_depth=1)["count"], 0)
        self.assertEqual(tools.find_broken_symlinks(self.tree, max_depth=1)
                         ["scan"]["depth_limited_dirs"], 1)
        self.assertEqual(tools.find_broken_symlinks(self.tree, max_depth=5)["count"], 1)
        self.link("zz", "top")
        self.assertEqual(tools.find_broken_symlinks(self.tree, recursive=False)["count"], 1)

    def test_proc_sys_dev_refused(self):
        for p in ("/proc", "/sys", "/dev"):
            self.assertFalse(tools.find_broken_symlinks(p)["ok"])

    def test_unreadable_directory_is_skipped_and_reported(self):
        self.link("nothing", "ok_broken")
        locked = self.p("locked")
        os.makedirs(locked)
        real_scandir = os.scandir

        def fake_scandir(path):
            if os.fspath(path) == locked:
                raise PermissionError(13, "Permission denied", locked)
            return real_scandir(path)

        with mock.patch("os.scandir", fake_scandir):
            res = tools.find_broken_symlinks(self.tree)
        self.assertEqual(res["count"], 1)
        self.assertEqual([s["path"] for s in res["scan"]["skipped"]], [locked])

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_really_unreadable_directory(self):
        self.link("nothing", "ok_broken")
        locked = self.p("locked")
        os.makedirs(locked)
        os.chmod(locked, 0)
        res = tools.find_broken_symlinks(self.tree)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["scan"]["skipped_count"], 1)

    def test_bad_inputs(self):
        self.assertFalse(tools.find_broken_symlinks(self.p("missing"))["ok"])
        f = self.write("file")
        self.assertFalse(tools.find_broken_symlinks(f)["ok"])
        self.assertFalse(tools.find_broken_symlinks("")["ok"])


class PathResolutionTests(TempTree):
    def test_home_alias_and_env(self):
        home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(home, "Desktop"))
        with mock.patch.dict(os.environ, {"HOME": home, "MYDIR": self.tree}):
            from inode_symlink_agent import safety
            self.assertEqual(safety.resolve_user_path("desktop"), os.path.join(home, "Desktop"))
            self.assertEqual(safety.resolve_user_path("my Desktop/x"),
                             os.path.join(home, "Desktop", "x"))
            self.assertEqual(safety.resolve_user_path("~/desktop"), os.path.join(home, "Desktop"))
            self.assertEqual(safety.resolve_user_path("$MYDIR/a"), os.path.join(self.tree, "a"))
            # downloads does not exist -> left as a relative path
            self.assertEqual(safety.resolve_user_path("downloads"), os.path.abspath("downloads"))


if __name__ == "__main__":
    unittest.main()
