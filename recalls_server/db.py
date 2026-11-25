"""Read and write access to the recalls snapshot (SQLite)."""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import pathlib
import re
import sqlite3
from typing import Any, Iterator

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "recalls.db")

SUMMARY_COLUMNS = (
    "recall_number, title, recall_date, product_name, hazard, units, sold_at, url"
)


def db_path() -> str:
    return os.environ.get("RECALLS_DB", DEFAULT_DB)


def _regexp(pattern: str, value: str | None) -> bool:
    return bool(value) and re.search(pattern, value, re.IGNORECASE) is not None


@contextlib.contextmanager
def connect(readonly: bool = True) -> Iterator[sqlite3.Connection]:
    """Open the snapshot. Read-only unless the caller needs to write, so a bug in a read tool can't change data."""
    if readonly:
        con = sqlite3.connect(pathlib.Path(db_path()).resolve().as_uri() + "?mode=ro", uri=True)
    else:
        con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.create_function("REGEXP", 2, _regexp, deterministic=True)
    try:
        yield con
    finally:
        con.close()


def normalize_recall_number(value: str) -> str:
    """Accept '26-123', '26123' or 'Recall 26-123' and return the stored form."""
    return re.sub(r"\D", "", value or "")


def retailer_core_pattern(retailer: str) -> str:
    """Regex for a retailer name that tolerates how CPSC writes it.

    'Walmart' matches 'Walmart stores', 'Walmart.com' and 'Wal-Mart'; 'Home Depot' matches
    'HomeDepot.com'; "Lowe's" matches 'Lowes'. A leading 'www.' / 'https://' and a trailing
    '.com' on the input are ignored. Letters can't continue on either side, so 'Target' does not
    match 'Targeted'. Raises ValueError for input too short or too long to be a retailer name.
    """
    name = (retailer or "").strip().lower()
    name = re.sub(r"^(https?://)?(www\.)?", "", name)
    name = re.sub(r"\.(com|net|org|co)\b/?$", "", name)
    tokens = [ch for ch in name if ch.isalnum()]
    if len(tokens) < 2 or len(name) > 60:
        raise ValueError("retailer must be a store or site name of 2-60 characters, for example 'Amazon' or 'Home Depot'")
    return "(?<![a-z0-9])" + r"[\W_]?".join(re.escape(ch) for ch in tokens) + "(?![a-z])"


def _like(text: str) -> str:
    """A LIKE pattern matching `text` literally anywhere ('%' and '_' in the input are not wildcards)."""
    return "%" + text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def _where(query: str, year: int | None, retailer: str = "") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if (retailer or "").strip():
        core = retailer_core_pattern(retailer)
        # `sold_at` is the field for this. A few recalls have it empty or wrong while the title
        # says where it was sold ("...; Sold on Amazon.com"), so also accept that title phrasing.
        clauses.append("(sold_at REGEXP ? OR title REGEXP ?)")
        params += [core, r"sold[^;]{0,40}?" + core]
    query = (query or "").strip()
    if query:
        like = _like(query)
        clauses.append(
            "(title LIKE ? ESCAPE '!' OR product_name LIKE ? ESCAPE '!' OR hazard LIKE ? ESCAPE '!' OR sold_at LIKE ? ESCAPE '!')"
        )
        params += [like, like, like, like]
    if year is not None:
        # Calendar year by recall_date. The recall-number prefix is CPSC's fiscal year (starts in
        # October), so it is NOT the calendar year. ISO dates compare correctly as strings.
        clauses.append("recall_date >= ? AND recall_date < ?")
        params += [f"{int(year):04d}-01-01", f"{int(year) + 1:04d}-01-01"]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def search(query: str = "", year: int | None = None, limit: int = 20, offset: int = 0, retailer: str = "") -> tuple[int, list[dict]]:
    where, params = _where(query, year, retailer)
    with connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM recalls{where}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM recalls{where} ORDER BY recall_date DESC, recall_number DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return total, [dict(r) for r in rows]


def count(query: str = "", year: int | None = None, retailer: str = "") -> int:
    where, params = _where(query, year, retailer)
    with connect() as con:
        return con.execute(f"SELECT COUNT(*) FROM recalls{where}", params).fetchone()[0]


def units_rows(query: str = "", year: int | None = None, retailer: str = "") -> list[dict]:
    """Every matching recall with its units, biggest first. No paging or size cap: totals need all rows."""
    where, params = _where(query, year, retailer)
    with connect() as con:
        rows = con.execute(
            f"SELECT recall_number, title, recall_date, units, units_text FROM recalls{where} "
            "ORDER BY units DESC, recall_number DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get(recall_number: str) -> dict | None:
    number = normalize_recall_number(recall_number)
    with connect() as con:
        row = con.execute("SELECT * FROM recalls WHERE recall_number = ?", [number]).fetchone()
        if row is None:
            return None
        record = dict(row)
        notes = con.execute(
            "SELECT author, body, created_at FROM notes WHERE recall_number = ? ORDER BY created_at",
            [number],
        ).fetchall()
    record["notes"] = [dict(n) for n in notes]
    return record


def recent(days: int, today: dt.date | None = None, limit: int = 50, retailer: str = "") -> list[dict]:
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    where, params = _where("", None, retailer)
    where = (where + " AND" if where else " WHERE") + " recall_date >= ?"
    with connect() as con:
        rows = con.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM recalls{where} ORDER BY recall_date DESC, recall_number DESC LIMIT ?",
            params + [since, limit],
        ).fetchall()
    return [dict(r) for r in rows]


def add_note(recall_number: str, body: str, author: str) -> dict | None:
    """Save a note under the stored (digits-only) number. Returns None if the recall doesn't exist."""
    number = normalize_recall_number(recall_number)
    created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect(readonly=False) as con:
        if con.execute("SELECT 1 FROM recalls WHERE recall_number = ?", [number]).fetchone() is None:
            return None
        cur = con.execute(
            "INSERT INTO notes (recall_number, author, body, created_at) VALUES (?, ?, ?, ?)",
            [number, author, body.strip(), created],
        )
        con.commit()
        note_id = cur.lastrowid
    return {"id": note_id, "recall_number": number, "author": author, "created_at": created}


# CPSC publishes weekly (typical gap 7-8 days; the longest in the snapshot is 21). Loads are nightly.
LOAD_OVERDUE_HOURS = 36
NO_NEW_RECALLS_DAYS = 15


def status(now: dt.datetime | None = None) -> dict:
    """Row count and last load, plus whether the data looks current.

    A load marked 'ok' only means the job ran. `warnings` says when the data itself looks behind,
    which the load log alone can't show.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    with connect() as con:
        rows = con.execute("SELECT COUNT(*) FROM recalls").fetchone()[0]
        newest = con.execute("SELECT MAX(recall_date) FROM recalls").fetchone()[0]
        loads = con.execute("SELECT run_at, status, rows_upserted FROM load_log ORDER BY run_at DESC LIMIT 7").fetchall()
    last = loads[0] if loads else None
    out: dict[str, Any] = {
        "recalls_loaded": rows,
        "last_load_at": last["run_at"] if last else None,
        "last_load_status": last["status"] if last else "never",
        "newest_recall_date": newest,
    }
    warnings: list[str] = []
    if last is None:
        warnings.append("No load has ever been recorded.")
    else:
        ran = dt.datetime.strptime(last["run_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        hours = (now - ran).total_seconds() / 3600
        out["hours_since_last_load"] = round(hours, 1)
        if hours > LOAD_OVERDUE_HOURS:
            warnings.append(f"Last load was {hours / 24:.1f} days ago; loads are nightly, so one is overdue.")
        if any(r["status"] != "ok" for r in loads[:1]):
            warnings.append(f"Last load status is {last['status']!r}.")
    if newest:
        days = (now.date() - dt.date.fromisoformat(newest)).days
        out["days_since_newest_recall"] = days
        if days > NO_NEW_RECALLS_DAYS:
            warnings.append(
                f"The newest recall is {days} days old but CPSC publishes weekly, so recent recalls may be missing."
            )
            if sum(r["rows_upserted"] for r in loads) > 0:
                warnings.append(
                    "Recent loads report upserting rows yet no recall is newer than that date: "
                    "the load log and the data disagree, so treat 'ok' loads with suspicion."
                )
    out["data_may_be_stale"] = bool(warnings)
    out["warnings"] = warnings
    return out
