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
| `delete_broken_symlinks` | **write** | unlinks only paths that are *still* symlinks and *still* broken at the moment of deletion; never a file or directory. (Loops are reported separately and not deleted by this tool.) |
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
Add `--json` for JSON output. Other options: `--no-recursive --max-depth N --max-entries N`.

### Natural-language mode (needs an LLM)

**Recommended provider: Groq** (free tier, works with this agent's plain-urllib client).
Get a key at <https://console.groq.com/keys>, then set it **for the current terminal only**
(nothing is saved to disk):

```bash
export AGENT_LLM_BASE_URL="https://api.groq.com/openai/v1"
export AGENT_LLM_API_KEY="paste-your-key-here"
export AGENT_LLM_MODEL="openai/gpt-oss-120b"      # fallback: openai/gpt-oss-20b
python3 -m inode_symlink_agent "find broken shortcuts on my desktop and delete them"
```

Model names change over time. To list the models your key can use right now:

```bash
curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $AGENT_LLM_API_KEY"
```

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

```python
from inode_symlink_agent import (AGENT_NAME, AGENT_DESCRIPTION,
                                 get_tool_schemas, call_tool, handle)

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
- `handle(..., client=obj)` accepts any object with `chat(messages, tools) -> message dict`,
  so the base shell can supply its own LLM client instead of this agent's urllib one.

## Files

```
inode_symlink_agent/  tools.py schemas.py safety.py llm.py agent.py report.py __main__.py
tests/                unittest suite (temp dirs, fake LLM, local HTTP stub; no network)
demo/                 make_demo.sh run_demo.sh cleanup.sh DEMO_SCRIPT.md
.env.example
```
