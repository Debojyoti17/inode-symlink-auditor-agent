import io
import json
import os
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from helpers import TempTree
import inode_symlink_agent as agent_pkg
from inode_symlink_agent import __main__ as cli, agent, llm


def tool_call(name, args, cid="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


def text(content):
    return {"role": "assistant", "content": content}


class FakeClient:
    """Returns scripted assistant messages and records what it was sent."""
    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def chat(self, messages, tools=None):
        self.calls.append((list(messages), tools))
        return self.script.pop(0)


class PublicApiTests(unittest.TestCase):
    def test_api_surface(self):
        self.assertTrue(agent_pkg.AGENT_NAME)
        self.assertIn("broken", agent_pkg.AGENT_DESCRIPTION)
        self.assertTrue(callable(agent_pkg.handle) and callable(agent_pkg.call_tool))

    def test_schemas_match_functions(self):
        import inspect
        from inode_symlink_agent import tools
        schemas = agent_pkg.get_tool_schemas()
        self.assertEqual({s["function"]["name"] for s in schemas}, set(tools.TOOLS))
        for s in schemas:
            fn = s["function"]
            self.assertEqual(s["type"], "function")
            self.assertTrue(len(fn["description"]) > 40)
            params = inspect.signature(tools.TOOLS[fn["name"]]).parameters
            for prop in fn["parameters"]["properties"]:
                self.assertIn(prop, params)
            for req in fn["parameters"]["required"]:
                self.assertIn(req, fn["parameters"]["properties"])
            self.assertNotIn("confirm", fn["parameters"]["properties"])
            self.assertNotIn("auto_confirm", fn["parameters"]["properties"])
            json.dumps(fn)

    def test_call_tool_errors(self):
        self.assertFalse(agent_pkg.call_tool("nope", {})["ok"])
        self.assertFalse(agent_pkg.call_tool("inspect_path", "{bad json")["ok"])
        self.assertFalse(agent_pkg.call_tool("inspect_path", {})["ok"])
        self.assertFalse(agent_pkg.call_tool("inspect_path", {"path": "/", "x": 1})["ok"])

    def test_llm_cannot_inject_auto_confirm(self):
        r = agent_pkg.call_tool("delete_broken_symlinks",
                                {"paths": ["/tmp/x"], "auto_confirm": True})
        self.assertFalse(r["ok"])
        self.assertIn("auto_confirm", r["error"])


class NaturalLanguageTests(TempTree):
    def setUp(self):
        super().setUp()
        self.b = self.link("gone", "b1")
        self.keep = self.write("keep.txt")

    def test_find_then_preview_then_delete_with_callback(self):
        client = FakeClient(
            tool_call("find_broken_symlinks", {"path": self.tree}),
            tool_call("delete_broken_symlinks", {"paths": [self.b], "dry_run": True}, "c2"),
            tool_call("delete_broken_symlinks", {"paths": [self.b], "dry_run": False}, "c3"),
            text("Deleted 1 broken link."))
        prompts = []
        reply = agent.handle("delete broken links", client=client,
                             confirm=lambda t: prompts.append(t) or True)
        self.assertEqual(reply, "Deleted 1 broken link.")
        self.assertFalse(os.path.lexists(self.b))
        self.assertTrue(os.path.exists(self.keep))
        self.assertEqual(len(prompts), 1)               # dry run did not ask
        msgs, tools_sent = client.calls[0]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Never guess paths", msgs[0]["content"])
        self.assertTrue(tools_sent)
        # tool results were fed back to the model
        last_msgs = client.calls[-1][0]
        self.assertEqual([m["role"] for m in last_msgs if m["role"] == "tool"], ["tool"] * 3)

    def test_no_confirmation_available_means_no_write(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}),
                            text("I could not delete it without your confirmation."))
        agent.handle("delete", client=client)
        self.assertTrue(os.path.islink(self.b))
        tool_msg = client.calls[-1][0][-1]
        self.assertIn("not_confirmed", tool_msg["content"])

    def test_declined(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}), text("ok"))
        agent.handle("delete", client=client, confirm=lambda t: False)
        self.assertTrue(os.path.islink(self.b))

    def test_auto_confirm_from_caller(self):
        client = FakeClient(tool_call("delete_broken_symlinks", {"paths": [self.b]}), text("ok"))
        agent.handle("delete", client=client, auto_confirm=True)
        self.assertFalse(os.path.lexists(self.b))

    def test_tool_call_limit_of_8(self):
        script = [tool_call("inode_usage", {"path": self.tree}, "c%d" % i) for i in range(9)]
        script.append(text("done"))
        client = FakeClient(*script)
        self.assertEqual(agent.handle("loop forever", client=client), "done")
        tool_msgs = [m for m in client.calls[-1][0] if m["role"] == "tool"]
        executed = [m for m in tool_msgs if "limit" not in m["content"]]
        self.assertEqual(len(executed), 8)
        self.assertEqual(len(tool_msgs), 9)               # the 9th was refused
        self.assertIsNone(client.calls[-1][1])           # tools withdrawn after the limit

    def test_llm_error_message(self):
        class Boom:
            def chat(self, *a, **k):
                raise llm.LLMError("timed out")
        self.assertIn("timed out", agent.handle("x", client=Boom()))

    def test_no_api_key(self):
        with mock.patch.dict(os.environ, {"AGENT_LLM_API_KEY": "",
                                          "AGENT_LLM_BASE_URL": "https://example.invalid/v1"}):
            with mock.patch("inode_symlink_agent.llm.load_dotenv"):
                reply = agent.handle("find broken links in ~")
        self.assertIn("No LLM API key", reply)
        self.assertIn("python3 -m inode_symlink_agent broken", reply)


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((self.path, self.headers.get("Authorization"), body))
        if body["model"] == "bad":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"nope"}')
            return
        out = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class HttpClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d/v1/" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_request_shape_and_response(self):
        c = llm.ChatClient(self.base, "sekret", "m1")
        msg = c.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
        self.assertEqual(msg["content"], "hello")
        path, auth, body = _Handler.seen[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth, "Bearer sekret")
        self.assertEqual(body["model"], "m1")
        self.assertEqual(body["tools"], [{"type": "function"}])

    def test_http_error_message(self):
        with self.assertRaises(llm.LLMError) as cm:
            llm.ChatClient(self.base, "k", "bad").chat([])
        self.assertIn("401", str(cm.exception))
        self.assertIn("key", str(cm.exception))

    def test_unreachable(self):
        with self.assertRaises(llm.LLMError):
            llm.ChatClient("http://127.0.0.1:9/v1", "k", "m", timeout=2).chat([])

    def test_from_env_and_dotenv(self):
        env = {"AGENT_LLM_BASE_URL": self.base, "AGENT_LLM_API_KEY": "abc",
               "AGENT_LLM_MODEL": "mm"}
        with mock.patch.dict(os.environ, env), mock.patch("inode_symlink_agent.llm.load_dotenv"):
            c = llm.ChatClient.from_env()
        self.assertEqual((c.api_key, c.model), ("abc", "mm"))

    def test_load_dotenv_does_not_override(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write('# c\nAGENT_T1="v1"\nexport AGENT_T2=v2\nAGENT_T3=fromfile\n')
        try:
            with mock.patch.dict(os.environ, {"AGENT_T3": "fromenv"}):
                os.environ.pop("AGENT_T1", None)
                os.environ.pop("AGENT_T2", None)
                llm.load_dotenv([fh.name])
                self.assertEqual(os.environ["AGENT_T1"], "v1")
                self.assertEqual(os.environ["AGENT_T2"], "v2")
                self.assertEqual(os.environ["AGENT_T3"], "fromenv")
                os.environ.pop("AGENT_T1"), os.environ.pop("AGENT_T2")
        finally:
            os.unlink(fh.name)


class CliTests(TempTree):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def setUp(self):
        super().setUp()
        self.b = self.link("gone", "b1")
        self.link("/etc/passwd", "pw")

    def test_direct_commands(self):
        for cmd, needle in (("scan", "broken symlinks:     1"), ("broken", "b1"),
                            ("loops", "loops under"), ("hardlinks", "Hard-link groups"),
                            ("inodes", "Inode usage"), ("suspicious", "/etc/passwd"),
                            ("inspect", "directory")):
            code, out = self.run_cli(cmd, self.tree)
            self.assertEqual(code, 0, cmd)
            self.assertIn(needle, out, cmd)

    def test_json_output(self):
        code, out = self.run_cli("broken", self.tree, "--json")
        self.assertEqual(json.loads(out)["count"], 1)

    def test_boundary_option(self):
        code, out = self.run_cli("suspicious", self.tree, "--boundary", self.root)
        self.assertIn("pw", out)

    def test_delete_broken_requires_yes_when_not_interactive(self):
        code, out = self.run_cli("delete-broken", self.tree)
        self.assertTrue(os.path.islink(self.b))
        self.assertIn("--yes", out)
        code, out = self.run_cli("delete-broken", self.tree, "--yes")
        self.assertFalse(os.path.lexists(self.b))
        self.assertTrue(os.path.islink(self.p("pw")))

    def test_multi_word_prompt_not_mistaken_for_command(self):
        self.assertFalse(cli._is_direct(["scan", "my", "desktop"]))
        self.assertTrue(cli._is_direct(["suspicious", "x", "--boundary", "y"]))
        self.assertFalse(cli._is_direct(["find broken links"]))

    def test_bad_path_exit_code(self):
        code, out = self.run_cli("broken", self.p("missing"))
        self.assertEqual(code, 1)
        self.assertIn("Error", out)


if __name__ == "__main__":
    unittest.main()
