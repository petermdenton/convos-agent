#!/usr/bin/env python3
"""Live tag-gate probe: one untagged drop (must be silent), one tagged ask
(must be answered). Results read from logs afterward."""
import subprocess, time

CHAT = "any;-;+16282895466"

def send(text):
    esc = text.replace('\\', '\\\\').replace('"', '\\"')
    s = f'tell application "Messages"\n  send "{esc}" to chat id "{CHAT}"\nend tell'
    r = subprocess.run(["osascript", "-e", s], capture_output=True, text=True)
    print("sent" if r.returncode == 0 else f"FAIL {r.stderr.strip()[:100]}", "::", text)

send("gate test: https://www.tokyobanana.jp souvenir idea, grabbing some at Haneda")
time.sleep(75)
send("Convos gate test: what airport do we fly out of again?")
