"""
Google Sheets, Docs and Slides tools for the portfolio agents.

SAFETY BOUNDARY (same spirit as drive_tools.py):
- CREATE puts new files ONLY in ADK_Agents_Workspace.
- WRITE (append rows, update cells, append to a doc) is allowed ONLY on
  files whose parent is ADK_Agents_Workspace — verified per call. Files in
  Bumtum_Freeme_Business, or anywhere else, are read-only.
- READ works on files in either workspace folder.
- Nothing here deletes anything.

List/tabular parameters are passed as JSON STRINGS (the tool schema layer
only carries str/int/bool), e.g. rows_json='[["EXW","Rs 120"],["Duty","15%"]]'.
"""

from __future__ import annotations

import json

from ._google_auth import enabled, service
from .drive_tools import ADK_WORKSPACE_ID, BUMTUM_FOLDER_ID


def _sheets():
    return service("sheets", "v4")


def _docs():
    return service("docs", "v1")


def _slides():
    return service("slides", "v1")


def _drive():
    return service("drive", "v3")


def _parents(file_id: str) -> list[str]:
    meta = _drive().files().get(fileId=file_id, fields="parents").execute()
    return meta.get("parents", [])


def _writable(file_id: str) -> bool:
    return ADK_WORKSPACE_ID in _parents(file_id)


def _readable(file_id: str) -> bool:
    ps = _parents(file_id)
    return ADK_WORKSPACE_ID in ps or BUMTUM_FOLDER_ID in ps


_WRITE_REFUSED = {
    "error": ("Write refused: this file is not in ADK_Agents_Workspace. "
              "Only files in the agents' own workspace folder can be written.")
}


def _loads(name: str, raw: str):
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}")


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def sheets_create(title: str, tab_names: str) -> dict:
    """Create a new Google Sheet in ADK_Agents_Workspace.

    Args:
        title: spreadsheet name, e.g. 'Portfolio_KB'.
        tab_names: comma-separated tab names, e.g. 'Knowledge,Gaps,Tasks,Log'.
                   Pass "" for a single default tab.

    Returns:
        dict with spreadsheet id, link and tab list.
    """
    f = _drive().files().create(
        body={"name": title,
              "mimeType": "application/vnd.google-apps.spreadsheet",
              "parents": [ADK_WORKSPACE_ID]},
        fields="id, webViewLink",
    ).execute()
    sid = f["id"]
    tabs = [t.strip() for t in tab_names.split(",") if t.strip()]
    if tabs:
        reqs = [{"addSheet": {"properties": {"title": t}}} for t in tabs]
        _sheets().spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": reqs}).execute()
    return {"id": sid, "link": f.get("webViewLink", ""), "tabs": tabs or ["Sheet1"]}


def sheets_read(spreadsheet_id: str, range_a1: str) -> dict:
    """Read values from a Google Sheet (any tab, any range).

    Args:
        spreadsheet_id: the file id (from drive_list_files / drive_search_files).
        range_a1: A1 notation, e.g. 'Knowledge!A1:F200' or 'Sheet1'.

    Returns:
        dict with 'values': list of rows (each a list of cell strings).
    """
    if not _readable(spreadsheet_id):
        return {"error": "This file is outside both workspace folders."}
    res = _sheets().spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_a1).execute()
    values = res.get("values", [])
    return {"range": res.get("range", range_a1), "rows": len(values),
            "values": values[:500]}


def sheets_append_rows(spreadsheet_id: str, range_a1: str, rows_json: str) -> dict:
    """Append rows to the end of a table in a workspace Google Sheet.

    Never overwrites existing cells — rows are added below the last data row.

    Args:
        spreadsheet_id: the file id. Must be in ADK_Agents_Workspace.
        range_a1: the table to append to, e.g. 'Tasks!A:E'.
        rows_json: JSON list of rows, e.g. '[["2026-09-16","Chase Madhab","open"]]'.

    Returns:
        dict with the updated range.
    """
    if not _writable(spreadsheet_id):
        return dict(_WRITE_REFUSED)
    rows = _loads("rows_json", rows_json)
    res = _sheets().spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=range_a1,
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return {"appended": len(rows),
            "range": res.get("updates", {}).get("updatedRange", "")}


def sheets_update_range(spreadsheet_id: str, range_a1: str, values_json: str) -> dict:
    """Overwrite a specific range in a workspace Google Sheet.

    Use ONLY to fill blanks or correct agent-written cells. Prefer
    sheets_append_rows for new information. Manual entries by Riddi must not
    be silently overwritten — if in doubt, append and flag instead.

    Args:
        spreadsheet_id: the file id. Must be in ADK_Agents_Workspace.
        range_a1: exact range, e.g. 'Knowledge!D7' or 'Gaps!A2:C2'.
        values_json: JSON list of rows matching the range shape, e.g. '[["Rs 455"]]'.

    Returns:
        dict with the number of updated cells.
    """
    if not _writable(spreadsheet_id):
        return dict(_WRITE_REFUSED)
    values = _loads("values_json", values_json)
    res = _sheets().spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_a1,
        valueInputOption="USER_ENTERED", body={"values": values},
    ).execute()
    return {"updated_cells": res.get("updatedCells", 0),
            "range": res.get("updatedRange", range_a1)}


# ---------------------------------------------------------------------------
# Google Docs
# ---------------------------------------------------------------------------

def docs_create(title: str, body_text: str) -> dict:
    """Create a new Google Doc in ADK_Agents_Workspace with initial text.

    Args:
        title: document name, e.g. 'Minutes_Bumtum_Sync_2026-09-16'.
        body_text: plain text content (blank lines separate paragraphs).

    Returns:
        dict with document id and link.
    """
    f = _drive().files().create(
        body={"name": title,
              "mimeType": "application/vnd.google-apps.document",
              "parents": [ADK_WORKSPACE_ID]},
        fields="id, webViewLink",
    ).execute()
    if body_text.strip():
        _docs().documents().batchUpdate(
            documentId=f["id"],
            body={"requests": [{"insertText": {
                "location": {"index": 1}, "text": body_text}}]},
        ).execute()
    return {"id": f["id"], "link": f.get("webViewLink", "")}


def docs_append(document_id: str, text: str) -> dict:
    """Append text to the end of a workspace Google Doc.

    Args:
        document_id: the file id. Must be in ADK_Agents_Workspace.
        text: plain text to add (a leading newline is added automatically).

    Returns:
        dict confirming the append.
    """
    if not _writable(document_id):
        return dict(_WRITE_REFUSED)
    doc = _docs().documents().get(documentId=document_id,
                                  fields="body(content(endIndex))").execute()
    end = doc["body"]["content"][-1]["endIndex"]
    _docs().documents().batchUpdate(
        documentId=document_id,
        body={"requests": [{"insertText": {
            "location": {"index": max(1, end - 1)}, "text": "\n" + text}}]},
    ).execute()
    return {"appended_chars": len(text)}


# ---------------------------------------------------------------------------
# Google Slides
# ---------------------------------------------------------------------------

def slides_create(title: str, slides_json: str) -> dict:
    """Create a Google Slides presentation in ADK_Agents_Workspace.

    Args:
        title: presentation name, e.g. 'Bumtum_Dealer_Pitch_v1'.
        slides_json: JSON list of slides, each {"title": "...", "bullets": ["...", ...]},
            e.g. '[{"title":"Why Nepal","bullets":["23 years in market","Import ready"]}]'.

    Returns:
        dict with presentation id, link and slide count.
    """
    slides = _loads("slides_json", slides_json)
    f = _drive().files().create(
        body={"name": title,
              "mimeType": "application/vnd.google-apps.presentation",
              "parents": [ADK_WORKSPACE_ID]},
        fields="id, webViewLink",
    ).execute()
    pid = f["id"]

    # Remove the blank default slide, if any.
    try:
        pres = _slides().presentations().get(
            presentationId=pid, fields="slides(objectId)").execute()
        default_ids = [s["objectId"] for s in pres.get("slides", [])]
    except Exception:
        default_ids = []

    reqs = []
    for i, s in enumerate(slides):
        sid = f"agent_slide_{i}"
        reqs.append({"createSlide": {
            "objectId": sid,
            "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
            "placeholderIdMappings": [
                {"layoutPlaceholder": {"type": "TITLE"}, "objectId": f"{sid}_t"},
                {"layoutPlaceholder": {"type": "BODY"}, "objectId": f"{sid}_b"},
            ]}})
        t = str(s.get("title", "")).strip()
        if t:
            reqs.append({"insertText": {"objectId": f"{sid}_t", "text": t}})
        body = "\n".join(str(b) for b in s.get("bullets", []) if str(b).strip())
        if body:
            reqs.append({"insertText": {"objectId": f"{sid}_b", "text": body}})
    for oid in default_ids:
        reqs.append({"deleteObject": {"objectId": oid}})
    if reqs:
        _slides().presentations().batchUpdate(
            presentationId=pid, body={"requests": reqs}).execute()
    return {"id": pid, "link": f.get("webViewLink", ""), "slides": len(slides)}


# ---------------------------------------------------------------------------
# Tool bundles — gated by the same .env flags that add their OAuth scope.
# ---------------------------------------------------------------------------
SHEETS_TOOLS = (
    [sheets_create, sheets_read, sheets_append_rows, sheets_update_range]
    if enabled("SHEETS") else []
)
DOCS_TOOLS = [docs_create, docs_append] if enabled("DOCS") else []
SLIDES_TOOLS = [slides_create] if enabled("SLIDES") else []
