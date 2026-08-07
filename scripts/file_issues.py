#!/usr/bin/env python3
"""file_issues.py — sync eval/issues.md rows to GitHub Issues.

Every un-synced, un-fixed row in the issues tables becomes a GitHub issue
on the convos-agent repo (via the gh CLI, using its existing auth). The row
is annotated in place with (gh#N) so syncs are idempotent. Fixed rows are
never filed; a previously-filed row that later gets marked fixed is closed.

Usage: file_issues.py            # sync
Prints a JSON summary. Safe to run repeatedly (e.g. after every eval run).
"""
import json
import os
import re
import shutil
import subprocess

HOME = os.path.expanduser("~/.hermes")
ISSUES = os.path.join(HOME, "eval", "issues.md")
REPO = "petermdenton/convos-agent"


def gh():
    for cand in ("gh", "/usr/local/bin/gh", "/opt/homebrew/bin/gh",
                 os.path.expanduser("~/.local/bin/gh")):
        p = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if p:
            return p
    return None


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def main():
    bin_ = gh()
    if not bin_:
        print(json.dumps({"error": "gh CLI not installed — brew install gh && gh auth login"}))
        return
    auth = run([bin_, "auth", "status"])
    if auth.returncode != 0:
        print(json.dumps({"error": "gh not authenticated — run `gh auth login` once"}))
        return

    text = open(ISSUES).read()
    lines = text.splitlines()
    created, closed, skipped = [], [], 0
    section = ""
    for i, line in enumerate(lines):
        if line.startswith("##"):
            section = line.lstrip("# ").strip()
            continue
        m = re.match(r"^\|\s*(\d+)\s*\|(.+)\|(.+)\|(.+)\|(.+)\|\s*$", line)
        if not m:
            continue
        num, what, expected, tier, status = (g.strip() for g in m.groups())
        ghm = re.search(r"gh#(\d+)", status)
        is_fixed = bool(re.search(r"\bfixed\b|\bmitigated\b", status, re.I))
        if ghm and is_fixed:
            r = run([bin_, "issue", "close", ghm.group(1), "-R", REPO,
                     "-c", f"Marked {status.split('(')[0].strip()} in eval/issues.md."])
            if r.returncode == 0:
                closed.append(int(ghm.group(1)))
                lines[i] = line.replace(f"gh#{ghm.group(1)}", f"gh#{ghm.group(1)} closed")
            continue
        if ghm or is_fixed:
            skipped += 1
            continue
        title = f"[eval #{num}] {what[:80]}"
        body = (f"**Observed:** {what}\n\n**Expected:** {expected}\n\n"
                f"**Fix tier:** {tier}\n\n**From:** {section}\n\n"
                f"_Filed automatically from `eval/issues.md`._")
        r = run([bin_, "issue", "create", "-R", REPO, "-t", title, "-b", body,
                 "-l", "eval"])
        if r.returncode != 0 and "could not add label" in (r.stderr or "").lower():
            r = run([bin_, "issue", "create", "-R", REPO, "-t", title, "-b", body])
        if r.returncode == 0:
            issue_no = r.stdout.strip().rsplit("/", 1)[-1]
            created.append(int(issue_no))
            lines[i] = re.sub(r"\|\s*([^|]*?)\s*\|\s*$",
                              lambda mm: f"| {mm.group(1)} (gh#{issue_no}) |", line)
        else:
            print(f"create failed: {r.stderr.strip()[:120]}")

    if created or closed:
        open(ISSUES, "w").write("\n".join(lines) + "\n")
    print(json.dumps({"created": created, "closed": closed, "skipped": skipped}))


if __name__ == "__main__":
    main()
