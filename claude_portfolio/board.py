"""
Boardroom: specialists discuss one company issue and the CEO writes a
decision memo for Riddi.

Flow (run by the server, one meeting at a time):
1. Chair — the CEO picks 2-4 relevant specialists (unless Riddi picked them)
   and gives each a question.
2. Round 1 — each specialist states a position: view, evidence, risk,
   next action.
3. Round 2 — each responds to the others by name: agree, disagree, changed.
4. Memo — the CEO writes the decision memo; the server saves it to
   ADK_Agents_Workspace as 90_OUT_Board_<date>_<slug>.md. If the discussion
   exposed an unowned recurring task, the CEO may file an agent proposal.

Nothing is acted on. In the meeting, specialists get READ-ONLY tools (no
drafts, tasks, files or events); Riddi decides what happens next.

Each turn is a separate one-shot SDK query, run sequentially, so only one
Claude process is alive at a time (the VM is an e2-micro).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

import roster
from claude_tools import GTOOLS_SERVER
from gtools.drive_tools import drive_write_markdown

MODEL = os.environ.get("PORTFOLIO_MODEL", "sonnet")
MAX_PARTICIPANTS = 4
BOARD_LOCK = asyncio.Lock()
_BOARDS_DIR = os.path.join(roster.STATE_DIR, "boards")

_READ_ONLY = {
    "mcp__gtools__drive_list_files", "mcp__gtools__drive_search_files",
    "mcp__gtools__drive_read_file", "mcp__gtools__sheets_read",
    "mcp__gtools__calendar_list_events", "mcp__gtools__tasks_list",
    "mcp__gtools__contacts_search", "mcp__gtools__gmail_search",
    "mcp__gtools__gmail_read_thread", "WebSearch",
}
_DENY = ["Bash", "Write", "Edit", "Read", "Glob", "Grep", "Task", "Agent",
         "NotebookEdit", "WebFetch"]
_DRIVE_READ = [t for t in _READ_ONLY if "drive_" in t]

_MEETING_RULES = """

# BOARD MEETING MODE
You are in a board meeting with other specialists, chaired by the CEO. This is
discussion only: do not create drafts, tasks, events or files. Use tools only
to look facts up. Speak as yourself, in plain prose, no preamble, no headings.
Name the file behind every number; unknowns are [DATA_GAP] with an owner.
Nepali money is Rs, never ₹.
"""


def _options(system_prompt: str, tools: list[str], max_turns: int) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"gtools": GTOOLS_SERVER, "roster": roster.ROSTER_SERVER},
        allowed_tools=tools,
        disallowed_tools=_DENY,
        permission_mode="bypassPermissions",
        model=MODEL,
        max_turns=max_turns,
    )


async def _ask(prompt: str, options: ClaudeAgentOptions, speaker: str,
               events: asyncio.Queue) -> str:
    """Run one turn; forward tool use to the UI; return the final text."""
    last_text, result = "", ""
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            texts = []
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    name = block.name.split("__")[-1]
                    await events.put({"type": "tool", "name": name, "agent": speaker,
                                      "sub": speaker != "ceo"})
                elif isinstance(block, TextBlock) and block.text.strip():
                    texts.append(block.text.strip())
            if texts:
                last_text = "\n\n".join(texts)
        elif isinstance(msg, ResultMessage):
            result = (getattr(msg, "result", None) or "").strip()
    return result or last_text


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return s[:40] or "Issue"


def _transcript(entries: list[dict]) -> str:
    return "\n\n".join(f"[Round {e['round']}] {e['label']}:\n{e['text']}" for e in entries)


def list_boards(limit: int = 20) -> list[dict]:
    try:
        files = sorted(os.listdir(_BOARDS_DIR), reverse=True)[:limit]
    except FileNotFoundError:
        return []
    out = []
    for f in files:
        try:
            with open(os.path.join(_BOARDS_DIR, f), encoding="utf-8") as fh:
                b = json.load(fh)
            out.append({k: b.get(k) for k in ("id", "issue", "started", "participants", "link")})
        except Exception:
            continue
    return out


def get_board(board_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,40}", board_id):
        return None
    for f in os.listdir(_BOARDS_DIR) if os.path.isdir(_BOARDS_DIR) else []:
        if f.endswith(f"_{board_id}.json"):
            with open(os.path.join(_BOARDS_DIR, f), encoding="utf-8") as fh:
                return json.load(fh)
    return None


def _save_board(rec: dict) -> None:
    os.makedirs(_BOARDS_DIR, exist_ok=True)
    path = os.path.join(_BOARDS_DIR, f"{rec['file_stamp']}_{rec['id']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)


class _Live:
    """The meeting in session. It runs in the background, so closing the
    browser tab doesn't end it; any page can re-attach and replay events."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = True
        self.changed = asyncio.Event()
        self.task: asyncio.Task | None = None


LIVE = _Live()


def in_session() -> bool:
    return not LIVE.done


class _Sink:
    """Queue-like adapter: _meeting puts events, watchers read LIVE.events."""

    async def put(self, ev: dict) -> None:
        LIVE.events.append(ev)
        LIVE.changed.set()


def start_board(issue: str, requested: list[str], rounds: int = 2) -> bool:
    """Start a meeting in the background. False if one is already running."""
    if in_session():
        return False
    LIVE.events = [{"type": "start", "issue": issue}]
    LIVE.done = False
    LIVE.changed = asyncio.Event()

    async def runner() -> None:
        sink = _Sink()
        async with BOARD_LOCK:
            try:
                await _meeting(issue, requested, max(1, min(rounds, 2)), sink)
            except Exception as exc:
                await sink.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                LIVE.done = True
                LIVE.changed.set()

    LIVE.task = asyncio.create_task(runner())
    return True


async def watch(start: int = 0) -> AsyncIterator[dict]:
    """Replay the live meeting's events from `start`, then follow until it ends."""
    i = start
    while True:
        while i < len(LIVE.events):
            yield LIVE.events[i]
            i += 1
        if LIVE.done:
            return
        LIVE.changed.clear()
        if i < len(LIVE.events) or LIVE.done:
            continue
        try:
            await asyncio.wait_for(LIVE.changed.wait(), timeout=15)
        except asyncio.TimeoutError:
            yield {"type": "ping"}


async def _meeting(issue: str, requested: list[str], rounds: int, ev: asyncio.Queue) -> None:
    t0 = time.monotonic()
    agents = roster.active_agents()
    labels = {r["name"]: r["label"] for r in roster.roster_info()}
    labels["ceo"] = "CEO"
    menu = "\n".join(f"- {n}: {agents[n].description}" for n in agents)
    rec = {"id": uuid.uuid4().hex[:8], "issue": issue,
           "started": roster.now(),
           "file_stamp": roster.now("%Y%m%d-%H%M%S"),
           "participants": [], "entries": [], "memo": "", "link": ""}

    # 1. Chair --------------------------------------------------------------
    await ev.put({"type": "phase", "text": "Chair is setting the agenda"})
    await ev.put({"type": "turn", "name": "ceo", "round": 0})
    chosen = [n for n in dict.fromkeys(requested) if n in agents][:MAX_PARTICIPANTS]
    chair_prompt = (
        f"You are chairing a board meeting on this issue:\n\n{issue}\n\n"
        f"Specialists available:\n{menu}\n\n"
        + (f"Riddi has chosen the participants: {', '.join(chosen)}.\n"
           if len(chosen) >= 2 else
           "Pick the 2 to 4 specialists whose decisions this issue actually touches.\n")
        + "You may read the 20_LIVE files first. Then reply with ONLY this JSON:\n"
        '{"participants": ["name", ...], "agenda": "one sentence framing", '
        '"questions": {"name": "the specific question for that specialist"}}'
    )
    chair_text = await _ask(chair_prompt,
                            _options(roster.ceo_prompt() + _MEETING_RULES, _DRIVE_READ, 8),
                            "ceo", ev)
    plan = _json_from(chair_text)
    if len(chosen) < 2:
        chosen = [n for n in plan.get("participants", []) if n in agents][:MAX_PARTICIPANTS]
    if len(chosen) < 2:
        chosen = [n for n in ("finance_capital", "market_demand", "business_planner")
                  if n in agents]
    agenda = str(plan.get("agenda", "")).strip() or issue
    questions = plan.get("questions", {}) if isinstance(plan.get("questions"), dict) else {}
    rec["participants"] = chosen
    rec["agenda"] = agenda
    await ev.put({"type": "board", "id": rec["id"], "agenda": agenda,
                  "participants": [{"name": n, "label": labels.get(n, n)} for n in chosen]})

    # 2-3. Rounds -----------------------------------------------------------
    for r in range(1, rounds + 1):
        await ev.put({"type": "phase",
                      "text": "Round 1 — positions" if r == 1 else "Round 2 — responses"})
        for name in chosen:
            await ev.put({"type": "turn", "name": name, "round": r})
            a = agents[name]
            tools = [t for t in (a.tools or []) if t in _READ_ONLY]
            if r == 1:
                prompt = (
                    f"BOARD ISSUE: {issue}\nCHAIR'S FRAMING: {agenda}\n"
                    f"QUESTION FOR YOU: {questions.get(name, 'What is your view from your function?')}\n\n"
                    + (f"Colleagues who spoke before you:\n\n{_transcript(rec['entries'])}\n\n"
                       if rec["entries"] else "")
                    + "Give your position in at most 180 words: your view, the evidence "
                      "(name the file), the biggest risk, and the next action you would "
                      "take and which person owns it."
                )
            else:
                prompt = (
                    f"BOARD ISSUE: {issue}\n\nThe discussion so far:\n\n"
                    f"{_transcript(rec['entries'])}\n\n"
                    "Respond in at most 150 words. Address colleagues by name: where you "
                    "agree, where you disagree and why, and whether your recommendation "
                    "changed. Don't repeat yourself."
                )
            try:
                text = await _ask(prompt, _options(a.prompt + _MEETING_RULES, tools, 8), name, ev)
            except Exception as exc:  # one failed speaker shouldn't end the meeting
                text = f"(could not answer: {type(exc).__name__})"
            entry = {"name": name, "label": labels.get(name, name), "round": r,
                     "text": text or "(no answer)"}
            rec["entries"].append(entry)
            await ev.put({"type": "say", **entry})

    # 4. Memo ---------------------------------------------------------------
    await ev.put({"type": "phase", "text": "CEO is writing the decision memo"})
    await ev.put({"type": "turn", "name": "ceo", "round": 3})
    today = roster.now("%Y-%m-%d")
    memo_prompt = f"""The board meeting has ended. Issue: {issue}

Transcript:

{_transcript(rec['entries'])}

Write the decision memo for Riddi in markdown, exactly these sections:
# Board memo — <short issue title>
**Date:** {today} · **Participants:** <names>
## Recommendation
(2-4 sentences; what to do and why)
## Options considered
## Where the specialists disagreed
(say who held which view; don't smooth it over)
## Next actions
| # | Action | Owner (person) | Specialist agent | Due |
## Data gaps
([DATA_GAP] items with owners)
## Decision needed from Riddi
(the exact yes/no or choice she must make)

Nothing is approved until Riddi says so — write actions as proposals.
Do not invent numbers that no specialist gave.

If the meeting exposed a recurring (weekly+) or essential task that no agent
owns, check roster_list and, if all four roster tests hold, call
propose_agent, then add a final section "## Roster proposal" with one line.
Reply with the memo only."""
    memo = await _ask(memo_prompt,
                      _options(roster.ceo_prompt() + _MEETING_RULES,
                               roster.ROSTER_TOOLS + _DRIVE_READ, 10),
                      "ceo", ev)
    rec["memo"] = memo
    link = ""
    try:
        res = await asyncio.to_thread(
            drive_write_markdown,
            f"90_OUT_Board_{today}_{roster.now('%H%M')}_{_slug(issue)}.md", memo)
        link = (res or {}).get("link") or (res or {}).get("webViewLink") or ""
    except Exception as exc:
        await ev.put({"type": "phase", "text": f"Memo not saved to Drive ({type(exc).__name__})"})
    rec["link"] = link
    rec["secs"] = round(time.monotonic() - t0, 1)
    _save_board(rec)
    await ev.put({"type": "memo", "text": memo, "link": link})
    await ev.put({"type": "done", "secs": rec["secs"]})
