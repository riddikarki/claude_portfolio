"""
Nepal Portfolio — Claude Agent SDK server.

FastAPI wrapper around the Claude Agent SDK: the CEO agent runs as the main
loop and delegates to the specialist subagents in agents.py. Serves the chat
console from web/ and a small JSON API:

    GET  /            -> the chat console
    POST /chat        -> {"session_id": "...", "message": "..."}
                          returns {"reply": "...", "cost_usd": ...}
    GET  /health      -> {"ok": true}

Auth model (single-user):
- The Claude side authenticates with CLAUDE_CODE_OAUTH_TOKEN (Max plan) or
  ANTHROPIC_API_KEY from the environment — see .env.example.
- The HTTP side is protected by PORTFOLIO_UI_KEY: every /chat request must
  carry it in the X-Portfolio-Key header. The console asks for it once.

Run locally:      uvicorn server:app --host 0.0.0.0 --port 8080
On the VM:        via deploy/portfolio-claude.service (systemd)
"""

from __future__ import annotations

import asyncio
import os
import secrets

from dotenv import load_dotenv

load_dotenv()  # .env in the project root — before agents/tools import config

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from agents import AGENTS, CEO_PROMPT  # noqa: E402
from claude_tools import ALL_GTOOLS, GTOOLS_SERVER  # noqa: E402

MODEL = os.environ.get("PORTFOLIO_MODEL", "sonnet")
UI_KEY = os.environ.get("PORTFOLIO_UI_KEY", "")
MAX_SESSIONS = 20  # oldest idle session is dropped beyond this

app = FastAPI(title="Nepal Portfolio Agents")


def _options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=CEO_PROMPT,
        agents=AGENTS,
        mcp_servers={"gtools": GTOOLS_SERVER},
        allowed_tools=ALL_GTOOLS + ["WebSearch", "Task"],
        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep"],
        permission_mode="bypassPermissions",  # headless server, no one to ask
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


_sessions: dict[str, _Session] = {}
_sessions_lock = asyncio.Lock()


async def _get_session(session_id: str) -> _Session:
    async with _sessions_lock:
        if session_id not in _sessions:
            if len(_sessions) >= MAX_SESSIONS:
                oldest_id, oldest = next(iter(_sessions.items()))
                if not oldest.lock.locked():
                    _sessions.pop(oldest_id)
                    if oldest.client:
                        try:
                            await oldest.client.disconnect()
                        except Exception:
                            pass
            _sessions[session_id] = _Session()
        return _sessions[session_id]


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
async def chat(
    req: ChatRequest,
    x_portfolio_key: str = Header(default=""),
) -> JSONResponse:
    if not UI_KEY:
        raise HTTPException(500, "PORTFOLIO_UI_KEY is not set in .env")
    if not secrets.compare_digest(x_portfolio_key, UI_KEY):
        raise HTTPException(401, "Wrong or missing access key")
    if not req.message.strip():
        raise HTTPException(400, "Empty message")

    session = await _get_session(req.session_id)
    async with session.lock:  # one turn at a time per session
        try:
            client = await session.ensure()
            await client.query(req.message)

            parts: list[str] = []
            cost = None
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
                elif isinstance(msg, ResultMessage):
                    cost = getattr(msg, "total_cost_usd", None)
                    if getattr(msg, "is_error", False) and not parts:
                        parts.append(
                            "The agent run ended with an error"
                            + (f": {msg.result}" if getattr(msg, "result", None) else ".")
                        )
            reply = "\n\n".join(p for p in parts if p.strip()) or "(no reply)"
            return JSONResponse({"reply": reply, "cost_usd": cost})
        except Exception as exc:
            # Reset the session's client so the next message starts clean.
            if session.client is not None:
                try:
                    await session.client.disconnect()
                except Exception:
                    pass
                session.client = None
            raise HTTPException(502, f"Agent error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
