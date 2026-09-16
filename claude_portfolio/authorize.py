"""
Run this ONCE on the LAPTOP to grant the agents every Google permission
(Drive, Gmail read+draft, Calendar, Tasks, Sheets, Docs, Slides).

It deletes the old token.json, opens a browser consent screen, and writes a
fresh token.json in this folder. Upload that token.json to the VM afterwards.

Usage (PowerShell, from the claude_portfolio folder, credentials.json present):
    ..\..\.venv\Scripts\python authorize.py
(or any Python that has google-auth-oauthlib installed)
"""

import os

for flag in ["GMAIL", "CALENDAR", "TASKS", "SHEETS", "DOCS", "SLIDES"]:
    os.environ["ADK_GOOGLE_" + flag] = "true"

if os.path.exists("token.json"):
    os.remove("token.json")
    print("Old token.json removed.")

from gtools._google_auth import get_credentials  # noqa: E402

creds = get_credentials()
print("\nNew token.json written with these permissions:")
for s in sorted(creds.scopes or []):
    print("  -", s.rsplit("/", 1)[-1])
print("\nNow upload token.json to the VM (SSH window: gear icon -> Upload file),")
print("then on the VM:")
print("  mv ~/token.json ~/claude_portfolio/claude_portfolio/token.json")
