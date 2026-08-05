#!/usr/bin/env python3
"""Smoke test: is the machine ready for self-build (claude CLI + git)?"""
import os
import subprocess
import sys

r = subprocess.run([sys.executable,
                    os.path.expanduser("~/.hermes/scripts/self_build.py"),
                    "--check"], capture_output=True, text=True)
print(r.stdout.strip() or r.stderr.strip())
