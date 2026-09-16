"""
Nepal Portfolio — Claude Agent SDK server (v2, streaming).

FastAPI wrapper around the Claude Agent SDK: the CEO agent runs as the main
loop and delegates to the specialist subagents in agents.py. Serves the chat
console from web/ and a streaming JSON API:

    GET  /            -> the chat console (live agent network view)
    POST /chat        -> {"session_id": "...", "message": "..."}
                          streams NDJSON events, one JSON object per line:
                          {"type":"start"}
                          {"type":"agent","name":"landed_cost"}      CEO delegated
                          {"type":"tool","name":"drive_read_file"}   a tool ran
                          {"type":"text","text":"..."}               reply text chunk
                          {"type":"done","cost_usd":0.0}             turn finished
                          {"type":"error","message":"..."}
    GET  /health      -> {"ok": true}

Auth model (single-user):
- Claude side: CLAUDE_CODE_OAUTH_TOKEN (Max plan) or ANTHROPIC_API_KEY in .env.
- HTTP side: PORTFOLIO_UI_KEY in the X-Portfolio-Key header on every /chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets

from dotenv import load_dotenv

load_dotenv()  # .env in the project root — before agents/tools import config

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from agents import AGENTS, CEO_PROMPT  # noqa: E402
from claude_tools import ALL_GTOOLS, GTOOLS_SERVER  # noqa: E402

MODEL = os.environ.get("PORTFOLIO_MODEL", "sonnet")
UI_KEY = os.environ.get("PORTFOLIO_UI_KEY", "")
MAX_SESSIONS = 20

app = FastAPI(title="Nepal Portfolio Agents")


def _options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=CEO_PROMPT,
        agents=AGENTS,
        mcp_servers={"gtools": GTOOLS_SERVER},
        allowed_tools=ALL_GTOOLS + ["WebSearch", "Task"],
        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep"],
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=40,
    )


class _Session:
    def __init__(self) -> None:
        self.client: ClaudeSDKClient | None = None
        self.lock = asyncio.Lock()

    async def ensure(self) -> ClaudeSDKClient:
        if self.client is None:
            self.client = ClaudeSDKClient(options=_options())
            await self.client.connect()
        return self.client

    async def reset(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None


_sessions: dict[str, _Session] = {}
_sessions_lock = asyncio.Lock()


async def _get_session(session_id: str) -> _Session:
    async with _sessions_lock:
        if session_id not in _sessions:
            if len(_sessions) >= MAX_SESSIONS:
                oldest_id, oldest = next(iter(_sessions.items()))
                if not oldest.lock.locked():
                    _sessions.pop(oldest_id)
                    await oldest.reset()
            _sessions[session_id] = _Session()
        return _sessions[session_id]


def _events_from(msg) -> list[dict]:
    """Translate an SDK message into UI events."""
    events: list[dict] = []
    if isinstance(msg, AssistantMessage):
        from_subagent = getattr(msg, "parent_tool_use_id", None) is not None
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                if block.name == "Task":
                    inp = block.input or {}
                    events.append({
                        "type": "agent",
                        "name": str(inp.get("subagent_type", "")),
                        "task": str(inp.get("description", ""))[:120],
                    })
                else:
                    name = block.name
                    if name.startswith("mcp__gtools__"):
                        name = name[len("mcp__gtools__"):]
                    events.append({"type": "tool", "name": name, "sub": from_subagent})
            elif isinstance(block, TextBlock) and not from_subagent:
                if block.text.strip():
                    events.append({"type": "text", "text": block.text})
    elif isinstance(msg, ResultMessage):
        events.append({
            "type": "done",
            "cost_usd": getattr(msg, "total_cost_usd", None),
            "is_error": bool(getattr(msg, "is_error", False)),
        })
    return events


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "index.html"))


@app.post("/chat")
async def chat(req: ChatRequest, x_portfolio_key: str = Header(default="")):
    if not UI_KEY:
        raise HTTPException(500, "PORTFOLIO_UI_KEY is not set in .env")
    if not secrets.compare_digest(x_portfolio_key, UI_KEY):
        raise HTTPException(401, "Wrong or missing access key")
    if not req.message.strip():
        raise HTTPException(400, "Empty message")

    session = await _get_session(req.session_id)

    async def gen():
        yield json.dumps({"type": "start"}) + "\n"
        async with session.lock:  # one turn at a time per session
            try:
                client = await session.ensure()
                await client.query(req.message)
                async for msg in client.receive_response():
                    for ev in _events_from(msg):
                        yield json.dumps(ev, ensure_ascii=False) + "\n"
            except Exception as exc:
                await session.reset()  # next message starts clean
                yield json.dumps({
                    "type": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                }) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
