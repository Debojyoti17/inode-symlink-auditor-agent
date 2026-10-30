"""Minimal OpenAI-compatible Chat Completions client (urllib only, no SDK)."""
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
# Some providers (e.g. Groq behind Cloudflare) reject urllib's default "Python-urllib/x.y".
USER_AGENT = "inode-symlink-auditor-agent/1.0"


MAX_RETRIES = 3          # extra attempts after a 429 / 503
MAX_TOTAL_WAIT = 60.0    # seconds we are willing to wait in total for one request
RETRY_STATUSES = (429, 503)


class _Retryable(Exception):
    def __init__(self, status, wait, detail):
        super().__init__(status)
        self.status, self.wait, self.detail = status, wait, detail


def parse_wait_seconds(retry_after=None, body_text=""):
    """Seconds to wait from a Retry-After header or a 'try again in 1m2.5s' message."""
    if retry_after:
        try:
            return max(0.0, float(str(retry_after).strip()))
        except ValueError:
            pass
    m = re.search(r"try again in\s+((?:\d+(?:\.\d+)?\s*(?:ms|[hms])\s*)+)", body_text or "",
                  re.I)
    if m:
        total = 0.0
        for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|[hms])", m.group(1), re.I):
            total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit.lower()]
        return total
    return None


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
    def __init__(self, base_url, api_key, model, timeout=60, verbose=False,
                 reasoning_effort=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.verbose = verbose
        # Optional, e.g. "low" for Groq's gpt-oss models: fewer thinking tokens, faster
        self.reasoning_effort = reasoning_effort or None
        self.max_retries = MAX_RETRIES
        self.max_total_wait = MAX_TOTAL_WAIT
        self.sleep = time.sleep                                   # replaced in tests
        self.notify = lambda text: print(text, file=sys.stderr)  # progress messages

    @classmethod
    def from_env(cls, timeout=60, verbose=False):
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
        return cls(base, key or "none", model, timeout, verbose,
                   os.environ.get("AGENT_LLM_REASONING_EFFORT", "").strip() or None)

    def chat(self, messages, tools=None):
        """Send one request; return the assistant message dict.

        A 429 / 503 is retried (up to MAX_RETRIES times, MAX_TOTAL_WAIT seconds in total),
        waiting as long as the server asks.
        """
        body = {"model": self.model, "messages": messages}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        waited = 0.0
        for attempt in range(self.max_retries + 1):
            try:
                return self._post(payload)
            except _Retryable as r:
                what = "Rate limit reached" if r.status == 429 else "LLM service busy (503)"
                if attempt == self.max_retries:
                    raise LLMError("%s and still failing after %d retries. %s"
                                   % (what, self.max_retries, r.detail))
                wait = (r.wait if r.wait is not None else 2.0 * (attempt + 1)) + 0.5
                if waited + wait > self.max_total_wait:
                    raise LLMError("%s; the server asks for %.0fs, longer than the %.0fs "
                                   "this program will wait. Try again later. %s"
                                   % (what, wait, self.max_total_wait, r.detail))
                self.notify("%s, waiting %.1fs..." % (what, wait))
                self.sleep(wait)
                waited += wait

    def _post(self, payload):
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": USER_AGENT,
                     "Authorization": "Bearer " + self.api_key}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")[:4000]
            except Exception:
                pass
            finally:
                exc.close()
            detail = raw[:300]
            if exc.code in RETRY_STATUSES:
                raise _Retryable(exc.code, parse_wait_seconds(
                    exc.headers.get("Retry-After") if exc.headers else None, raw),
                    detail.strip())
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
        if self.verbose:
            usage = data.get("usage") or {}
            extra = (" (API reported %s total)" % usage["total_tokens"]
                     if "total_tokens" in usage else "")
            print("[llm] request ~%d tokens%s" % (len(payload) // 4, extra), file=sys.stderr)
        return msg
