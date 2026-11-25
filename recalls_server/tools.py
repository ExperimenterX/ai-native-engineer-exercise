"""Tool implementations. Kept free of MCP imports so they can be unit tested directly."""
from __future__ import annotations

import datetime as dt
import functools
import re

from . import db, units
from .limits import clamp_limit, fit

SOURCE = "US Consumer Product Safety Commission recalls feed, loaded nightly"

# Notes are free text written by any connected agent and read back by others, so bound them: an
# oversized note would blow the response-size budget for everyone who opens that recall.
MAX_NOTE_CHARS = 1000
MAX_AUTHOR_CHARS = 100
MAX_NOTES_SHOWN = 10
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _reports_bad_input(fn):
    """Turn a ValueError from input validation (e.g. a bad retailer name) into an error result agents can read."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            return {"error": str(exc)}

    return wrapper


@_reports_bad_input
def search_recalls(query: str = "", year: int | None = None, limit: int = 20, cursor: int = 0, retailer: str = "") -> dict:
    limit = clamp_limit(limit)
    cursor = max(cursor or 0, 0)
    total, rows = db.search(query, year, limit, cursor, retailer)
    payload = fit({"total": total, "results": rows})
    # Advance by what was actually returned: fit() may have trimmed the page, and skipping ahead
    # by `limit` would silently drop the trimmed rows.
    returned = cursor + len(payload["results"])
    if returned < total:
        payload["next_cursor"] = returned
    return payload


@_reports_bad_input
def count_recalls(query: str = "", year: int | None = None, retailer: str = "") -> dict:
    return {"count": db.count(query, year, retailer)}


@_reports_bad_input
def sum_units(year: int | None = None, retailer: str = "", query: str = "", top: int = 5) -> dict:
    """Total units for the matching recalls, with the data-quality breakdown (see units.py)."""
    rows = db.units_rows(query, year, retailer)
    return units.summarize(rows, top=max(1, min(top or 5, 20)))


def get_recall(recall_number: str) -> dict:
    record = db.get(recall_number)
    if record is None:
        return {"found": False, "recall_number": recall_number}
    record["found"] = True
    notes = record.get("notes", [])
    if len(notes) > MAX_NOTES_SHOWN:  # keep the newest; they are stored oldest-first
        record["notes_total"] = len(notes)
        record["notes_truncated"] = True
        record["notes"] = notes[-MAX_NOTES_SHOWN:]
    return record


@_reports_bad_input
def recent_recalls(days: int = 30, today: dt.date | None = None, retailer: str = "") -> dict:
    days = max(1, min(days or 30, 365))
    cap = 50
    rows = db.recent(days, today=today, limit=cap + 1, retailer=retailer)
    capped = len(rows) > cap
    rows = rows[:cap]
    payload = fit({"days": days, "count": len(rows), "results": rows})
    payload["count"] = len(payload["results"])
    if capped or payload.get("truncated"):
        payload["truncated"] = True
        payload["note"] = "More recalls match than are shown; narrow `days` or use search_recalls with paging."
    return payload


def add_note(recall_number: str, note: str, author: str = "agent") -> dict:
    note = _CONTROL_CHARS.sub("", note or "").strip()
    author = _CONTROL_CHARS.sub("", author or "").strip() or "agent"
    if not note:
        return {"saved": False, "error": "note is empty"}
    if len(note) > MAX_NOTE_CHARS or len(author) > MAX_AUTHOR_CHARS:
        return {"saved": False, "error": f"note must be at most {MAX_NOTE_CHARS} characters and author at most {MAX_AUTHOR_CHARS}"}
    saved = db.add_note(recall_number, note, author)
    if saved is None:
        return {"saved": False, "error": f"no recall found with number {recall_number!r}"}
    return {"saved": True, **saved}


def server_status(now: dt.datetime | None = None) -> dict:
    return {**db.status(now), "source": SOURCE}
