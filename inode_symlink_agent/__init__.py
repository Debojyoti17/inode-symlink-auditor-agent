"""Agent 10: Inode & Symlink Auditor Agent.

Public API for a base shell::

    from inode_symlink_agent import AGENT_NAME, AGENT_DESCRIPTION, get_tool_schemas, \
        call_tool, handle
"""
from .agent import (AGENT_DESCRIPTION, AGENT_NAME, AGENT_TITLE, call_tool, handle,
                    get_tool_schemas)

__all__ = ["AGENT_NAME", "AGENT_TITLE", "AGENT_DESCRIPTION", "get_tool_schemas",
           "call_tool", "handle"]
