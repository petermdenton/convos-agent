#!/usr/bin/env python3
"""Re-render the Living Plan doc for every active trip that has one.

Deterministic utility (no agent needed): runs trip_doc.py update per trip,
which regenerates the doc from state with the current renderer. Safe to run
any time — the doc is a pure view.
"""
import json
import os
import sqlite3
import subprocess
import sys

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB", os.path.expanduser("~/.hermes/data/trips.db"))
TRIP_DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trip_doc.py")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    trips = conn.execute(
        "SELECT id, name FROM trips WHERE archived = 0 AND doc_id IS NOT NULL"
    ).fetchall()
    ok = fail = 0
    for t in trips:
        r = subprocess.run([sys.executable, TRIP_DOC, "update", str(t["id"])],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            ok += 1
            print(f"refreshed: trip {t['id']} — {t['name']}")
        else:
            fail += 1
            err = (r.stderr or r.stdout).strip()[:200]
            print(f"FAILED: trip {t['id']} — {t['name']}: {err}")
    print(f"\n{ok} doc(s) refreshed" + (f", {fail} failed" if fail else "."))


if __name__ == "__main__":
    main()
