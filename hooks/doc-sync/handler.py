"""doc-sync hook.

After every agent turn (agent:end) in a Photon chat with a bound trip:
fingerprint the trip's mutable state; if it differs from the fingerprint at
the last successful doc render, run `trip_doc.py update` and store the new
fingerprint. Guarantees: any state change lands in the doc within the same
turn, even when the model forgets the update step. No change → no API call.

Fingerprints live in ~/.hermes/data/doc_sync.json. Errors are logged, never
raised, and never block the pipeline.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
DB = HOME / "data" / "trips.db"
MARKS = HOME / "data" / "doc_sync.json"
TRIP_DOC = HOME / "scripts" / "trip_doc.py"

_TABLES = ("plan_options", "plan_itinerary", "plan_summaries", "plan_working",
           "plan_log", "trip_travelers", "tasks")


def _log(msg):
    try:
        with open(HOME / "logs" / "doc-sync.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _fingerprint(conn, trip_id):
    parts = []
    for t in _TABLES:
        try:
            n, mx = conn.execute(
                f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {t} WHERE trip_id = ?",
                (trip_id,)).fetchone()
            # updated_at when the table has it (edits without row growth)
            try:
                up = conn.execute(
                    f"SELECT COALESCE(MAX(updated_at), '') FROM {t} WHERE trip_id = ?",
                    (trip_id,)).fetchone()[0]
            except sqlite3.OperationalError:
                up = ""
            parts.append(f"{t}:{n}:{mx}:{up}")
        except sqlite3.OperationalError:
            parts.append(f"{t}:-")
    return "|".join(parts)


def _sync(chat_id):
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        trip = conn.execute(
            "SELECT id, doc_id FROM trips WHERE chat_id = ? AND archived = 0",
            (chat_id,)).fetchone()
        if not trip or not trip["doc_id"]:
            return
        trip_id = trip["id"]
        fp = _fingerprint(conn, trip_id)
    finally:
        conn.close()

    try:
        marks = json.loads(MARKS.read_text()) if MARKS.exists() else {}
    except Exception:  # noqa: BLE001
        marks = {}
    if marks.get(str(trip_id)) == fp:
        return  # nothing changed since last render

    out = subprocess.run([sys.executable, str(TRIP_DOC), "update", str(trip_id)],
                         capture_output=True, text=True, timeout=180)
    if out.returncode == 0:
        marks[str(trip_id)] = fp
        try:
            MARKS.write_text(json.dumps(marks))
        except OSError:
            pass
        _log(f"trip {trip_id}: state changed → doc re-rendered")
    else:
        _log(f"trip {trip_id}: update FAILED: {(out.stderr or '').strip()[:200]}")


async def handle(event_type, context):
    try:
        if event_type != "agent:end":
            return
        ctx = context or {}
        if ctx.get("platform") != "photon":
            return
        chat_id = ctx.get("chat_id") or ""
        if not chat_id:
            return
        await asyncio.to_thread(_sync, chat_id)
    except Exception as e:  # noqa: BLE001
        _log(f"hook error: {e}")
