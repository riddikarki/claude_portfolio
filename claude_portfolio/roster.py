"""
Live roster: the built-in specialists from agents.py plus any agent Riddi
has approved from a CEO proposal.

How a new agent comes to exist:
1. The CEO (in chat or at the end of a board meeting) notices a recurring or
   essential task no specialist owns, and calls the `propose_agent` tool.
2. The proposal lands in state/proposals.json as "pending" and shows up in
   the console's Proposals panel.
3. Riddi approves or rejects it there. Approval adds the agent to
   state/agents_extra.json; new chat sessions pick it up at once — no code
   change, no restart.

Safety: a proposed agent can only be given the existing tool groups (the
gtools wrappers keep their hard boundaries — Drive writes only in
ADK_Agents_Workspace, Gmail drafts only, nothing deletes). Every proposed
prompt is wrapped with the shared manifest. Agents are never deleted, only
switched off.

State lives in state/ next to this file (git-ignored; the VM copy is the
record). Override with PORTFOLIO_STATE_DIR.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

from claude_agent_sdk import AgentDefinition, create_sdk_mcp_server, tool

import claude_tools as T
from agents import AGENTS, CEO_PROMPT, SPECIALIST_MODEL, _instr, _instr_voice

STATE_DIR = os.environ.get(
    "PORTFOLIO_STATE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state"),
)
_PROPOSALS = os.path.join(STATE_DIR, "proposals.json")
_EXTRA = os.path.join(STATE_DIR, "agents_extra.json")
_lock = threading.Lock()

# Console labels for the built-in specialists: (line 1, line 2, 3-letter code, full name)
BUILTIN_UI = {
    "deal_partnership": ("Deal &", "Partner", "DLP", "Deal & Partnership"),
    "landed_cost": ("Landed", "Cost", "LDC", "Landed Cost"),
    "trade_compliance": ("Trade", "Comply", "TRC", "Trade Compliance"),
    "finance_capital": ("Finance", "Capital", "FIN", "Finance & Capital"),
    "market_demand": ("Market", "Demand", "MKT", "Market & Demand"),
    "correspondence": ("Corres-", "pondence", "COR", "Correspondence"),
    "meeting_prep": ("Meeting", "Prep", "MTG", "Meeting Prep"),
    "business_planner": ("Business", "Plan", "BPL", "Business Plan"),
}

# The only tool groups a proposed agent may receive.
TOOL_GROUPS = {
    "DRIVE": T.DRIVE, "SHEETS": T.SHEETS, "DOCS": T.DOCS, "SLIDES": T.SLIDES,
    "CALENDAR": T.CALENDAR, "TASKS": T.TASKS, "CONTACTS": T.CONTACTS,
    "GMAIL": T.GMAIL, "WEB": ["WebSearch"],
}


def now(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Kathmandu time (the VM clock is UTC)."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        return datetime.now(ZoneInfo("Asia/Kathmandu")).strftime(fmt)
    except Exception:
        return time.strftime(fmt)


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,29}$")


# ---------------------------------------------------------------------------
# state files
# ---------------------------------------------------------------------------
def _load(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []


def _save(path: str, data: list[dict]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _version() -> float:
    """Changes whenever the approved roster changes (used to refresh sessions)."""
    try:
        return os.path.getmtime(_EXTRA)
    except FileNotFoundError:
        return 0.0


ROSTER_VERSION = _version


# ---------------------------------------------------------------------------
# the live roster
# ---------------------------------------------------------------------------
def _definition(rec: dict) -> AgentDefinition:
    tools: list[str] = []
    for g in rec["tool_groups"]:
        for t in TOOL_GROUPS.get(g, []):
            if t not in tools:
                tools.append(t)
    wrap = _instr_voice if rec.get("writes_in_voice") else _instr
    return AgentDefinition(
        description=rec["description"],
        prompt=wrap(rec["prompt"]),
        tools=tools,
        model=SPECIALIST_MODEL,
    )


def extra_agents(include_disabled: bool = False) -> list[dict]:
    return [a for a in _load(_EXTRA) if include_disabled or a.get("enabled", True)]


def active_agents() -> dict[str, AgentDefinition]:
    agents = dict(AGENTS)
    for rec in extra_agents():
        if rec["name"] not in agents:
            agents[rec["name"]] = _definition(rec)
    return agents


def ceo_prompt() -> str:
    extras = extra_agents()
    if not extras:
        return CEO_PROMPT
    lines = "\n".join(f"- **{a['name']}** — {a['description']}" for a in extras)
    return (CEO_PROMPT + "\n\n# APPROVED ADDITIONAL SPECIALISTS\n"
            "Riddi approved these later; route to them exactly like the "
            "specialists above:\n" + lines)


def roster_info() -> list[dict]:
    """What the console needs to draw the network."""
    out = []
    for name in AGENTS:
        l1, l2, code, full = BUILTIN_UI.get(
            name, (name[:8], "", name[:3].upper(), name))
        out.append({"name": name, "label": full, "line1": l1, "line2": l2,
                    "code": code, "builtin": True})
    def fit(word: str) -> str:
        return word if len(word) <= 9 else word[:8] + "…"

    for a in extra_agents():
        l1, _, l2 = a["title"].partition(" ")
        out.append({"name": a["name"], "label": a["title"], "line1": fit(l1),
                    "line2": fit(l2), "code": a["code"], "builtin": False})
    return out


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------
def list_proposals(status: str | None = None) -> list[dict]:
    items = _load(_PROPOSALS)
    if status:
        items = [p for p in items if p["status"] == status]
    return sorted(items, key=lambda p: p["created"], reverse=True)


def create_proposal(fields: dict, source: str) -> dict:
    """Validate and store a proposal. Returns the record or {'error': ...}."""
    name = str(fields.get("name", "")).strip().lower()
    if not _NAME_RE.match(name):
        return {"error": "name must be 3-30 chars: lowercase letters, digits, underscore; start with a letter"}
    groups = [g.strip().upper() for g in str(fields.get("tool_groups", "")).split(",") if g.strip()]
    bad = [g for g in groups if g not in TOOL_GROUPS]
    if bad or not groups:
        return {"error": f"tool_groups must be a comma list from {sorted(TOOL_GROUPS)}; bad: {bad}"}
    code = re.sub(r"[^A-Z]", "", str(fields.get("code", "")).upper())[:3]
    if len(code) != 3:
        return {"error": "code must be 3 letters, e.g. PRC"}
    required = ["title", "description", "why_now", "owns_decision", "reads",
                "recurrence", "hire_test", "prompt"]
    missing = [k for k in required if not str(fields.get(k, "")).strip()]
    if missing:
        return {"error": f"missing fields: {missing}"}
    if len(str(fields["prompt"])) < 300:
        return {"error": "prompt is too thin — write a full role block (what it owns, rules, where outputs go)"}

    with _lock:
        taken = set(AGENTS) | {a["name"] for a in extra_agents(include_disabled=True)}
        if name in taken:
            return {"error": f"'{name}' already exists in the roster"}
        props = _load(_PROPOSALS)
        if any(p["name"] == name and p["status"] == "pending" for p in props):
            return {"error": f"a proposal for '{name}' is already waiting for Riddi"}
        rec = {
            "id": uuid.uuid4().hex[:10],
            "status": "pending",
            "created": now(),
            "source": source,
            "name": name,
            "code": code,
            "title": str(fields["title"]).strip()[:40],
            "description": str(fields["description"]).strip()[:400],
            "why_now": str(fields["why_now"]).strip()[:800],
            "test": {
                "owns_decision": str(fields["owns_decision"]).strip()[:500],
                "reads": str(fields["reads"]).strip()[:500],
                "recurrence": str(fields["recurrence"]).strip()[:500],
                "hire_test": str(fields["hire_test"]).strip()[:500],
            },
            "overlaps": str(fields.get("overlaps", "")).strip()[:500],
            "tool_groups": groups,
            "writes_in_voice": bool(fields.get("writes_in_voice")),
            "prompt": str(fields["prompt"]).strip()[:8000],
        }
        props.append(rec)
        _save(_PROPOSALS, props)
    return rec


def decide(proposal_id: str, approve: bool, note: str = "") -> dict:
    with _lock:
        props = _load(_PROPOSALS)
        rec = next((p for p in props if p["id"] == proposal_id), None)
        if rec is None:
            return {"error": "proposal not found"}
        if rec["status"] != "pending":
            return {"error": f"proposal is already {rec['status']}"}
        if approve:
            extras = _load(_EXTRA)
            if rec["name"] in AGENTS or any(a["name"] == rec["name"] for a in extras):
                return {"error": f"'{rec['name']}' already exists"}
            extras.append({k: rec[k] for k in (
                "name", "code", "title", "description", "tool_groups",
                "writes_in_voice", "prompt")} | {
                "enabled": True, "approved": now(),
                "proposal_id": rec["id"]})
            _save(_EXTRA, extras)
        rec["status"] = "approved" if approve else "rejected"
        rec["decided"] = now()
        rec["note"] = note[:500]
        _save(_PROPOSALS, props)
    return rec


def set_enabled(name: str, enabled: bool) -> dict:
    """Switch an approved agent on/off. Built-ins can't be switched off here."""
    with _lock:
        extras = _load(_EXTRA)
        rec = next((a for a in extras if a["name"] == name), None)
        if rec is None:
            return {"error": "only approved (non built-in) agents can be switched"}
        rec["enabled"] = enabled
        _save(_EXTRA, extras)
    return {"name": name, "enabled": enabled}


# ---------------------------------------------------------------------------
# CEO-only MCP tools
# ---------------------------------------------------------------------------
_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "snake_case id, e.g. procurement_scheduler"},
        "title": {"type": "string", "description": "short display name, e.g. 'Procurement Scheduler'"},
        "code": {"type": "string", "description": "3 capital letters for the console, e.g. PRC"},
        "description": {"type": "string", "description": "one or two sentences: what it owns (used for routing)"},
        "why_now": {"type": "string", "description": "the recurring or essential task you saw, with the evidence"},
        "owns_decision": {"type": "string", "description": "test 1: the decision it owns that no other agent owns"},
        "reads": {"type": "string", "description": "test 2: the body of knowledge it reads that neighbours don't"},
        "recurrence": {"type": "string", "description": "test 3: how often the work recurs (must be weekly or more)"},
        "hire_test": {"type": "string", "description": "test 4: why you would hire a person to do only this"},
        "overlaps": {"type": "string", "description": "which existing agents it touches and where the line is"},
        "tool_groups": {"type": "string", "description": "comma list from DRIVE,SHEETS,DOCS,SLIDES,CALENDAR,TASKS,CONTACTS,GMAIL,WEB — the minimum needed"},
        "writes_in_voice": {"type": "boolean", "description": "true if it drafts prose that goes out in Riddi's voice"},
        "prompt": {"type": "string", "description": "the full role block, in the same style as the existing specialists: what it owns, rules, live context, where outputs go"},
    },
    "required": ["name", "title", "code", "description", "why_now", "owns_decision",
                 "reads", "recurrence", "hire_test", "overlaps", "tool_groups",
                 "writes_in_voice", "prompt"],
}


def _text(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}


@tool("propose_agent",
      "Propose a NEW specialist agent for Riddi to approve. Use only when a "
      "recurring (weekly+) or essential task has no owner in the roster and "
      "all four roster tests are honestly yes. The agent does NOT exist until "
      "Riddi approves it in the console.",
      _PROPOSE_SCHEMA)
async def _propose_agent(args: dict):
    rec = create_proposal(args, source="ceo")
    if "error" in rec:
        return _text(rec)
    return _text({"status": "pending", "id": rec["id"], "name": rec["name"],
                  "message": "Sent to Riddi for approval. Do not route to it until approved."})


@tool("roster_list",
      "List the current roster (built-in and approved agents) and proposals "
      "still waiting for Riddi. Check this before proposing an agent.",
      {"type": "object", "properties": {}, "required": []})
async def _roster_list(args: dict):
    return _text({
        "agents": [{"name": r["name"], "label": r["label"], "builtin": r["builtin"]}
                   for r in roster_info()],
        "pending_proposals": [{"name": p["name"], "title": p["title"],
                               "created": p["created"]}
                              for p in list_proposals("pending")],
    })


ROSTER_SERVER = create_sdk_mcp_server(
    name="roster", version="1.0.0", tools=[_propose_agent, _roster_list])
ROSTER_TOOLS = ["mcp__roster__propose_agent", "mcp__roster__roster_list"]
