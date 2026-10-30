# Agent 10: Inode & Symlink Auditor Agent

Part of the **Agentic Shell** class project (a base shell routes a plain-English request to one
of 35 agents). This agent finds and cleans up **broken symlinks**, detects **symlink loops**,
manages **hard links**, and reports **inode** information. Example request:

> "Find all broken shortcuts on my desktop and delete them."

Python 3.8+ and the standard library only. No `pip install`, no `sudo`.
Core features use `os.lstat`, `os.readlink`, `os.statvfs`, `os.unlink`, `os.symlink`, `os.link`
(no `subprocess`).

> **AI assistance note:** this code was developed with AI assistance (Claude by Anthropic),
> which the course permits.

## How it works

1. You type a request in English.
2. The agent sends it, plus a list of **tools** (JSON function-calling schemas), to an LLM.
3. The LLM picks tools; the Python code does the real work with system calls and returns the
   result; the LLM turns it into a short plain-English answer (max 8 tool calls per request).

You can also skip the LLM entirely with **direct mode** (see below).

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `find_broken_symlinks` | read | dangling links (target missing) |
| `find_symlink_loops` | read | self-links and cycles (A→B→A), reports the chain |
| `inspect_path` | read | type, inode, device, link count, size, owner, permissions; for symlinks raw/resolved target, broken or not, relative or absolute |
| `find_hard_links` | read | groups of files sharing one (device, inode) with link count > 1. Only names inside the scanned tree are found; the result says how many names are *outside* it |
| `inode_usage` | read | total/used/free inodes and % used, like `df -i` |
| `find_suspicious_symlinks` | read | links leaving a boundary directory, pointing at `/etc`, `/root`, `~/.ssh`…, or sitting in world-writable directories, with the reason |
| `audit_summary` | read | one scan, counts of every issue type |
| `delete_broken_symlinks` | **write** | unlinks dead symlinks (target missing, or a symlink loop such as a self-link or A→B→A). Right before each unlink it re-checks that the path is *still* a symlink and *still* fails to resolve; never touches a working link, file or directory. The preview says why each link is removed |
| `create_symlink`, `create_hard_link` | **write** | create a link |
| `repoint_symlink` | **write** | change a link's target atomically (temp link + `os.replace`) |

Scanning rules: symlinks are **never followed** while walking; `/proc`, `/sys`, `/dev` are
skipped; defaults are max depth 8, max 50 000 entries and a 30 s time budget, so scanning `/`
or `~` cannot hang. Unreadable directories are skipped and listed in the report. `~`, `$VARS`
and the words `desktop`, `downloads`, `documents` are expanded to the matching home folders.

## Safety rules

- Every write tool has `dry_run` and returns a preview of exactly what would change.
- **Nothing is written silently.** In an interactive terminal you see the preview and answer
  `y/N`. When called by another program, nothing is written unless the caller passes a
  `confirm` callback or `auto_confirm=True`. The LLM cannot set these itself.
- Writes are refused inside `/`, `/bin /boot /dev /etc /lib* /proc /sbin /sys /usr /var`, and on
  anything you do not own.
- Every write (and every declined attempt) is logged as one JSON line in
  `~/.local/state/inode_symlink_agent/actions.log`.

## Setup on a fresh Ubuntu machine (copy-paste)

```bash
git clone https://github.com/Debojyoti17/inode-symlink-auditor-agent.git
cd inode-symlink-auditor-agent
python3 --version                           # needs 3.8 or newer
python3 -m unittest discover -s tests -v    # run the tests (no network, no key needed)
```

> **The repository is private.** On a shared lab PC, either clone over HTTPS and sign in with a
> GitHub **personal access token** instead of your password (enter your GitHub username, then
> paste the token when asked for a password; do not save it on a shared machine), or open the
> repo on GitHub and use **Code → Download ZIP**, then run `unzip` on it and `cd` into the folder.

### Try it without an LLM (direct mode)

```bash
./demo/make_demo.sh                                   # builds /tmp/agent10_demo
python3 -m inode_symlink_agent scan /tmp/agent10_demo
python3 -m inode_symlink_agent broken /tmp/agent10_demo/desktop
python3 -m inode_symlink_agent delete-broken /tmp/agent10_demo/desktop   # asks y/N
```

Commands: `scan PATH | broken PATH | loops PATH | hardlinks PATH | inodes PATH |
suspicious PATH [--boundary DIR] | inspect PATH | delete-broken PATH [--yes]`.
Add `--json` for JSON output. Other options: `--no-recursive --max-depth N --max-entries N`,
and `--fast` for chat mode (see below).

### Natural-language mode (needs an LLM)

**Recommended provider: Groq** (free tier, works with this agent's plain-urllib client).
Get a key at <https://console.groq.com/keys>, then set it **for the current terminal only**
(nothing is saved to disk):

```bash
export AGENT_LLM_BASE_URL="https://api.groq.com/openai/v1"
export AGENT_LLM_API_KEY="paste-your-key-here"
export AGENT_LLM_MODEL="openai/gpt-oss-120b"      # fallback: openai/gpt-oss-20b
python3 -m inode_symlink_agent          # chat mode (recommended)
```

Model names change over time. To list the models your key can use right now:

```bash
curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $AGENT_LLM_API_KEY"
```

**Chat mode (the main way to use and demo the agent).** Run with no arguments:

```
$ python3 -m inode_symlink_agent
agent10> find the broken shortcuts in /tmp/agent10_demo/desktop
agent10> delete them
  (the program prints the exact preview and asks "Apply these changes? [y/N]")
agent10> now check my Desktop
agent10> exit
```

The conversation is remembered between turns (older turns are trimmed, but a small "session
note" with the current folder, the last few actions and the items found is always sent), so
follow-ups like "yes, delete them" or "now
check my Desktop" work. Type `exit`, `quit` or press Ctrl+D to leave. The y/N question is
asked by the program itself for every write, never by the LLM, so you always see the preview
first.

**One request at a time** also works, but a one-shot run cannot answer follow-up questions:

```bash
python3 -m inode_symlink_agent "find broken shortcuts on my desktop and delete them"
```

If the program is not run in a terminal (no way to ask y/N), writes are refused unless you
add `--yes`.

Or copy `.env.example` to `.env` and edit it (`.env` is git-ignored; real environment
variables win over `.env`). Never commit a key.

| Provider | `AGENT_LLM_BASE_URL` | `AGENT_LLM_MODEL` (example) | Key |
|---|---|---|---|
| **Groq (recommended)** | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` (fallback `openai/gpt-oss-20b`) | <https://console.groq.com/keys> |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | from platform.openai.com |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` (a tool-calling model) | any text, or empty for localhost |
| Google Gemini (see warning) | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` | Google AI Studio |

> **Gemini warning:** Google AI Studio now issues new keys that start with `AQ.`. In our
> testing these fail on both the OpenAI-compatible endpoint ("Invalid Auth key") and Google's
> native endpoint ("ACCESS_TOKEN_TYPE_UNSUPPORTED"). Gemini only works if you still have an
> older `AIza...` API key; otherwise use Groq.

The model must support tool calling. If a model name is rejected (HTTP 404), list the current
models as shown above. The client sends an explicit `User-Agent` header because some
providers' Cloudflare protection blocks Python's default one (HTTP 403, "error code: 1010").

**Groq free-tier limits.** The free tier limits tokens per minute (for example 8000 TPM for
`openai/gpt-oss-120b`). Every request carries the system prompt, the tool list and the chat
history, roughly 2000 tokens or more, so several quick questions in a row can hit the limit.
What the agent does about it:

- On an HTTP 429 (rate limit) or 503 it waits as long as the server asks (from the
  `Retry-After` header or the "try again in 17.5s" text), prints
  `Rate limit reached, waiting 17.5s...` and retries, at most 3 times and at most about
  60 seconds of waiting in total. If the server asks for longer, it stops with a clear message.
- Requests are kept small: short prompt and tool descriptions, at most 50 list items sent to
  the LLM (with a count of the rest; write previews and direct mode always show everything),
  and chat mode remembers only the last 6 turns, keeping tool output for the latest 2.
- A write request needs a single tool call (no extra dry run), and the program asks y/N itself.

**Faster on the free tier:** start chat mode with `--fast`
(`python3 -m inode_symlink_agent --fast`, or `export AGENT_FAST=1`). The agent then answers with
its own report of the tool result instead of asking the LLM a second time, so each request needs
one LLM call instead of two: about half the tokens and half the waiting. If a tool fails, the LLM
is still asked so it can correct itself. With Groq's gpt-oss models you can also cut the model's
hidden "thinking" tokens with `export AGENT_LLM_REASONING_EFFORT=low` (leave it unset for other
models; if the provider rejects it, run `unset AGENT_LLM_REASONING_EFFORT`).

Tips: wait a few seconds between requests, and use `--verbose` (for example
`python3 -m inode_symlink_agent --verbose`) to see the approximate number of tokens sent with
each request, plus the API's own count when it reports one. Direct mode never uses the LLM
and has no rate limit.

With no key set, the agent says so and points you to direct mode. Add `--yes` to approve
writes without the y/N prompt.

### Demo and cleanup

```bash
./demo/make_demo.sh     # build /tmp/agent10_demo
./demo/run_demo.sh      # direct commands, then NL prompts (skipped without a key)
./demo/cleanup.sh       # remove /tmp/agent10_demo
```

`demo/DEMO_SCRIPT.md` is a step-by-step script for presenting.

## Plugging into a base shell

The class's base shell is not built yet, so the agent is ready for it in two ways:

1. **A fixed contract.** Every agent exposes `AGENT_NAME`, `AGENT_DESCRIPTION` and
   `handle(prompt, confirm=...)`. The base shell only needs these to find, choose and call it.
2. **A working example base shell**, `examples/mini_base_shell.py`. It loads the agents listed
   in `AGENT_MODULES`, routes each request to the best-matching agent (by its description),
   and calls `handle()` with its own y/N confirmation, keeping a chat history per agent:

```
$ python3 examples/mini_base_shell.py
agentic-shell> agents
- inode_symlink_auditor: Inode & Symlink Auditor: finds and cleans up broken (dangling) ...
agentic-shell> find broken shortcuts in /tmp/agent10_demo/desktop
[shell] -> inode_symlink_auditor
...
agentic-shell> exit
```

When the real base shell exists, adding this agent is one line (`"inode_symlink_agent"` in its
list of agents); its router can be smarter (for example an LLM that reads every
`AGENT_DESCRIPTION`), but the call stays the same. The API in detail:

```python
from inode_symlink_agent import (AGENT_NAME, AGENT_DESCRIPTION,
                                 get_tool_schemas, call_tool, handle, new_history)

AGENT_NAME            # "inode_symlink_auditor"
AGENT_DESCRIPTION     # paragraph for the router LLM, with example requests
get_tool_schemas()    # list of OpenAI-style function schemas

# Run one tool yourself (always returns a dict):
call_tool("find_broken_symlinks", {"path": "desktop"})
call_tool("delete_broken_symlinks", {"paths": ["/home/me/x"]},
          confirm=lambda preview: input(preview + "\nOK? ") == "y")

# Let the agent's own LLM loop answer a plain-English request (returns a string):
reply = handle("audit my projects folder", confirm=my_confirm_callback)
```

- `confirm(preview_text) -> bool` is called before any write. With neither `confirm` nor
  `auto_confirm=True`, a non-interactive caller never causes a write.
- `handle(prompt, history=new_history())` keeps a conversation across calls (chat mode uses this);
  without `history` each call is a fresh one-shot request.
- `handle(..., client=obj)` accepts any object with `chat(messages, tools) -> message dict`,
  so the base shell can supply its own LLM client instead of this agent's urllib one.

## Files

```
inode_symlink_agent/  tools.py schemas.py safety.py llm.py agent.py context.py report.py __main__.py
examples/             mini_base_shell.py (a working stand-in for the class base shell)
tests/                unittest suite (temp dirs, fake LLM, local HTTP stub; no network)
demo/                 make_demo.sh run_demo.sh cleanup.sh DEMO_SCRIPT.md
.env.example
```
