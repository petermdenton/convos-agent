#!/usr/bin/env python3
"""Eval reset: unbind the Convos DM (Pete's number) — delete its test trips
so the next message triggers first contact fresh. Also removes the stray
test option (Frank Fat's) from trip 6."""
import os
import sqlite3
import subprocess
import sys

DB = os.path.expanduser("~/.hermes/data/trips.db")
S = os.path.expanduser("~/.hermes/scripts")

conn = sqlite3.connect(DB, timeout=10)
conn.row_factory = sqlite3.Row
dm_trips = [r["id"] for r in conn.execute(
    "SELECT id FROM trips WHERE chat_id = 'any;-;+12064273866' AND archived = 0")]
conn.execute("DELETE FROM plan_options WHERE id = 155")  # test Frank Fat's in trip 6
conn.commit()
conn.close()
print(f"dm trips to delete: {dm_trips}; removed test option 155")

for tid in dm_trips:
    r = subprocess.run([sys.executable, f"{S}/trip_tasks.py", "delete-trip", str(tid)],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
