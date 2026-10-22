"""The example base shell can find our agent, route to it and pass a confirm callback."""
import functools
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import ROOT, TempTree
from test_agent import FakeClient, text, tool_call

sys.path.insert(0, os.path.join(ROOT, "examples"))
import mini_base_shell as shell  # noqa: E402

import inode_symlink_agent  # noqa: E402
from inode_symlink_agent import agent  # noqa: E402


def fake_agent(name, description, replies):
    mod = types.ModuleType(name)
    mod.AGENT_NAME, mod.AGENT_DESCRIPTION = name, description
    mod.calls = []

    def handle(prompt, confirm=None, **kw):
        mod.calls.append((prompt, confirm, kw))
        return replies
    mod.handle = handle
    return mod


class LoadingAndRoutingTests(unittest.TestCase):
    def test_finds_our_agent_through_its_public_contract(self):
        agents = shell.load_agents(["inode_symlink_agent"], out=lambda *_: None)
        self.assertEqual([a["name"] for a in agents], ["inode_symlink_auditor"])
        self.assertIsNotNone(agents[0]["history"])          # chat history per agent

    def test_broken_or_incomplete_agents_are_skipped(self):
        notes = []
        with mock.patch.dict(sys.modules, {"half_agent": types.ModuleType("half_agent")}):
            agents = shell.load_agents(["no_such_agent_xyz", "half_agent", "inode_symlink_agent"],
                                       out=notes.append)
        self.assertEqual(len(agents), 1)
        self.assertEqual(len(notes), 2)

    def test_routes_link_requests_to_us_and_others_elsewhere(self):
        weather = fake_agent("weather", "Weather forecast agent: temperature, rain, "
                             "forecast for a city today or tomorrow.", "sunny")
        with mock.patch.dict(sys.modules, {"weather": weather}):
            agents = shell.load_agents(["inode_symlink_agent", "weather"], out=lambda *_: None)
        pick = lambda p: (shell.route(p, agents) or {}).get("name")
        for p in ("find broken shortcuts on my desktop and delete them",
                  "am I running out of inodes?", "which files are hard links of each other",
                  "any symlink loops in /tmp?"):
            self.assertEqual(pick(p), "inode_symlink_auditor", p)
        self.assertEqual(pick("what is the forecast for Vellore tomorrow"), "weather")
        self.assertIsNone(pick("open firefox"))


class ShellLoopTests(TempTree):
    def run_shell(self, lines, agents, confirm):
        it = iter(lines)

        def fake_input(prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError
        out = []
        code = shell.run(agents, input_fn=fake_input, out=out.append, confirm=confirm)
        return code, "\n".join(str(x) for x in out)

    def test_end_to_end_delete_through_the_base_shell(self):
        """User -> base shell -> our agent -> fake LLM -> tool -> shell's y/N callback."""
        b = self.link("gone", "b1")
        client = FakeClient(
            tool_call("find_broken_symlinks", {"path": self.tree}), text("One broken link."),
            tool_call("delete_broken_symlinks", {"paths": [b]}, "d1"), text("Deleted it."))
        asked = []
        with mock.patch.object(inode_symlink_agent, "handle",
                               functools.partial(agent.handle, client=client)):
            agents = shell.load_agents(["inode_symlink_agent"], out=lambda *_: None)
            code, out = self.run_shell(
                ["find broken shortcuts in " + self.tree, "delete them", "exit"], agents,
                confirm=lambda preview: asked.append(preview) or True)
        self.assertEqual(code, 0)
        self.assertIn("[shell] -> inode_symlink_auditor", out)
        self.assertIn("Deleted it.", out)
        self.assertEqual(len(asked), 1)                      # the shell's callback was asked
        self.assertIn(b, asked[0])
        self.assertFalse(os.path.lexists(b))

    def test_declining_in_the_base_shell_keeps_the_link(self):
        b = self.link("gone", "b1")
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [b]}))
        with mock.patch.object(inode_symlink_agent, "handle",
                               functools.partial(agent.handle, client=client)):
            agents = shell.load_agents(["inode_symlink_agent"], out=lambda *_: None)
            code, out = self.run_shell(["delete the broken link " + b], agents,
                                       confirm=lambda preview: False)
        self.assertIn("OK, nothing was deleted.", out)
        self.assertTrue(os.path.islink(b))

    def test_agents_listing_unknown_requests_and_exit(self):
        agents = shell.load_agents(["inode_symlink_agent"], out=lambda *_: None)
        code, out = self.run_shell(["", "agents", "open firefox", "quit", "never reached"],
                                   agents, confirm=lambda p: False)
        self.assertEqual(code, 0)
        self.assertIn("- inode_symlink_auditor:", out)
        self.assertIn("no agent matches", out)

    def test_without_an_llm_key_the_agent_says_so(self):
        with mock.patch.dict(os.environ, {"AGENT_LLM_API_KEY": "",
                                          "AGENT_LLM_BASE_URL": "https://example.invalid/v1"}), \
                mock.patch("inode_symlink_agent.llm.load_dotenv"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                shell.main(["find broken shortcuts in " + self.tree])
        self.assertIn("No LLM API key", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
