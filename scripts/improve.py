#!/usr/bin/env python3
"""improve.py — apply ONE fix per night via self_build (Claude Code CLI).

Picks the single most important open, auto-fixable issue from
eval/issues.md (PRIORITY first, then lowest row number; platform-tier rows
are never picked — those are Hermes upstream). Hands it to self_build.py,
which snapshots git, lets Claude edit only the allowed surface, syntax-
checks, and rolls back on breakage. Then runs the gate self-test as a
hard acceptance gate — a "fix" that breaks the react-only law is reverted.
On success the row is marked fix-applied; the NEXT night's scorer decides
fixed vs reopen from fresh evidence."""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
ISSUES = os.path.join(HOME, "eval", "issues.md")
SB = os.path.join(HOME, "scripts", "self_build.py")
SKIP = re.compile(r"fixed|mitigated|fix-applied|moot", re.I)


def pick(text):
    cands = []
    for m in re.finditer(r"^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|\s*$", text, re.M):
        num, what, expected, tier, status = (g.strip() for g in m.groups())
        if SKIP.search(status):
            continue
        if tier.lower().startswith("platform"):
            continue
        cands.append({"num": int(num), "what": what, "expected": expected,
                      "tier": tier, "status": status,
                      "priority": "PRIORITY" in status or "PRIORITY" in what})
    if not cands:
        return None
    cands.sort(key=lambda c: (not c["priority"], c["num"]))
    return cands[0]


def main():
    text = open(ISSUES).read()
    issue = pick(text)
    if not issue:
        print(json.dumps({"picked": None, "note": "no open auto-fixable issues"}))
        return
    task = (
        f"Fix eval issue #{issue['num']}.\n"
        f"OBSERVED: {issue['what']}\n"
        f"EXPECTED: {issue['expected']}\n"
        f"SUGGESTED TIER/FIX: {issue['tier']}\n\n"
        "Make the smallest correct change. Follow the three-tier ratchet: "
        "prefer deterministic code (hooks/renderers/wrappers) over canned "
        "strings over prompt rules. After editing, ensure "
        "`python3 ~/.hermes/scripts/gate_selftest.py` passes — the react-only "
        "law (untagged turns suppressed) must keep working. Do not weaken "
        "silence-guard or tag-gate. Do not touch protected files."
    )
    r = subprocess.run(["python3", SB, task], capture_output=True, text=True,
                       timeout=1200)
    ok = r.returncode == 0
    st = subprocess.run(["python3", os.path.join(HOME, "scripts", "gate_selftest.py")],
                        capture_output=True, text=True, timeout=120)
    if ok and st.returncode != 0:
        subprocess.run(["git", "-C", HOME, "reset", "--hard", "HEAD~1"],
                       capture_output=True, text=True)
        ok = False
    if ok:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gh = re.search(r"gh#\d+", issue["status"])
        tag = f" ({gh.group(0)})" if gh else ""
        pat = re.compile(rf"^(\|\s*{issue['num']}\s*\|.+?\|.+?\|.+?\|\s*)[^|]+?(\|)\s*$", re.M)
        text = pat.sub(rf"\1fix-applied {day} (verify next run){tag} \2", text, count=1)
        open(ISSUES, "w").write(text)
    print(json.dumps({"picked": issue["num"], "what": issue["what"][:90],
                      "build_ok": ok,
                      "selftest": st.stdout.strip()[-80:],
                      "build_tail": (r.stdout or r.stderr).strip()[-200:]}))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
