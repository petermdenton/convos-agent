"""collector-first-contact hook.

Fires on agent:start for every inbound message. If the chat has no trip bound
in ~/.hermes/data/trips.db, this handler — not the model — runs first contact:

  1. INSERT the trip row bound to the chat (atomic; doubles as the dedup lock)
  2. subprocess trip_doc.py create  → real Google Doc URL
  3. POST the welcome (with the URL) straight to the Photon sidecar /send

Errors never propagate — worst case the model handles it per SOUL.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
DB = HOME / "data" / "trips.db"
TRIP_DOC = HOME / "scripts" / "trip_doc.py"
RUNTIME = HOME / "runtime" / "photon-sidecar.json"

WELCOME = ("Hey, just start dropping things in the chat and I will organize "
           "it into a Google Doc: {url}")


def _log(msg):
    try:
        with open(HOME / "logs" / "collector-hook.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _first_contact(chat_id: str, chat_type: str) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Ensure minimum schema exists even on a virgin DB.
        conn.execute("""CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            destination TEXT, start_date TEXT, end_date TEXT,
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trips)")}
        for col in ("chat_id", "doc_id", "doc_url"):
            if col not in cols:
                conn.execute(f"ALTER TABLE trips ADD COLUMN {col} TEXT")
        conn.commit()

        existing = conn.execute(
            "SELECT id FROM trips WHERE chat_id = ? AND archived = 0",
            (chat_id,)).fetchone()
        if existing:
            return  # chat already onboarded — nothing to do

        name = "Group Trip" if chat_type == "group" else "Trip"
        cur = conn.execute(
            "INSERT INTO trips (name, created_at, chat_id) VALUES (?, ?, ?)",
            (name, datetime.now(timezone.utc).isoformat(timespec="seconds"), chat_id))
        conn.commit()
        trip_id = cur.lastrowid
        _log(f"first contact for {chat_id}: created trip {trip_id}")
    finally:
        conn.close()

    # 2. Create the Google Doc (subprocess so its sqlite writes are its own).
    url = None
    try:
        out = subprocess.run(
            [sys.executable, str(TRIP_DOC), "create", str(trip_id)],
            capture_output=True, text=True, timeout=120)
        if out.returncode == 0:
            url = json.loads(out.stdout).get("url")
        else:
            _log(f"trip_doc create failed: {out.stderr.strip()[:300]}")
    except Exception as e:  # noqa: BLE001
        _log(f"trip_doc create error: {e}")

    if not url:
        return  # model will pick it up per SOUL; better silent than linkless

    # 3. Send the welcome via the sidecar.
    try:
        record = json.loads(RUNTIME.read_text())
        port, token = record.get("port", 8789), record.get("token")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/send",
            data=json.dumps({"spaceId": chat_id, "text": WELCOME.format(url=url)}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Hermes-Sidecar-Token": token or ""},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            _log(f"welcome sent to {chat_id} (HTTP {resp.status}), doc {url}")
    except Exception as e:  # noqa: BLE001
        _log(f"welcome send failed: {e}")


async def handle(event_type, context):
    try:
        if event_type != "agent:start":
            return
        if (context or {}).get("platform") != "photon":
            return
        chat_id = (context or {}).get("chat_id") or ""
        if not chat_id:
            return
        chat_type = (context or {}).get("chat_type") or ""
        # Off the event loop — sqlite + subprocess + http are all blocking.
        await asyncio.to_thread(_first_contact, chat_id, chat_type)
    except Exception as e:  # noqa: BLE001
        _log(f"hook error: {e}")
