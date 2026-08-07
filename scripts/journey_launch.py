#!/usr/bin/env python3
"""Launch the journey driver detached so the cron job returns immediately
while the ~40-minute conversation plays out on its own."""
import os
import subprocess
import sys

driver = os.path.expanduser("~/.hermes/scripts/journey_driver.py")
p = subprocess.Popen([sys.executable, driver],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
print(f"journey driver launched, pid {p.pid} — follow eval/journey_run.log")
