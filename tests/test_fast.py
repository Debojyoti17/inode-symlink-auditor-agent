"""Fast mode: one LLM call per request, the program's own report as the answer."""
import json
import os
import unittest
from unittest import mock

from helpers import TempTree
import threading
from http.server import HTTPServer

from test_agent import FakeClient, text, tool_call, _Handler
from inode_symlink_agent import __main__ as cli, agent, llm


class FastModeTests(TempTree):
    def setUp(self):
        super().setUp()
        self.b = self.link("gone", "b1")

    def test_read_tool_answered_without_second_llm_call(self):
        client = FakeClient(tool_call("find_broken_symlinks", {"path": self.tree}))
        reply = agent.handle("find broken links", client=client, fast=True)
        self.assertEqual(len(client.calls), 1)                 # only the tool-choosing call
        self.assertIn("Broken symlinks under %s: 1" % self.tree, reply)
        self.assertIn(self.b, reply)

    def test_env_variable_turns_it_on(self):
        client = FakeClient(tool_call("inode_usage", {"path": self.tree}))
        with mock.patch.dict(os.environ, {"AGENT_FAST": "1"}):
            reply = agent.handle("inodes?", client=client)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("Inode usage", reply)

    def test_off_by_default(self):
        client = FakeClient(tool_call("inode_usage", {"path": self.tree}), text("plenty left"))
        self.assertEqual(agent.handle("inodes?", client=client), "plenty left")
        self.assertEqual(len(client.calls), 2)

    def test_write_with_confirmation_reports_result(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}))
        reply = agent.handle("delete them", client=client, fast=True, confirm=lambda t: True)
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(os.path.lexists(self.b))
        self.assertIn("1 of 1 change(s) applied", reply)

    def test_decline_still_gives_the_short_reply(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}))
        reply = agent.handle("delete them", client=client, fast=True, confirm=lambda t: False)
        self.assertEqual(reply, "OK, nothing was deleted.")
        self.assertTrue(os.path.islink(self.b))

    def test_tool_error_goes_back_to_the_llm_to_recover(self):
        client = FakeClient(tool_call("find_broken_symlinks", {"path": self.p("missing")}),
                            tool_call("find_broken_symlinks", {"path": self.tree}, "c2"))
        reply = agent.handle("find broken links", client=client, fast=True)
        self.assertEqual(len(client.calls), 2)                 # error was shown to the model
        self.assertIn("Path does not exist", client.calls[1][0][-1]["content"])
        self.assertIn(self.b, reply)

    def test_history_and_context_kept_for_follow_ups(self):
        client = FakeClient(tool_call("find_broken_symlinks", {"path": self.tree}),
                            tool_call("delete_broken_symlinks", {"paths": [self.b]}, "d1"))
        hist = agent.new_history()
        agent.handle("find broken links in " + self.tree, client=client, history=hist, fast=True)
        self.assertEqual(hist[-1]["role"], "assistant")
        self.assertIn(self.b, hist[-1]["content"])
        self.assertIn(self.b, hist.context.render())
        agent.handle("delete them", client=client, history=hist, fast=True,
                     confirm=lambda t: True)
        self.assertIn(self.b, json.dumps(client.calls[1][0]))  # the model saw the earlier result
        self.assertFalse(os.path.lexists(self.b))

    def test_cli_flag(self):
        a = cli._build_parser().parse_args(["--fast"])
        self.assertTrue(a.fast)
        with mock.patch.object(cli, "run_chat", return_value=0) as rc:
            cli.main(["--fast"])
        rc.assert_called_once_with(auto_confirm=False, verbose=False, fast=True)


class ReasoningEffortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d/v1/" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_sent_only_when_configured(self):
        llm.ChatClient(self.base, "k", "m1").chat([])
        self.assertNotIn("reasoning_effort", _Handler.seen[-1][2])
        llm.ChatClient(self.base, "k", "m1", reasoning_effort="low").chat([])
        self.assertEqual(_Handler.seen[-1][2]["reasoning_effort"], "low")

    def test_read_from_environment(self):
        env = {"AGENT_LLM_BASE_URL": self.base, "AGENT_LLM_API_KEY": "k",
               "AGENT_LLM_REASONING_EFFORT": "low"}
        with mock.patch.dict(os.environ, env), mock.patch("inode_symlink_agent.llm.load_dotenv"):
            self.assertEqual(llm.ChatClient.from_env().reasoning_effort, "low")
        with mock.patch.dict(os.environ, {"AGENT_LLM_BASE_URL": self.base,
                                          "AGENT_LLM_API_KEY": "k",
                                          "AGENT_LLM_REASONING_EFFORT": ""}), \
                mock.patch("inode_symlink_agent.llm.load_dotenv"):
            self.assertIsNone(llm.ChatClient.from_env().reasoning_effort)


if __name__ == "__main__":
    unittest.main()
