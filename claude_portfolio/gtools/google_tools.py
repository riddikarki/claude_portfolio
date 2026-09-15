"""
Calendar, Tasks, Contacts and Gmail tools for the portfolio agents.

SAFETY BOUNDARY (do not change casually):
- No tool sends email. Gmail is read + draft only; drafts land in your
  Gmail Drafts folder for you to review and send by hand.
- No tool deletes anything — no event delete, no task delete, no trash.
- Contacts are read from a CSV in Drive, never from the live address book.

All parameters are required. Where a value is optional in practice,
pass an empty string "" (or 0) — Gemini function declarations do not
carry default values reliably, so the defaults live in the code.

Times are ISO-8601. Naive times are interpreted as Nepal time
(Asia/Kathmandu, UTC+05:45).
"""

from __future__ import annotations

import base64
import datetime as _dt
from email.message import EmailMessage

from ._google_auth import service

TIMEZONE = "Asia/Kathmandu"
_NPT = _dt.timezone(_dt.timedelta(hours=5, minutes=45))


def _iso(value: str) -> str:
    """Normalise a user/LLM supplied time string to RFC-3339 with offset."""
    v = (value or "").strip().replace("Z", "+00:00")
    if not v:
        raise ValueError("empty datetime")
    if len(v) == 10:                       # date only -> start of day NPT
        v += "T00:00:00"
    dt = _dt.datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_NPT)
    return dt.isoformat()


def _now() -> _dt.datetime:
    return _dt.datetime.now(_NPT)


# ===========================================================================
# Calendar
# ===========================================================================

def calendar_list_events(days_ahead: int, query: str) -> dict:
    """List upcoming calendar events.

    Args:
        days_ahead: how many days forward to look (e.g. 7). Use 1 for today/tomorrow.
        query: free-text filter on title/description, or "" for everything.

    Returns:
        dict with 'events': list of {id, summary, start, end, location, attendees}.
    """
    start = _now()
    end = start + _dt.timedelta(days=max(int(days_ahead), 1))
    params = {
        "calendarId": "primary",
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": 50,
    }
    if query.strip():
        params["q"] = query.strip()
    res = service("calendar", "v3").events().list(**params).execute()
    events = []
    for e in res.get("items", []):
        events.append({
            "id": e.get("id"),
            "summary": e.get("summary", "(no title)"),
            "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
            "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
            "location": e.get("location", ""),
            "attendees": [a.get("email") for a in e.get("attendees", [])],
        })
    return {"window_days": days_ahead, "count": len(events), "events": events}


def calendar_create_event(
    summary: str, start_time: str, end_time: str,
    description: str, location: str, attendees: str,
) -> dict:
    """Create a calendar event.

    Args:
        summary: event title.
        start_time: ISO-8601 start, e.g. '2026-09-18T10:30:00'. Nepal time if no offset.
        end_time: ISO-8601 end.
        description: body text, or "".
        location: place, or "".
        attendees: comma-separated email addresses, or "".

    Returns:
        dict with the created event's id, summary and htmlLink.
    """
    body = {
        "summary": summary,
        "start": {"dateTime": _iso(start_time), "timeZone": TIMEZONE},
        "end": {"dateTime": _iso(end_time), "timeZone": TIMEZONE},
    }
    if description.strip():
        body["description"] = description
    if location.strip():
        body["location"] = location
    emails = [a.strip() for a in attendees.split(",") if a.strip()]
    if emails:
        body["attendees"] = [{"email": e} for e in emails]

    e = service("calendar", "v3").events().insert(
        calendarId="primary", body=body, sendUpdates="none"
    ).execute()
    return {"id": e.get("id"), "summary": e.get("summary"), "link": e.get("htmlLink")}


def calendar_update_event(
    event_id: str, summary: str, start_time: str, end_time: str,
    description: str, location: str,
) -> dict:
    """Update or reschedule an existing event. Pass "" for any field to leave unchanged.

    Args:
        event_id: id from calendar_list_events.
        summary: new title, or "".
        start_time: new ISO-8601 start, or "".
        end_time: new ISO-8601 end, or "".
        description: new body, or "".
        location: new place, or "".

    Returns:
        dict with the updated event's id, summary, start and end.
    """
    patch: dict = {}
    if summary.strip():
        patch["summary"] = summary
    if description.strip():
        patch["description"] = description
    if location.strip():
        patch["location"] = location
    if start_time.strip():
        patch["start"] = {"dateTime": _iso(start_time), "timeZone": TIMEZONE}
    if end_time.strip():
        patch["end"] = {"dateTime": _iso(end_time), "timeZone": TIMEZONE}
    if not patch:
        return {"error": "nothing to update"}

    e = service("calendar", "v3").events().patch(
        calendarId="primary", eventId=event_id, body=patch, sendUpdates="none"
    ).execute()
    return {
        "id": e.get("id"),
        "summary": e.get("summary"),
        "start": (e.get("start") or {}).get("dateTime"),
        "end": (e.get("end") or {}).get("dateTime"),
    }


# ===========================================================================
# Tasks
# ===========================================================================

def _tasklist_id(name: str) -> str:
    lists = service("tasks", "v1").tasklists().list(maxResults=100).execute().get("items", [])
    if not lists:
        raise RuntimeError("No task lists found on this account.")
    if name.strip():
        for tl in lists:
            if tl.get("title", "").lower() == name.strip().lower():
                return tl["id"]
    return lists[0]["id"]


def tasks_list(list_name: str, include_completed: bool) -> dict:
    """List tasks from a Google Tasks list.

    Args:
        list_name: the list title, or "" for the default list.
        include_completed: True to include finished tasks.

    Returns:
        dict with 'tasks': list of {id, title, notes, due, status}.
    """
    tid = _tasklist_id(list_name)
    res = service("tasks", "v1").tasks().list(
        tasklist=tid, showCompleted=bool(include_completed),
        showHidden=bool(include_completed), maxResults=100,
    ).execute()
    tasks = [{
        "id": t.get("id"),
        "title": t.get("title", ""),
        "notes": t.get("notes", ""),
        "due": t.get("due", ""),
        "status": t.get("status", ""),
    } for t in res.get("items", [])]
    return {"list": list_name or "(default)", "count": len(tasks), "tasks": tasks}


def tasks_create(title: str, notes: str, due_date: str, list_name: str) -> dict:
    """Create a task.

    Args:
        title: the task title.
        notes: detail text, or "".
        due_date: 'YYYY-MM-DD', or "" for no due date.
        list_name: target list title, or "" for the default list.

    Returns:
        dict with the created task's id and title.
    """
    tid = _tasklist_id(list_name)
    body: dict = {"title": title}
    if notes.strip():
        body["notes"] = notes
    if due_date.strip():
        d = _dt.datetime.fromisoformat(due_date.strip()[:10])
        body["due"] = d.replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    t = service("tasks", "v1").tasks().insert(tasklist=tid, body=body).execute()
    return {"id": t.get("id"), "title": t.get("title"), "due": t.get("due", "")}


def tasks_complete(task_id: str, list_name: str) -> dict:
    """Mark a task completed.

    Args:
        task_id: id from tasks_list.
        list_name: the list it lives in, or "" for the default list.

    Returns:
        dict with the task's id and new status.
    """
    tid = _tasklist_id(list_name)
    t = service("tasks", "v1").tasks().patch(
        tasklist=tid, task=task_id, body={"status": "completed"}
    ).execute()
    return {"id": t.get("id"), "status": t.get("status")}


# ===========================================================================
# Contacts — read from contacts_merged_clean.csv in Drive (no People API)
# ===========================================================================

CONTACTS_FILE_ID = "1ihHQHEizqbiIA9Tx8cXWQphXBNkAhwqz"  # contacts_merged_clean.csv

_contacts_cache: list[dict] | None = None


def _load_contacts() -> list[dict]:
    """Download and parse the contacts CSV once per process."""
    global _contacts_cache
    if _contacts_cache is not None:
        return _contacts_cache

    import csv
    import io as _io

    raw = service("drive", "v3").files().get_media(
        fileId=CONTACTS_FILE_ID
    ).execute()
    text = raw.decode("utf-8", "replace")

    rows = []
    for r in csv.DictReader(_io.StringIO(text)):
        name = " ".join(x for x in [
            (r.get("First Name") or "").strip(),
            (r.get("Middle Name") or "").strip(),
            (r.get("Last Name") or "").strip(),
        ] if x).strip()
        emails = [e.strip() for e in [
            r.get("E-mail 1 - Value"), r.get("E-mail 2 - Value")
        ] if e and e.strip()]
        phones = [t.strip() for t in [
            r.get("Phone 1 - Value"), r.get("Phone 2 - Value"), r.get("Phone 3 - Value")
        ] if t and t.strip()]
        entry = {
            "name": name,
            "organisation": (r.get("Organization Name") or "").strip(),
            "emails": sorted(set(emails)),
            "phones": sorted(set(phones)),
            "notes": (r.get("Notes") or "").strip(),
            "labels": (r.get("Labels") or "").strip(),
        }
        if entry["name"] or entry["emails"] or entry["phones"]:
            entry["_blob"] = " ".join([
                entry["name"], entry["organisation"], entry["notes"],
                entry["labels"], " ".join(entry["emails"]), " ".join(entry["phones"]),
            ]).lower()
            rows.append(entry)

    _contacts_cache = rows
    return rows


def contacts_search(query: str) -> dict:
    """Look up a contact in Riddi's address book (contacts_merged_clean.csv in Drive).

    Names in this file are messy — the company often sits in the last-name
    field ('Adhunik Balaju', 'Ji NMB Bank'). Search by any fragment: a person's
    name, a company, a town, a bank. Some rows have a phone but no name.

    Args:
        query: text to match, e.g. 'NMB', 'boodmo', 'Pokhara', 'federal mogul'.

    Returns:
        dict with 'contacts': list of {name, organisation, emails, phones, notes, labels}.
    """
    q = query.strip().lower()
    if not q:
        return {"error": "empty query"}
    rows = _load_contacts()
    hits = [r for r in rows if q in r["_blob"]]
    out = [{k: v for k, v in r.items() if k != "_blob"} for r in hits[:40]]
    return {
        "query": query,
        "matched": len(hits),
        "showing": len(out),
        "contacts": out,
    }


# ===========================================================================
# Gmail — read and draft only. NOTHING HERE SENDS MAIL.
# ===========================================================================

def _thread_snippet(t: dict) -> dict:
    msgs = t.get("messages", [])
    first = msgs[0] if msgs else {}
    hdrs = {h["name"].lower(): h["value"] for h in (first.get("payload", {}).get("headers", []))}
    return {
        "thread_id": t.get("id"),
        "subject": hdrs.get("subject", "(no subject)"),
        "from": hdrs.get("from", ""),
        "date": hdrs.get("date", ""),
        "messages": len(msgs),
        "snippet": first.get("snippet", ""),
    }


def gmail_search(query: str, max_results: int) -> dict:
    """Search Gmail threads using Gmail search syntax.

    Args:
        query: e.g. 'from:boodmo.com newer_than:30d' or 'subject:distributorship'.
        max_results: how many threads to return (1-25).

    Returns:
        dict with 'threads': list of {thread_id, subject, from, date, messages, snippet}.
    """
    n = max(1, min(int(max_results or 10), 25))
    svc = service("gmail", "v1")
    res = svc.users().threads().list(userId="me", q=query, maxResults=n).execute()
    threads = []
    for ref in res.get("threads", []):
        t = svc.users().threads().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        threads.append(_thread_snippet(t))
    return {"query": query, "count": len(threads), "threads": threads}


def _plain_text(payload: dict) -> str:
    if payload.get("mimeType", "").startswith("text/plain"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        txt = _plain_text(part)
        if txt:
            return txt
    return ""


def gmail_read_thread(thread_id: str) -> dict:
    """Read the full text of one Gmail thread.

    Args:
        thread_id: id from gmail_search.

    Returns:
        dict with 'subject' and 'messages': list of {from, to, date, body}.
    """
    t = service("gmail", "v1").users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    subject, msgs = "", []
    for m in t.get("messages", []):
        payload = m.get("payload", {})
        hdrs = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        subject = subject or hdrs.get("subject", "")
        body = _plain_text(payload) or m.get("snippet", "")
        if len(body) > 4000:
            body = body[:4000] + "\n… (truncated)"
        msgs.append({
            "from": hdrs.get("from", ""), "to": hdrs.get("to", ""),
            "date": hdrs.get("date", ""), "body": body.strip(),
        })
    return {"thread_id": thread_id, "subject": subject, "messages": msgs}


def gmail_create_draft(to: str, subject: str, body: str, reply_to_thread_id: str) -> dict:
    """Save a draft email in Gmail. THIS DOES NOT SEND — Riddi reviews and sends it.

    Write the body in Riddi's own voice (see the writing-style block in your
    instructions): formal, un-contracted, no exclamation marks, the ask named
    early, labelled sections and dash bullets if it runs long.

    Args:
        to: comma-separated recipient addresses.
        subject: the subject line.
        body: the full plain-text body, greeting and sign-off included.
        reply_to_thread_id: thread id to reply within, or "" for a new thread.

    Returns:
        dict with the draft's id and a note confirming it was not sent.
    """
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    payload: dict = {"message": {"raw": raw}}
    if reply_to_thread_id.strip():
        payload["message"]["threadId"] = reply_to_thread_id.strip()

    d = service("gmail", "v1").users().drafts().create(userId="me", body=payload).execute()
    return {
        "draft_id": d.get("id"),
        "to": to,
        "subject": subject,
        "status": "SAVED AS DRAFT — not sent. Review it in Gmail > Drafts.",
    }


# ---------------------------------------------------------------------------
# Tool bundles — attach only what an agent actually needs, since every tool
# schema costs input tokens on every single model call that agent makes.
# ---------------------------------------------------------------------------
from ._google_auth import enabled  # noqa: E402  (flag check, after definitions)

# Toolsets switch on with the same .env flags that add their OAuth scope, so a
# disabled API never reaches an agent as a callable tool.
CALENDAR_TOOLS = (
    [calendar_list_events, calendar_create_event, calendar_update_event]
    if enabled("CALENDAR") else []
)
TASK_TOOLS = (
    [tasks_list, tasks_create, tasks_complete] if enabled("TASKS") else []
)
GMAIL_TOOLS = (
    [gmail_search, gmail_read_thread, gmail_create_draft] if enabled("GMAIL") else []
)
# Contacts ride on the Drive scope, so they are always available.
CONTACT_TOOLS = [contacts_search]

ALL_GOOGLE_TOOLS = CALENDAR_TOOLS + TASK_TOOLS + CONTACT_TOOLS + GMAIL_TOOLS
