# Demo script: Agent 10, Inode & Symlink Auditor (about 8 minutes)

Before class: `git clone` done, `python3 -m unittest discover -s tests` is green, and for Part C
the key is exported in the terminal you will present from (see README). Part C is optional.

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

## C. Safety: deleting (2 min)
1. `python3 -m inode_symlink_agent delete-broken /tmp/agent10_demo/desktop`
   **See:** a preview of exactly 2 links, then `Apply these changes? [y/N]`.
   **Do:** type `n`. **See:** "Nothing was changed". Run `broken` again: still 2.
   **Say:** "Default is no. Called from another program, it never writes unless the caller
   confirms."
2. Run it again and type `y`. **See:** 2 links removed; `loops`/`scan` show everything else
   untouched (the regular files and healthy links survive).
   **Say:** "Right before each unlink we re-check it is still a symlink and still broken,
   so a regular file or a link that became valid is never removed."
3. `tail -3 ~/.local/state/inode_symlink_agent/actions.log`
   **See:** one JSON line per action with time and result.
4. Mention: writes inside system paths (/etc, /usr, ...) and on files you do not own are refused.
   system paths are refused.

## D. Natural language (2 min, needs the key)
Re-run `./demo/make_demo.sh` to restore the broken links, then:
1. `python3 -m inode_symlink_agent "Audit the folder /tmp/agent10_demo and tell me what is wrong"`
   **See:** `[tool] audit_summary ...` then a short English summary.
2. `python3 -m inode_symlink_agent "Find all broken shortcuts in /tmp/agent10_demo/desktop and delete them"`
   **See:** `[tool]` lines for find, dry-run preview, then the y/N prompt; answer `y`.
   **Say:** "The LLM chose the tools and the order. It cannot confirm for us; the y/N comes
   from our code."
3. If the LLM or network misbehaves, fall back to Part B and say: "Direct mode needs no LLM."

## E. Wrap up (30 s)
`python3 -m unittest discover -s tests` -> all green. `./demo/cleanup.sh`.
**Say:** "For the base shell: import `AGENT_DESCRIPTION`, `get_tool_schemas`, `call_tool`
and `handle`. Standard library only. Developed with AI assistance (Claude), as the course permits."
