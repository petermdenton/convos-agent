"""roster-autolink hook.

Every inbound Photon message carries the sender's handle (phone or iMessage
email). If the chat has a trip bound, make sure that sender is on the trip
roster with their real name:

  1. upsert traveler keyed by handle (create stub if new)
  2. if the stored name is missing or a single word, resolve the handle via
     macOS Contacts (contact_lookup.py) and store the full name — this is
     what turns the doc's Travelers initials from "P" into "PD"
  3. link traveler to the chat's trip (idempotent)

Silent and non-blocking: errors are logged, never raised.
"""
import asyncio
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
DB = HOME / "data" / "trips.db"
SCRIPTS = HOME / "scripts"


def _log(msg):
    try:
        with open(HOME / "logs" / "roster-hook.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(handle):
    h = (handle or "").strip()
    if "@" in h:
        return h.lower()
    d = re.sub(r"\D", "", h)
    if len(d) == 10:
        d = "1" + d
    return f"+{d}" if d else ""


def _resolve_name(handle):
    try:
        sys.path.insert(0, str(SCRIPTS))
        from contact_lookup import lookup  # noqa: PLC0415
        return (lookup(handle) or {}).get("name")
    except Exception as e:  # noqa: BLE001
        _log(f"contact lookup failed for {handle}: {e}")
        return None


def _autolink(chat_id, sender):
    handle = _norm(sender)
    if not handle:
        return
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        trip = conn.execute(
            "SELECT id FROM trips WHERE chat_id = ? AND archived = 0",
            (chat_id,)).fetchone()
        if not trip:
            return
        trip_id = trip["id"]

        tv = conn.execute("SELECT * FROM travelers WHERE phone = ?",
                          (handle,)).fetchone()
        if not tv:
            conn.execute(
                "INSERT INTO travelers (phone, created_at, updated_at) VALUES (?, ?, ?)",
                (handle, _now(), _now()))
            conn.commit()
            tv = conn.execute("SELECT * FROM travelers WHERE phone = ?",
                              (handle,)).fetchone()

        # Name upgrade: only when missing or a single word (never clobber a
        # deliberate full name someone set).
        name = tv["name"] or ""
        if len(name.split()) < 2:
            full = _resolve_name(handle)
            if full and full != name:
                conn.execute("UPDATE travelers SET name = ?, updated_at = ? WHERE id = ?",
                             (full, _now(), tv["id"]))
                conn.commit()
                _log(f"named {handle} → {full}")

        linked = conn.execute(
            "SELECT 1 FROM trip_travelers WHERE trip_id = ? AND traveler_id = ?",
            (trip_id, tv["id"])).fetchone()
        if not linked:
            conn.execute(
                """INSERT INTO trip_travelers (trip_id, traveler_id, role, committed, added_at)
                   VALUES (?, ?, 'traveler', 0, ?)""",
                (trip_id, tv["id"], _now()))
            conn.commit()
            _log(f"linked {handle} to trip {trip_id}")
    finally:
        conn.close()


async def handle(event_type, context):
    try:
        if event_type != "agent:start":
            return
        ctx = context or {}
        if ctx.get("platform") != "photon":
            return
        chat_id, sender = ctx.get("chat_id") or "", ctx.get("user_id") or ""
        if not chat_id or not sender:
            return
        await asyncio.to_thread(_autolink, chat_id, sender)
    except Exception as e:  # noqa: BLE001
        _log(f"hook error: {e}")
