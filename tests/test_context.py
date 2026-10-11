import json
import os
import unittest
from unittest import mock

from helpers import TempTree
from test_agent import FakeClient, text, tool_call
from inode_symlink_agent import agent
from inode_symlink_agent.context import SessionContext


def system_text(client, call_index=-1):
    return client.calls[call_index][0][0]["content"]


class SessionContextUnitTests(unittest.TestCase):
    def test_folder_actions_and_items(self):
        ctx = SessionContext()
        ctx.update("find_broken_symlinks", {"path": "x"},
                   {"ok": True, "path": "/d", "count": 2,
                    "broken_symlinks": [{"path": "/d/a"}, {"path": "/d/b"}]})
        out = ctx.render()
        self.assertIn("Current folder: /d", out)
        self.assertIn("searched for broken links in /d: found 2", out)
        self.assertIn("/d/a, /d/b", out)

    def test_delete_does_not_move_folder_and_removes_deleted_items(self):
        ctx = SessionContext()
        ctx.update("find_broken_symlinks", {}, {"ok": True, "path": "/d", "count": 2,
                   "broken_symlinks": [{"path": "/d/sub/a"}, {"path": "/d/sub/b"}]})
        ctx.update("delete_broken_symlinks", {}, {
            "ok": True, "status": "done", "would_change": [{"path": "/d/sub/a"}],
            "results": [{"path": "/d/sub/a", "done": True}]})
        self.assertEqual(ctx.folder, "/d")
        self.assertEqual(ctx.items, ["/d/sub/b"])
        self.assertIn("deleted 1 item(s)", ctx.render())

    def test_declined_is_recorded_and_actions_are_limited(self):
        ctx = SessionContext()
        for i in range(6):
            ctx.update("inode_usage", {}, {"ok": True, "path": "/p%d" % i,
                                           "inodes_used_percent": 1})
        ctx.update("delete_broken_symlinks", {}, {"ok": True, "status": "not_confirmed",
                                                  "declined": True, "would_change": []})
        self.assertEqual(len(ctx.actions), 3)
        self.assertIn("the user declined", ctx.render())

    def test_empty_context_renders_nothing_and_bad_input_is_ignored(self):
        ctx = SessionContext()
        self.assertEqual(ctx.render(), "")
        ctx.update("find_broken_symlinks", "not a dict", None)
        ctx.update("nope", {}, {"ok": True})
        self.assertEqual(ctx.render(), "")

    def test_failed_call_is_noted_without_changing_folder(self):
        ctx = SessionContext()
        ctx.folder = "/keep"
        ctx.update("find_broken_symlinks", {}, {"ok": False, "error": "Path does not exist"})
        self.assertEqual(ctx.folder, "/keep")
        self.assertIn("failed", ctx.render())


class ContextSurvivesTrimmingTests(TempTree):
    def setUp(self):
        super().setUp()
        self.b1 = self.link("gone1", "b1")
        self.b2 = self.link("gone2", "b2")
        a = self.write("a.txt")
        os.link(a, self.p("a2.txt"))

    def test_follow_up_after_many_turns_still_knows_the_folder(self):
        script = [
            tool_call("find_broken_symlinks", {"path": self.tree}), text("Found 2."),
            tool_call("delete_broken_symlinks", {"paths": [self.b1]}, "d1"), text("Deleted one."),
            tool_call("delete_broken_symlinks", {"paths": [self.b2]}, "d2"), text("Deleted two."),
        ]
        script += [text("ok %d" % i) for i in range(6)]      # push the early turns out of history
        script += [tool_call("find_hard_links", {"path": self.tree}, "h1"), text("Two names.")]
        client = FakeClient(*script)
        hist = agent.new_history()
        for prompt in ["find broken shortcuts in " + self.tree, "delete the first", "delete the other"]:
            agent.handle(prompt, client=client, history=hist, confirm=lambda t: True)
        for i in range(6):
            agent.handle("filler %d" % i, client=client, history=hist)
        agent.handle("which files there are hard links of each other?", client=client,
                     history=hist)

        first_request_of_last_turn = next(
            c for c in client.calls if c[0][-1].get("content", "").startswith("which files there"))
        msgs = first_request_of_last_turn[0]
        contents = [m.get("content") or "" for m in msgs if m["role"] == "user"]
        self.assertNotIn("find broken shortcuts in " + self.tree, contents)   # trimmed away
        sysmsg = msgs[0]["content"]
        self.assertIn("Current folder: " + self.tree, sysmsg)                  # ...but remembered
        self.assertIn("deleted 1 item(s)", sysmsg)
        self.assertIn("never ask", sysmsg.lower().replace("do not ask", "never ask"))
        self.assertTrue(sysmsg.startswith(agent.SYSTEM_PROMPT))

    def test_context_updates_after_each_tool_call_within_a_turn(self):
        client = FakeClient(tool_call("find_broken_symlinks", {"path": self.tree}),
                            tool_call("find_hard_links", {"path": self.tree}, "c2"),
                            text("done"))
        hist = agent.new_history()
        agent.handle("audit", client=client, history=hist)
        self.assertNotIn("Current folder", system_text(client, 0))            # before any call
        self.assertIn("searched for broken links", system_text(client, 1))
        self.assertIn("hard links", system_text(client, 2))
        self.assertIn(self.b1, system_text(client, 1))                        # "them" targets

    def test_one_shot_has_no_context_note(self):
        client = FakeClient(tool_call("find_broken_symlinks", {"path": self.tree}), text("x"))
        agent.handle("find", client=client)
        self.assertEqual(system_text(client), agent.SYSTEM_PROMPT)   # no context note appended

    def test_system_prompt_mentions_context_and_plain_language(self):
        sp = agent.SYSTEM_PROMPT
        self.assertIn("Session context", sp)
        self.assertIn("Never mention tool, function or parameter names", sp)
        self.assertIn('Say "delete them"', sp)


class PlainReplyTests(TempTree):
    def setUp(self):
        super().setUp()
        self.b = self.link("gone", "b1")

    def test_declined_gets_the_fixed_reply_without_another_model_call(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}),
                            text("Would you like me to run delete_broken_symlinks with dry_run?"))
        hist = agent.new_history()
        reply = agent.handle("delete them", client=client, history=hist, confirm=lambda t: False)
        self.assertEqual(reply, "OK, nothing was deleted.")
        self.assertEqual(len(client.calls), 1)               # the model was not asked again
        self.assertTrue(os.path.islink(self.b))
        self.assertEqual(hist[-1], {"role": "assistant", "content": "OK, nothing was deleted."})
        self.assertIn("declined", hist.context.render())

    def test_declined_at_the_yn_prompt(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}))
        with mock.patch("inode_symlink_agent.safety.is_interactive", return_value=True), \
                mock.patch("builtins.input", return_value="n"), mock.patch("builtins.print"):
            reply = agent.handle("delete them", client=client)
        self.assertEqual(reply, "OK, nothing was deleted.")

    def test_declined_create_and_repoint_wording(self):
        for tool, args, expected in (
                ("create_symlink", {"target": "x", "link_path": self.p("new")},
                 "OK, nothing was created."),
                ("repoint_symlink", {"link_path": self.b, "new_target": "other"},
                 "OK, nothing was changed.")):
            client = FakeClient(tool_call(tool, args))
            self.assertEqual(agent.handle("do it", client=client, confirm=lambda t: False),
                             expected)

    def test_no_confirmation_available_still_goes_back_to_the_model(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}),
                            text("I could not ask you; re-run with --yes."))
        reply = agent.handle("delete them", client=client)   # non-interactive, no callback
        self.assertEqual(len(client.calls), 2)
        self.assertIn("--yes", reply)

    def test_replies_never_contain_internal_names(self):
        raw = ("I called `delete_broken_symlinks` with dry_run=true and then "
               "find_broken_symlinks; auto_confirm was off.")
        client = FakeClient(text(raw))
        reply = agent.handle("what did you do", client=client)
        for name in ("delete_broken_symlinks", "find_broken_symlinks", "dry_run", "auto_confirm"):
            self.assertNotIn(name, reply)
        self.assertIn("the deletion", reply)
        self.assertNotIn("`", reply)

    def test_scrub_leaves_normal_text_alone(self):
        s = "Found 2 broken links. Say \"delete them\" to remove them."
        self.assertEqual(agent.scrub_internal_names(s), s)

    def test_tool_names_not_leaked_by_schemas_into_user_text_paths(self):
        # every tool name is covered by the scrubber
        from inode_symlink_agent import tools
        for name in tools.TOOLS:
            self.assertNotIn(name, agent.scrub_internal_names("use " + name + " now"))


if __name__ == "__main__":
    unittest.main()
