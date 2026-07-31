#!/usr/bin/env python3
"""Cron pre-check wrapper: runs `doc_comments.py check`.

Hermes cron's `script` field takes a bare path (no arguments), so this
wrapper exists to invoke the check subcommand. Prints the digest of doc
comment threads needing a reply, ending with a wakeAgent JSON gate.
"""
import os
import runpy
import sys

sys.argv = [os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "doc_comments.py"), "check"]
runpy.run_path(sys.argv[0], run_name="__main__")
