#!/usr/bin/env python3
"""One-off: replace expired Skiplagged booking-token URLs on trip 14's car
options with the durable search URL, then re-render doc + app."""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

DB = os.path.expanduser("~/.hermes/data/trips.db")
S = os.path.expanduser("~/.hermes/scripts")
SEARCH = "https://skiplagged.com/cars/smf/-/2026-09-11/10:00/smf/-/2026-09-14/10:00"

conn = sqlite3.connect(DB, timeout=10)
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
cur = conn.execute(
    """UPDATE plan_options SET url = ?, link_ok = 1, updated_at = ?
       WHERE trip_id = 14 AND url LIKE '%skiplagged.com/car/book/%'""",
    (SEARCH, now))
conn.commit()
print(f"rewrote {cur.rowcount} token links → search URL")
conn.close()

for cmd in (("trip_doc.py", "update", "14"), ("trip_pwa.py", "deploy", "14", "--if-deployed")):
    r = subprocess.run([sys.executable, os.path.join(S, cmd[0])] + list(cmd[1:]),
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
