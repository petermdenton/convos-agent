#!/usr/bin/env python3
"""Eval prep: wipe the Convos DM's session history (fresh-user simulation),
then restart the gateway to clear its agent cache."""
import os
import shutil
import sqlite3
import subprocess

CHAT = "any;-;+12064273866"
DB = os.path.expanduser("~/.hermes/state.db")

conn = sqlite3.connect(DB, timeout=15)
conn.row_factory = sqlite3.Row
ids = [r["id"] for r in conn.execute(
    "SELECT id FROM sessions WHERE chat_id = ?", (CHAT,))]
for sid in ids:
    conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
conn.execute("DELETE FROM sessions WHERE chat_id = ?", (CHAT,))
conn.commit()
conn.close()
print(f"wiped {len(ids)} session(s) for {CHAT}")

for cand in ("hermes", os.path.expanduser("~/.local/bin/hermes"),
             os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")):
    path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
    if path:
        env = {k: v for k, v in os.environ.items() if k != "_HERMES_GATEWAY"}
        r = subprocess.run([path, "gateway", "restart"], capture_output=True,
                           text=True, timeout=120, env=env)
        print((r.stdout or r.stderr).strip()[-200:])
        break
