#!/usr/bin/env python3
"""Detach nightly_eval.py so the cron slot returns immediately."""
import os
import subprocess
import sys

target = os.path.expanduser("~/.hermes/scripts/nightly_eval.py")
p = subprocess.Popen([sys.executable, target],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
print(f"nightly eval launched, pid {p.pid} — follow eval/nightly.log")
