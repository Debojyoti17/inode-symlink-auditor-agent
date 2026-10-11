"""Minimal OpenAI-compatible Chat Completions client (urllib only, no SDK)."""
import json
import os
import socket
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class LLMError(Exception):
    """A problem talking to the LLM, with a message safe to show the user."""


class LLMNotConfigured(LLMError):
    pass


def load_dotenv(paths=None):
    """Read KEY=VALUE lines from a .env file into os.environ (existing vars win)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in paths or [os.path.join(os.getcwd(), ".env"), os.path.join(here, ".env")]:
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[7:]
                    key, _, val = line.partition("=")
                    val = val.strip().strip('"').strip("'")
                    os.environ.setdefault(key.strip(), val)
        except OSError:
            continue


class ChatClient:
    def __init__(self, base_url, api_key, model, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls, timeout=60):
        load_dotenv()
        base = os.environ.get("AGENT_LLM_BASE_URL") or DEFAULT_BASE_URL
        key = os.environ.get("AGENT_LLM_API_KEY", "")
        model = os.environ.get("AGENT_LLM_MODEL") or DEFAULT_MODEL
        local = any(h in base for h in ("localhost", "127.0.0.1", "[::1]"))
        if not key and not local:
            raise LLMNotConfigured(
                "No LLM API key is set (AGENT_LLM_API_KEY). Natural-language mode needs one. "
                "See README.md / .env.example, or use direct mode that needs no LLM, e.g.: "
                "python3 -m inode_symlink_agent broken ~/Desktop")
        return cls(base, key or "none", model, timeout)

    def chat(self, messages, tools=None):
        """Send one request; return the assistant message dict."""
        body = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            finally:
                exc.close()
            hints = {401: "the API key was rejected", 403: "access denied (check key/permissions)",
                     404: "endpoint or model not found (check AGENT_LLM_BASE_URL and "
                          "AGENT_LLM_MODEL)",
                     429: "rate limit or quota exceeded; wait a bit and retry"}
            raise LLMError("LLM API error %d: %s. %s" % (
                exc.code, hints.get(exc.code, exc.reason), detail))
        except (socket.timeout, TimeoutError):
            raise LLMError("The LLM request timed out after %ss." % self.timeout)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LLMError("The LLM request timed out after %ss." % self.timeout)
            raise LLMError("Could not reach the LLM at %s: %s" % (self.base_url, exc.reason))
        except (ValueError, UnicodeDecodeError):
            raise LLMError("The LLM returned a response that is not valid JSON.")
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected LLM response format: %s" % json.dumps(data)[:300])
        msg.setdefault("role", "assistant")
        return msg
