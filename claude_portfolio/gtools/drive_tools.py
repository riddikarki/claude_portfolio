"""
Google Drive tools for the ADK portfolio agents.

SAFETY BOUNDARY (do not change casually):
- Writes are HARD-CODED to the ADK_Agents_Workspace folder.
- The Bumtum_Freeme_Business folder (managed by a different agent system)
  is strictly read-only. Any write attempt there is refused in code.

Auth: OAuth installed-app flow. First run opens a browser window once;
token is cached in token.json next to this file's project root.
Requires credentials.json from Google Cloud Console (Drive API enabled,
OAuth client type "Desktop app").
"""

from __future__ import annotations

import io
import os

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from ._google_auth import service

# ---------------------------------------------------------------------------
# Folder boundary
# ---------------------------------------------------------------------------
ADK_WORKSPACE_ID = "1v8fFJjIdOwlOraPEuXBbOKaDsWVj_Qac"   # ADK_Agents_Workspace (write OK)
BUMTUM_FOLDER_ID = "1CWHxIMgNF2kAyPfGDwhqm-shUk4dbDPQ"   # Bumtum_Freeme_Business (READ ONLY)

FOLDER_ALIASES = {
    "adk": ADK_WORKSPACE_ID,
    "workspace": ADK_WORKSPACE_ID,
    "bumtum": BUMTUM_FOLDER_ID,
    "bumtum_freeme": BUMTUM_FOLDER_ID,
}

# Auth (Drive, Calendar, Tasks, Contacts, Gmail) lives in _google_auth.py
# so that one token.json covers every Google tool in this project.


def _drive():
    """Authenticated Drive service (shared credentials, cached)."""
    return service("drive", "v3")


# ---------------------------------------------------------------------------
# Tools (plain functions — ADK wraps them automatically)
# ---------------------------------------------------------------------------

def drive_list_files(folder: str) -> dict:
    """List files in a workspace folder.

    Args:
        folder: 'adk' for the ADK_Agents_Workspace (read/write) or
                'bumtum' for Bumtum_Freeme_Business (read-only).

    Returns:
        dict with 'files': list of {id, name, mimeType, modifiedTime}.
    """
    folder_id = FOLDER_ALIASES.get(folder.lower().strip())
    if not folder_id:
        return {"error": f"Unknown folder '{folder}'. Use 'adk' or 'bumtum'."}
    res = _drive().files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="files(id, name, mimeType, modifiedTime)",
        pageSize=100,
    ).execute()
    return {"folder": folder, "files": res.get("files", [])}


def drive_search_files(query_text: str) -> dict:
    """Search both workspace folders by file name.

    Args:
        query_text: text to match in the file title.

    Returns:
        dict with matching files and which folder each belongs to.
    """
    out = []
    for alias, fid in (("adk", ADK_WORKSPACE_ID), ("bumtum", BUMTUM_FOLDER_ID)):
        res = _drive().files().list(
            q=(f"'{fid}' in parents and trashed = false "
               f"and name contains '{query_text}'"),
            fields="files(id, name, mimeType, modifiedTime)",
            pageSize=50,
        ).execute()
        for f in res.get("files", []):
            f["folder"] = alias
            out.append(f)
    return {"query": query_text, "files": out}


_EXPORT_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def drive_read_file(file_id: str) -> dict:
    """Read a Drive file's content as text.

    Google Docs export as plain text, Sheets as CSV (first tab),
    plain/markdown/CSV files download directly.

    Args:
        file_id: the Drive file ID (get it from drive_list_files or
                 drive_search_files — never guess an ID).

    Returns:
        dict with 'name' and 'content' (possibly truncated at ~60k chars).
    """
    svc = _drive()
    meta = svc.files().get(fileId=file_id, fields="id, name, mimeType").execute()
    mime = meta["mimeType"]

    if mime in _EXPORT_MAP:
        request = svc.files().export_media(fileId=file_id, mimeType=_EXPORT_MAP[mime])
    elif mime.startswith("text/") or mime in ("application/json",):
        request = svc.files().get_media(fileId=file_id)
    else:
        return {"name": meta["name"],
                "error": f"Unsupported type for text read: {mime}"}

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    text = buf.getvalue().decode("utf-8", errors="replace")
    if len(text) > 60_000:
        text = text[:60_000] + "\n...[TRUNCATED]"
    return {"name": meta["name"], "content": text}


def drive_write_markdown(title: str, content: str) -> dict:
    """Create or update a markdown file in the ADK_Agents_Workspace ONLY.

    If a file with the same title already exists in the ADK folder, its
    content is replaced (versioned by Drive). Writing anywhere else,
    including the Bumtum_Freeme_Business folder, is not possible with
    this tool by design.

    Args:
        title: file name, e.g. 'Landed_Cost_Run_2026-09-15.md'.
        content: full markdown content of the file.

    Returns:
        dict with the file id and webViewLink.
    """
    svc = _drive()
    if not title.endswith(".md"):
        title += ".md"

    existing = svc.files().list(
        q=(f"'{ADK_WORKSPACE_ID}' in parents and trashed = false "
           f"and name = '{title}'"),
        fields="files(id)",
    ).execute().get("files", [])

    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")),
                              mimetype="text/markdown")
    if existing:
        f = svc.files().update(
            fileId=existing[0]["id"], media_body=media,
            fields="id, webViewLink",
        ).execute()
        action = "updated"
    else:
        f = svc.files().create(
            body={"name": title, "parents": [ADK_WORKSPACE_ID]},
            media_body=media,
            fields="id, webViewLink",
        ).execute()
        action = "created"
    return {"action": action, "id": f["id"], "link": f.get("webViewLink", "")}


ALL_DRIVE_TOOLS = [
    drive_list_files,
    drive_search_files,
    drive_read_file,
    drive_write_markdown,
]
