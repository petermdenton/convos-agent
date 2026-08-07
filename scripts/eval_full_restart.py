#!/usr/bin/env python3
"""Full eval reset: kill any running journey, unbind + wipe the Convos DM,
restart the gateway. (The journey relaunch is a separate scheduled job so
the gateway is fully up before messages start.)"""
import os
import shutil
import sqlite3
import subprocess
import sys

CHAT = "any;-;+12064273866"
HOME = os.path.expanduser("~/.hermes")

subprocess.run(["pkill", "-f", "journey_driver.py"], capture_output=True)
print("journey driver killed (if running)")

conn = sqlite3.connect(f"{HOME}/data/trips.db", timeout=10)
conn.row_factory = sqlite3.Row
tids = [r["id"] for r in conn.execute(
    "SELECT id FROM trips WHERE chat_id = ? AND archived = 0", (CHAT,))]
conn.close()
for tid in tids:
    r = subprocess.run([sys.executable, f"{HOME}/scripts/trip_tasks.py",
                        "delete-trip", str(tid)], capture_output=True, text=True)
    print(f"deleted trip {tid}")

s = sqlite3.connect(f"{HOME}/state.db", timeout=15)
ids = [r[0] for r in s.execute("SELECT id FROM sessions WHERE chat_id = ?", (CHAT,))]
for sid in ids:
    s.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
s.execute("DELETE FROM sessions WHERE chat_id = ?", (CHAT,))
s.commit()
s.close()
print(f"wiped {len(ids)} session(s)")

with open(f"{HOME}/eval/journey_run.log", "a") as f:
    f.write("=== FULL RESET — starting over ===\n")

for cand in ("hermes", os.path.expanduser("~/.local/bin/hermes"),
             os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")):
    path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
    if path:
        env = {k: v for k, v in os.environ.items() if k != "_HERMES_GATEWAY"}
        r = subprocess.run([path, "gateway", "restart"], capture_output=True,
                           text=True, timeout=120, env=env)
        print((r.stdout or r.stderr).strip()[-150:])
        break
