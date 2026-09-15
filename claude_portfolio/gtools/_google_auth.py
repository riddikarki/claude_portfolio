"""
Shared Google OAuth for all portfolio agent tools.

One token, one consent screen, every API. Drive, Calendar, Tasks,
Contacts and Gmail all authenticate through here.

DELIBERATE OMISSION — there is no send scope for Gmail beyond what
`gmail.compose` implies, and no tool in this project sends mail.
Agents draft; you press send. See google_tools.py.

First run opens a browser once and caches token.json in the project
root. If you ever add a scope below, DELETE token.json and re-run —
the old token was granted for the old scope list only.
"""

from __future__ import annotations

import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Scopes — read + write, no deletion, no sending
# ---------------------------------------------------------------------------
def enabled(name: str) -> bool:
    """Feature flag from .env, e.g. ADK_GOOGLE_CALENDAR=true."""
    return os.environ.get("ADK_GOOGLE_" + name.upper(), "false").strip().lower() in (
        "1", "true", "yes", "on"
    )


# Drive is always on. The rest switch on only when you have enabled that API
# in Google Cloud Console — asking for a scope whose API is off just produces
# confusing 403s later. Turn one on: set the flag in .env, DELETE token.json,
# re-run once to re-consent.
SCOPES = ["https://www.googleapis.com/auth/drive"]

if enabled("GMAIL"):
    SCOPES += [
        "https://www.googleapis.com/auth/gmail.readonly",   # read mail
        "https://www.googleapis.com/auth/gmail.compose",    # save drafts (never sends)
    ]
if enabled("CALENDAR"):
    SCOPES += ["https://www.googleapis.com/auth/calendar.events"]
if enabled("TASKS"):
    SCOPES += ["https://www.googleapis.com/auth/tasks"]

# Contacts needs no scope: they are read from contacts_merged_clean.csv in Drive.

TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"

_creds = None
_services: dict[str, object] = {}


def get_credentials():
    """Return cached OAuth credentials, refreshing or re-consenting as needed."""
    global _creds
    if _creds and _creds.valid and _creds.has_scopes(SCOPES):
        return _creds

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            # No scopes argument: passing SCOPES would overwrite the scopes the
            # token was actually granted, making the has_scopes check below
            # always pass and the refresh fail with invalid_scope.
            creds = Credentials.from_authorized_user_file(TOKEN_FILE)
        except Exception:
            creds = None

    # A token granted for a narrower scope list must be thrown away.
    if creds and not creds.has_scopes(SCOPES):
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # Refresh token revoked, expired, or never granted these scopes
                # (e.g. a box left unticked on the consent screen). Re-consent.
                creds = None
        if not creds or not creds.valid:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE} not found. Download an OAuth Desktop "
                    "client from Google Cloud Console and place it in the "
                    "project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    _creds = creds
    return _creds


def service(api: str, version: str):
    """Build (and cache) an authenticated Google API client."""
    key = f"{api}:{version}"
    if key not in _services:
        _services[key] = build(
            api, version, credentials=get_credentials(), cache_discovery=False
        )
    return _services[key]
