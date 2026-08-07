#!/usr/bin/env python3
"""eval/check.py — instant eval verdict from state, no screenshots.

Given a window start (UTC ISO or unix ts), reports everything observable:
  * assistant messages per session since then — split into DELIVERED bubbles
    vs suppressed silence markers (NO_REPLY/[SILENT])
  * options/itinerary/roster changes filed since then, per trip
  * silence-guard suppressions and gateway errors in the window

Usage:  eval_check.py [--since "2026-08-05T19:50"]   (default: last 10 min)
Prints JSON.
"""
import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
SILENCE = {"NO_REPLY", "NO REPLY", "[SILENT]", "SILENT"}


def _canon(t):
    return " ".join((t or "").strip().upper().split()).strip("*_.()[] ") or ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None)
    args = p.parse_args()
    if args.since:
        dt = datetime.fromisoformat(args.since)
        since_ts = dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        since_iso = datetime.fromtimestamp(since_ts, timezone.utc).isoformat(timespec="seconds")
    else:
        since_ts = time.time() - 600
        since_iso = datetime.fromtimestamp(since_ts, timezone.utc).isoformat(timespec="seconds")

    out = {"since": since_iso, "delivered": [], "suppressed": [],
           "filed": [], "guard_log": [], "errors": []}

    s = sqlite3.connect(f"file:{HOME}/state.db?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    for r in s.execute(
            """SELECT m.session_id, m.content, m.timestamp, se.chat_id
               FROM messages m LEFT JOIN sessions se ON se.id = m.session_id
               WHERE m.role = 'assistant' AND m.timestamp > ?
               AND m.content IS NOT NULL AND m.content != ''
               ORDER BY m.timestamp""", (since_ts,)):
        text = r["content"].strip()
        entry = {"chat": (r["chat_id"] or r["session_id"] or "")[-18:],
                 "at": datetime.fromtimestamp(r["timestamp"], timezone.utc)
                 .strftime("%H:%M:%S"),
                 "text": text[:160]}
        if _canon(text) in SILENCE:
            out["suppressed"].append(entry)
        else:
            out["delivered"].append(entry)
    s.close()

    t = sqlite3.connect(f"file:{HOME}/data/trips.db?mode=ro", uri=True)
    t.row_factory = sqlite3.Row
    for r in t.execute(
            """SELECT trip_id, kind, label, status, url IS NOT NULL AS has_url,
               phone IS NOT NULL AS has_phone, created_at, updated_at
               FROM plan_options WHERE updated_at > ? ORDER BY id""",
            (since_iso,)):
        out["filed"].append(dict(r))
    for r in t.execute("SELECT trip_id, day, slot, text FROM plan_itinerary "
                       "WHERE updated_at > ?", (since_iso,)):
        out["filed"].append({"itinerary": dict(r)})
    t.close()

    for log, key in (("silence-guard.log", "guard_log"),
                     ("gateway.error.log", "errors")):
        path = os.path.join(HOME, "logs", log)
        try:
            for line in open(path).readlines()[-200:]:
                if line[:19] >= since_iso[:19].replace("T", "T"):
                    if line[:4].isdigit() and line[:19] >= since_iso[:19]:
                        out[key].append(line.strip()[:200])
        except OSError:
            pass
    out["verdict_hint"] = (f"{len(out['delivered'])} delivered bubble(s), "
                           f"{len(out['suppressed'])} suppressed, "
                           f"{len(out['filed'])} state change(s)")
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
