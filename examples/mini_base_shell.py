"""A minimal stand-in for the Agentic Shell's base shell.

The class will build the real base shell later. This file shows, with working code, how
that shell can find our agent and call it, so the agent can be demonstrated end to end
today. Standard library only.

How it works:
  1. AGENT_MODULES lists the agent packages to load (later: all 35 class agents).
  2. Every agent exposes AGENT_NAME, AGENT_DESCRIPTION and handle(prompt, confirm=...).
  3. route() picks an agent for each request. Here it is a simple word-overlap router; the
     real base shell can instead send every AGENT_DESCRIPTION to an LLM and ask which agent
     fits.
  4. The shell calls agent.handle() with its own y/N confirm callback, so every change an
     agent wants to make is still approved by the user.

Run from the repository root:
  python3 examples/mini_base_shell.py                    (interactive)
  python3 examples/mini_base_shell.py "find broken shortcuts in /tmp/agent10_demo"
"""
import importlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

AGENT_MODULES = ["inode_symlink_agent"]   # add the other class agents here

_STOP = set("""a an the and or of to in on for with from by is are be it its this that these
those my me i you your can do does what which who how when where why all any some about into
than then them there their please show find check tell list give make""".split())


def _stems(text):
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) >= 3 and w not in _STOP}


def load_agents(modules=AGENT_MODULES, out=print):
    """Import each agent module and keep the ones that follow the agent contract."""
    agents = []
    for name in modules:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:                       # one broken agent must not stop the shell
            out("[shell] could not load %s: %s" % (name, exc))
            continue
        missing = [a for a in ("AGENT_NAME", "AGENT_DESCRIPTION", "handle") if not hasattr(mod, a)]
        if missing:
            out("[shell] skipping %s: missing %s" % (name, ", ".join(missing)))
            continue
        history = mod.new_history() if hasattr(mod, "new_history") else None
        agents.append({"name": mod.AGENT_NAME, "description": mod.AGENT_DESCRIPTION,
                       "module": mod, "stems": _stems(mod.AGENT_DESCRIPTION),
                       "history": history})
    return agents


def route(prompt, agents):
    """Return the agent whose description shares the most words with the request."""
    words = _stems(prompt)
    best, best_score = None, 0
    for agent in agents:
        score = len(words & agent["stems"])
        if score > best_score:
            best, best_score = agent, score
    return best


def ask_yes_no(preview):
    """The base shell's confirmation callback: show the agent's preview, ask y/N."""
    print(preview)
    try:
        return input("Apply these changes? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def dispatch(prompt, agents, out=print, confirm=ask_yes_no):
    agent = route(prompt, agents)
    if agent is None:
        out("[shell] no agent matches that request. Type 'agents' to see what is available.")
        return None
    out("[shell] -> %s" % agent["name"])
    kwargs = {"confirm": confirm}
    if agent["history"] is not None:
        kwargs["history"] = agent["history"]          # keeps follow-ups like "delete them"
    reply = agent["module"].handle(prompt, **kwargs)
    out(reply)
    return agent["name"]


def run(agents, input_fn=input, out=print, confirm=ask_yes_no):
    out("Agentic Shell (demo base shell). %d agent(s) loaded. Type 'agents', or 'exit'."
        % len(agents))
    while True:
        try:
            line = input_fn("agentic-shell> ").strip()
        except EOFError:
            out("")
            return 0
        except KeyboardInterrupt:
            out("")
            continue
        if not line:
            continue
        if line.lower() in ("exit", "quit"):
            return 0
        if line.lower() in ("agents", "help"):
            for a in agents:
                out("- %s: %s" % (a["name"], a["description"][:110] + "..."))
            continue
        dispatch(line, agents, out=out, confirm=confirm)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    agents = load_agents()
    if not agents:
        print("[shell] no agents could be loaded.")
        return 1
    if argv:
        dispatch(" ".join(argv), agents)
        return 0
    return run(agents)


if __name__ == "__main__":
    sys.exit(main())
