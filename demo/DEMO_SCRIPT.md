# Demo script: Agent 10, Inode & Symlink Auditor (about 8 minutes)

Before class: `git clone` done, `python3 -m unittest discover -s tests` is green, and for Part C
the key is exported in the terminal you will present from (see README). Part C is optional (it needs the key); Part D is the no-LLM fallback.

## A. Set the scene (30 s)
**Say:** "Our agent audits symlinks and hard links. The LLM chooses tools; our Python code does
the real work with `lstat`, `readlink`, `statvfs` and `unlink`. It never follows symlinks while
scanning, and it never writes without confirmation."

## B. Direct mode, no LLM (3 min)
1. `./demo/make_demo.sh`
   **See:** a folder with 2 broken links, 3 loop links, 1 suspicious link and 3 hard-linked files
   (same inode number printed three times).
   **Say:** "This is a fixed test bed. It is in /tmp and needs no sudo."
2. `python3 -m inode_symlink_agent scan /tmp/agent10_demo`
   **See:** counts: 2 broken, 3 loops, 1 suspicious, 1 hard-link group.
   **Say:** "One bounded scan: depth and entry limits mean scanning `/` can't hang."
3. `python3 -m inode_symlink_agent broken /tmp/agent10_demo/desktop`
   **See:** the two dangling links, with raw targets, relative vs absolute.
4. `python3 -m inode_symlink_agent loops /tmp/agent10_demo/desktop`
   **See:** `self-link [self-link]` and `loop-a`/`loop-b` with the chain A -> B -> A.
   **Say:** "The kernel reports ELOOP for these; we trace the chain by hand with readlink."
5. `python3 -m inode_symlink_agent hardlinks /tmp/agent10_demo`
   **See:** one inode, link count 3, three names.
   **Say:** "We can only find names inside the scanned folder; the report says how many
   names are outside."
6. `python3 -m inode_symlink_agent suspicious /tmp/agent10_demo`
   **See:** `passwd-shortcut -> /etc/passwd` flagged HIGH with the reason.
7. `python3 -m inode_symlink_agent inspect /tmp/agent10_demo/desktop/old-project` and
   `python3 -m inode_symlink_agent inodes /tmp/agent10_demo`
   **See:** inode, device, link count, owner, permissions, BROKEN status; then inode usage like `df -i`.

## C. Chat mode: the main demo (4 min, needs the key)
Run `./demo/make_demo.sh` again so the broken links are back, then start chat mode:
`python3 -m inode_symlink_agent` (prompt: `agent10>`).
**Say:** "One conversation. The LLM chooses the tools; our code does the work and owns the
safety gate."
1. `agent10> Audit /tmp/agent10_demo and tell me what is wrong`
   **See:** a `[tool] audit_summary ...` line, then a short plain-text summary.
2. `agent10> Find the broken shortcuts in /tmp/agent10_demo/desktop`
   **See:** `[tool] find_broken_symlinks ...` and the two dangling links named.
3. `agent10> delete them`
   **See:** the program (not the LLM) prints the exact preview and asks
   `Apply these changes? [y/N]`. Type `n` first. **See:** "Nothing was changed".
   **Say:** "The default is no. The LLM never asks for permission in text; the program does."
4. `agent10> yes, delete them` then type `y` at the prompt.
   **See:** both links removed. **Say:** "It remembered the earlier results: chat keeps history.
   Right before each unlink we re-check the path is still a broken symlink, so a regular
   file or a link that became valid is never removed."
5. `agent10> now check /tmp/agent10_demo/desktop for loops and suspicious links`
   **See:** the 3 loop links and `passwd-shortcut -> /etc/passwd` with the reasons.
6. `agent10> exit` (or Ctrl+D). Then `tail -3 ~/.local/state/inode_symlink_agent/actions.log`
   **See:** one JSON line per write with time and result.
   Also mention: writes inside system paths (/etc, /usr, ...) and on files you do not own
   are refused.

## D. Safety without an LLM (1 min, fallback if the network or LLM misbehaves)
`./demo/make_demo.sh`, then `python3 -m inode_symlink_agent delete-broken /tmp/agent10_demo/desktop`
**See:** a preview of 5 links (2 with a missing target, 3 symlink loops), each with the reason,
and the y/N prompt. Type `n`, then run `broken` to show nothing changed.
**Say:** "Direct mode needs no LLM. Called from another program, it never writes unless the
caller confirms."

## E. How it plugs into the base shell (1 min)
**Say:** "Sir asked the students to build the base shell later, so our agent is ready for it.
Any base shell only needs three things from us: AGENT_NAME, AGENT_DESCRIPTION and handle()."
1. `python3 examples/mini_base_shell.py` (prompt: `agentic-shell>`). Type `agents`.
   **See:** our agent listed with its description; this is what a base shell reads to route.
2. `agentic-shell> are there symlink loops in /tmp/agent10_demo/desktop?` (needs the key)
   **See:** `[shell] -> inode_symlink_auditor`, then our agent's answer.
3. `agentic-shell> open firefox` **See:** "no agent matches", because no such agent is loaded.
   **Say:** "Adding our agent to the real base shell is one line in its agent list; any change
   is still confirmed with y/N through the base shell's own callback."

## F. Wrap up (30 s)
`python3 -m unittest discover -s tests` -> all green. `./demo/cleanup.sh`.
**Say:** "For the base shell: import `AGENT_DESCRIPTION`, `get_tool_schemas`, `call_tool`
and `handle`. Standard library only. Developed with AI assistance (Claude), as the course permits."
