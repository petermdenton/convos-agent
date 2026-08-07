#!/usr/bin/env python3
"""Restart the Hermes gateway (to load silence-guard v1.2) via the hermes CLI."""
import os
import shutil
import subprocess

for cand in ("hermes", os.path.expanduser("~/.local/bin/hermes"),
             "/usr/local/bin/hermes", "/opt/homebrew/bin/hermes"):
    path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
    if path:
        print(f"using {path}")
        env = {k: v for k, v in os.environ.items() if k != "_HERMES_GATEWAY"}
        r = subprocess.run([path, "gateway", "restart"],
                           capture_output=True, text=True, timeout=120, env=env)
        print((r.stdout or r.stderr).strip()[-400:])
        break
else:
    print("hermes CLI not found")
