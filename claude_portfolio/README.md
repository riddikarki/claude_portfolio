# Nepal Portfolio — Claude Agent SDK version

The same 8-agent system as the ADK version (CEO + 7 specialists), ported to
the Claude Agent SDK so it can run on Riddi's Claude Max plan — no per-token
API bill. Same Google Drive knowledge base, same safety boundaries:

- Writes go ONLY to `ADK_Agents_Workspace`; `Bumtum_Freeme_Business` is
  read-only in code (`gtools/drive_tools.py`).
- Gmail is read + draft only. No tool sends mail, no tool deletes anything.
- Contacts come from `contacts_merged_clean.csv` in Drive.

## Layout

| File | What it is |
|---|---|
| `house.py` | Shared manifest + Riddi's writing voice (mirror of the ADK copies) |
| `agents.py` | The roster: 7 specialist `AgentDefinition`s + the CEO prompt |
| `claude_tools.py` | Wraps `gtools/` functions as SDK MCP tools |
| `gtools/` | Google Drive/Calendar/Tasks/Contacts/Gmail tools — copied from `portfolio/tools/` |
| `server.py` | FastAPI server: `/chat` API + serves the console |
| `web/index.html` | The chat console |
| `deploy/` | VM setup script + systemd service |
| `SETUP_GUIDE.md` | Step-by-step: from Google Cloud console to running system |

## Run it on the laptop first (recommended)

```powershell
cd claude_portfolio
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then edit .env: token + UI key
copy ..\credentials.json .  # reuse the ADK project's Google auth
copy ..\token.json .
python server.py
```

Open http://localhost:8080. If it works here, the VM will behave the same.

## Deploy

See `SETUP_GUIDE.md` — the whole path from creating the free e2-micro VM to
the running service, step by step.

## Keeping the two versions in sync

`house.py` mirrors `portfolio/manifest.py` + `portfolio/writing_style.py`,
and `gtools/` mirrors `portfolio/tools/`. If you change house rules or tools
in one system, make the same change in the other (or ask Claude to).
