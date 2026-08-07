#!/usr/bin/env python3
import os, subprocess
CLAUDE = os.path.expanduser("~/.local/bin/claude")
r = subprocess.run([CLAUDE, "-p", "Reply with exactly the word: pong"],
                   capture_output=True, text=True, timeout=240)
print("rc:", r.returncode)
print("stdout:", (r.stdout or "")[:300])
print("stderr:", (r.stderr or "")[:400])
