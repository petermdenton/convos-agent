#!/usr/bin/env python3
import subprocess
script = ('tell application "Messages"\n'
          'set out to ""\n'
          'repeat with c in chats\n'
          'try\n'
          'set pnames to ""\n'
          'repeat with p in (participants of c)\n'
          'set pnames to pnames & (handle of p) & ","\n'
          'end repeat\n'
          'set out to out & (id of c) & " | " & pnames & "\n"\n'
          'end try\n'
          'end repeat\n'
          'return out\n'
          'end tell')
r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=90)
print(r.stdout[:4000] or r.stderr[:500])
