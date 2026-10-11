import os
import unittest
import unittest.mock

from helpers import TempTree, tools
from inode_symlink_agent import safety


class DeleteTests(TempTree):
    def setUp(self):
        super().setUp()
        self.b1 = self.link("gone1", "b1")
        self.b2 = self.link("gone2", "sub", "b2")

    def test_non_interactive_without_confirmation_never_writes(self):
        res = tools.delete_broken_symlinks([self.b1])
        self.assertEqual(res["status"], "not_confirmed")
        self.assertTrue(os.path.islink(self.b1))

    def test_dry_run_previews_only(self):
        res = tools.delete_broken_symlinks([self.b1, self.b2], dry_run=True, auto_confirm=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(len(res["would_change"]), 2)
        self.assertIn(self.b1, res["preview_text"])
        self.assertTrue(os.path.islink(self.b1) and os.path.islink(self.b2))

    def test_callback_decline_and_accept(self):
        seen = []
        res = tools.delete_broken_symlinks([self.b1], confirm=lambda t: seen.append(t) or False)
        self.assertEqual(res["status"], "not_confirmed")
        self.assertTrue(os.path.islink(self.b1))
        self.assertIn(self.b1, seen[0])
        res = tools.delete_broken_symlinks([self.b1], confirm=lambda t: True)
        self.assertEqual(res["status"], "done")
        self.assertFalse(os.path.lexists(self.b1))

    def test_failing_callback_means_no(self):
        def boom(_):
            raise RuntimeError("x")
        res = tools.delete_broken_symlinks([self.b1], confirm=boom)
        self.assertEqual(res["status"], "not_confirmed")
        self.assertTrue(os.path.islink(self.b1))

    def test_never_deletes_regular_file_or_directory(self):
        f = self.write("keep.txt")
        d = self.p("keepdir")
        os.makedirs(d)
        res = tools.delete_broken_symlinks([f, d], auto_confirm=True)
        self.assertEqual(res["status"], "refused")
        self.assertTrue(os.path.isfile(f) and os.path.isdir(d))
        self.assertEqual(len(res["refused"]), 2)

    def test_never_deletes_healthy_symlink(self):
        ok = self.link("keep.txt", "okl")
        self.write("keep.txt")
        res = tools.delete_broken_symlinks([ok], auto_confirm=True)
        self.assertEqual(res["status"], "refused")
        self.assertTrue(os.path.islink(ok))

    def test_link_that_becomes_valid_again_is_kept(self):
        """Preview says delete, then the target appears before the actual unlink."""
        def confirm_then_fix(_text):
            self.write("gone1")             # b1 is valid again after the user said yes
            return True
        res = tools.delete_broken_symlinks([self.b1, self.b2], confirm=confirm_then_fix)
        self.assertTrue(os.path.islink(self.b1))
        self.assertFalse(os.path.lexists(self.b2))
        self.assertEqual(res["status"], "partial")
        bad = [r for r in res["results"] if not r["done"]][0]
        self.assertIn("no longer broken", bad["reason"])

    def test_path_replaced_by_regular_file_is_kept(self):
        def confirm_then_swap(_text):
            os.unlink(self.b1)
            self.write("b1", text="precious")
            return True
        tools.delete_broken_symlinks([self.b1], confirm=confirm_then_swap)
        with open(self.b1) as fh:
            self.assertEqual(fh.read(), "precious")

    def test_system_path_refused(self):
        res = tools.delete_broken_symlinks(["/etc/definitely_not_here"], auto_confirm=True)
        self.assertFalse(res["ok"])

    def test_log_written(self):
        tools.delete_broken_symlinks([self.b1], auto_confirm=True)
        text = self.log_text()
        self.assertIn("delete_broken_symlinks", text)
        self.assertIn(self.b1, text)
        self.assertIn('"result": "ok"', text)

    def test_arguments_string_accepted(self):
        res = tools.delete_broken_symlinks(self.b1, dry_run=True)
        self.assertEqual(res["status"], "dry_run")


class InteractivePromptTests(TempTree):
    """The y/N path, with a simulated terminal and simulated typing."""

    def ask(self, answer, interactive=True):
        link = self.link("gone", "b")
        with unittest.mock.patch("inode_symlink_agent.safety.is_interactive",
                                 return_value=interactive), \
                unittest.mock.patch("builtins.input", return_value=answer) as fake_input, \
                unittest.mock.patch("builtins.print"):
            res = tools.delete_broken_symlinks([link])
        return link, res, fake_input

    def test_yes_deletes(self):
        for answer in ("y", "Y", " yes "):
            link, res, fake_input = self.ask(answer)
            self.assertEqual(res["status"], "done", answer)
            self.assertFalse(os.path.lexists(link))
            fake_input.assert_called_once()

    def test_no_empty_and_garbage_keep_the_link(self):
        for answer in ("n", "", "maybe"):
            link, res, _ = self.ask(answer)
            self.assertEqual(res["status"], "not_confirmed", answer)
            self.assertTrue(os.path.islink(link))
            os.unlink(link)

    def test_eof_means_no(self):
        link = self.link("gone", "b")
        with unittest.mock.patch("inode_symlink_agent.safety.is_interactive",
                                 return_value=True), \
                unittest.mock.patch("builtins.input", side_effect=EOFError), \
                unittest.mock.patch("builtins.print"):
            res = tools.delete_broken_symlinks([link])
        self.assertEqual(res["status"], "not_confirmed")
        self.assertTrue(os.path.islink(link))

    def test_non_interactive_never_asks(self):
        link, res, fake_input = self.ask("y", interactive=False)
        self.assertEqual(res["status"], "not_confirmed")
        self.assertTrue(os.path.islink(link))
        fake_input.assert_not_called()


class CreateAndRepointTests(TempTree):
    def test_create_symlink(self):
        t = self.write("t.txt")
        lp = self.p("l")
        self.assertEqual(tools.create_symlink(t, lp, dry_run=True)["status"], "dry_run")
        self.assertFalse(os.path.lexists(lp))
        self.assertEqual(tools.create_symlink(t, lp)["status"], "not_confirmed")
        self.assertEqual(tools.create_symlink(t, lp, auto_confirm=True)["status"], "done")
        self.assertEqual(os.readlink(lp), t)
        self.assertEqual(tools.create_symlink(t, lp, auto_confirm=True)["status"], "refused")

    def test_create_symlink_warns_on_missing_target(self):
        res = tools.create_symlink("nothing", self.p("l"), dry_run=True)
        self.assertIn("warning", res["preview_text"])

    def test_create_hard_link(self):
        src = self.write("s.txt")
        dst = self.p("h.txt")
        self.assertEqual(tools.create_hard_link(src, dst, dry_run=True)["status"], "dry_run")
        self.assertEqual(tools.create_hard_link(src, dst, auto_confirm=True)["status"], "done")
        self.assertEqual(os.stat(src).st_ino, os.stat(dst).st_ino)
        self.assertEqual(os.stat(src).st_nlink, 2)

    def test_hard_link_refuses_directories_symlinks_and_existing(self):
        os.makedirs(self.p("d"))
        self.assertFalse(tools.create_hard_link(self.p("d"), self.p("d2"), auto_confirm=True)["ok"])
        s = self.write("s")
        l = self.link("s", "sl")
        self.assertFalse(tools.create_hard_link(l, self.p("x"), auto_confirm=True)["ok"])
        self.assertFalse(tools.create_hard_link(s, l, auto_confirm=True)["ok"])

    def test_repoint_atomic(self):
        a, b = self.write("a"), self.write("b")
        l = self.link(a, "l")
        res = tools.repoint_symlink(l, b, dry_run=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(os.readlink(l), a)
        res = tools.repoint_symlink(l, b, auto_confirm=True)
        self.assertEqual(res["status"], "done")
        self.assertEqual(os.readlink(l), b)
        self.assertEqual(os.listdir(self.tree).count("l"), 1)
        self.assertEqual(sorted(os.listdir(self.tree)), ["a", "b", "l"])  # no temp files left
        self.assertEqual(tools.repoint_symlink(l, b, auto_confirm=True)["status"], "nothing_to_do")

    def test_repoint_refuses_non_symlink(self):
        f = self.write("f")
        self.assertFalse(tools.repoint_symlink(f, "x", auto_confirm=True)["ok"])
        self.assertTrue(os.path.isfile(f))


class SafetyTests(TempTree):
    def test_system_paths(self):
        for p in ("/", "/etc/x", "/usr/bin/y", "/var/tmp/z", "/lib64/q", "/proc/1", "/foo",
                  "/boot/g", "/bin/ls", "/sbin/x", "/sys/x", "/dev/null"):
            self.assertTrue(safety.is_system_path(p), p)
        self.assertFalse(safety.is_system_path(self.p("x")))

    def test_system_path_via_symlinked_parent(self):
        ln = self.link("/etc", "etclink")
        self.assertTrue(safety.is_system_path(os.path.join(ln, "foo")))

    def test_writes_in_system_paths_refused(self):
        res = tools.create_symlink("/tmp", "/etc/agent10_should_not_exist", auto_confirm=True)
        self.assertFalse(res["ok"])
        self.assertFalse(os.path.lexists("/etc/agent10_should_not_exist"))
        res = tools.create_symlink("/tmp", "/agent10_root_level", auto_confirm=True)
        self.assertFalse(res["ok"])

    def test_not_owned_refused(self):
        l = self.link("gone", "l")
        real = os.lstat
        class Fake:  # pretend another user owns the link
            def __init__(self, st):
                self.__dict__.update({k: getattr(st, k) for k in dir(st) if k.startswith("st_")})
                self.st_uid = os.geteuid() + 1
        with unittest.mock.patch("os.lstat", lambda p, *a, **k: Fake(real(p, *a, **k))):
            res = tools.delete_broken_symlinks([l], auto_confirm=True)
        self.assertFalse(res["ok"])
        self.assertTrue(os.path.islink(l))


if __name__ == "__main__":
    unittest.main()
