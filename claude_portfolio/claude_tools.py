"""
Bridges the existing Google tools (gtools/) into the Claude Agent SDK.

The plain functions in gtools/ are exactly the ones the ADK version uses —
same safety boundaries (writes only to ADK_Agents_Workspace, Gmail drafts
only, no deletion anywhere). This module wraps each one as an SDK MCP tool
so Claude agents can call them.

Tool names as agents see them: mcp__gtools__<function_name>,
e.g. mcp__gtools__drive_list_files.
"""

from __future__ import annotations

import asyncio
import inspect
import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from gtools.drive_tools import ALL_DRIVE_TOOLS
from gtools.google_tools import (
    CALENDAR_TOOLS,
    CONTACT_TOOLS,
    GMAIL_TOOLS,
    TASK_TOOLS,
)
from gtools.workspace_tools import DOCS_TOOLS, SHEETS_TOOLS, SLIDES_TOOLS

_TYPE_MAP = {
    "int": "integer",
    "bool": "boolean",
    "float": "number",
    int: "integer",
    bool: "boolean",
    float: "number",
}


def _schema_for(fn) -> dict:
    """Build a JSON schema from the function signature.

    The gtools functions take only str/int/bool parameters, all required
    (documented in gtools/google_tools.py — optional-in-practice values are
    passed as "" or 0).
    """
    props = {}
    for p in inspect.signature(fn).parameters.values():
        json_type = _TYPE_MAP.get(p.annotation, "string")
        props[p.name] = {"type": json_type}
    return {"type": "object", "properties": props, "required": list(props)}


def _wrap(fn):
    """Wrap a plain gtools function as an async SDK MCP tool."""
    description = inspect.getdoc(fn) or fn.__name__

    @tool(fn.__name__, description, _schema_for(fn))
    async def handler(args: dict):
        try:
            # gtools functions are blocking (googleapiclient) — run off-loop.
            result = await asyncio.to_thread(fn, **args)
        except Exception as exc:  # surface the error to the agent, don't crash
            result = {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, default=str),
                }
            ]
        }

    return handler


_ALL_FUNCTIONS = (
    list(ALL_DRIVE_TOOLS)
    + list(CALENDAR_TOOLS)
    + list(TASK_TOOLS)
    + list(CONTACT_TOOLS)
    + list(GMAIL_TOOLS)
    + list(SHEETS_TOOLS)
    + list(DOCS_TOOLS)
    + list(SLIDES_TOOLS)
)

GTOOLS_SERVER = create_sdk_mcp_server(
    name="gtools",
    version="1.0.0",
    tools=[_wrap(fn) for fn in _ALL_FUNCTIONS],
)


def _names(fns) -> list[str]:
    return [f"mcp__gtools__{fn.__name__}" for fn in fns]


# Tool-name groups for agents.py — mirrors the ADK tool groupings.
DRIVE = _names(ALL_DRIVE_TOOLS)
CALENDAR = _names(CALENDAR_TOOLS)
TASKS = _names(TASK_TOOLS)
CONTACTS = _names(CONTACT_TOOLS)
GMAIL = _names(GMAIL_TOOLS)
SHEETS = _names(SHEETS_TOOLS)
DOCS = _names(DOCS_TOOLS)
SLIDES = _names(SLIDES_TOOLS)
ALL_GTOOLS = _names(_ALL_FUNCTIONS)
