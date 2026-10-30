import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from inode_symlink_agent import tools  # noqa: E402


class TempTree(unittest.TestCase):
    """Gives each test a temp dir (self.root) and a private actions log."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="agent10_test_"))
        self.log = os.path.join(self.root, "_actions.log")
        self._old_log = os.environ.get("AGENT_ACTION_LOG")
        os.environ["AGENT_ACTION_LOG"] = self.log
        self.tree = os.path.join(self.root, "tree")
        os.mkdir(self.tree)
        # Tests must behave identically with or without a real terminal: force
        # non-interactive mode, and fail loudly if anything ever tries to prompt.
        env = mock.patch.dict(os.environ)             # restore the environment afterwards
        env.start()
        self.addCleanup(env.stop)
        for var in ("AGENT_FAST", "AGENT_LLM_REASONING_EFFORT"):
            os.environ.pop(var, None)                 # a developer's shell must not change tests
        patches = [mock.patch("inode_symlink_agent.safety.is_interactive", return_value=False),
                   mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt"))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        if self._old_log is None:
            os.environ.pop("AGENT_ACTION_LOG", None)
        else:
            os.environ["AGENT_ACTION_LOG"] = self._old_log
        for dp, dns, _ in os.walk(self.root):
            for d in dns:
                try:
                    os.chmod(os.path.join(dp, d), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tree, *parts)

    def write(self, *parts, text="data"):
        path = self.p(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def link(self, target, *parts):
        path = self.p(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        return path

    def log_text(self):
        try:
            with open(self.log) as fh:
                return fh.read()
        except OSError:
            return ""
