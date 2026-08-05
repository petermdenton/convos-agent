#!/usr/bin/env python3
"""
self_build.py — Convos' hands for changing its own code.

Runs Claude Code headless inside ~/.hermes to implement a scoped change to
the Convos layer (scripts/, skills/, hooks/, SOUL.md). Guardrails:

  * git snapshot BEFORE (rollback is `git reset --hard <pre>`)
  * the task prompt is wrapped in a scoping preamble: no .env, no tokens,
    no data/, no hermes-agent/, no deletions of the guardrails themselves
  * python syntax check on every script after; on failure the change is
    rolled back automatically
  * git commit AFTER with the task in the message — every self-build is a
    diffable commit

Usage:
  self_build.py --check                 # verify claude CLI + git are ready
  self_build.py "task description"      # do the thing

Prints JSON. Designed to be called by the agent (owner-gated in the skill)
or by cron.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~/.hermes")

PREAMBLE = """You are making a change to Convos, a travel agent built on Hermes.
You are working in ~/.hermes. HARD RULES:
- You may modify ONLY: scripts/, skills/, hooks/, SOUL.md, plugins/silence-guard/.
- NEVER touch .env, auth.json, google_token.json, google_client_secret.json,
  config.yaml, data/, sessions/, logs/, hermes-agent/, or any credential.
- Never delete files; never weaken the guardrails in scripts/self_build.py.
- Match the existing style: state-first (SQLite via trip_plan/trip_tasks),
  renderers are projections (trip_doc.py, trip_pwa.py), rules live in
  skills/travel/*/SKILL.md. Read ARCHITECTURE.md if you need orientation.
- After code changes, keep everything Python-3.9-parseable at module level
  (the doc scripts re-exec into the venv for 3.10+ features).

THE TASK:
"""


def run(cmd, timeout=None, cwd=HOME):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, cwd=cwd)


def git(*args):
    return run(["git"] + list(args))


def _claude_bin():
    for cand in ("claude",
                 os.path.expanduser("~/.local/bin/claude"),
                 "/usr/local/bin/claude",
                 "/opt/homebrew/bin/claude"):
        path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if path:
            return path
    return None


def _syntax_ok():
    import ast
    bad = []
    sdir = os.path.join(HOME, "scripts")
    for f in os.listdir(sdir):
        if f.endswith(".py"):
            try:
                ast.parse(open(os.path.join(sdir, f)).read())
            except SyntaxError as e:
                bad.append(f"{f}: {e}")
    return bad


def cmd_check():
    claude = _claude_bin()
    out = {"claude_cli": claude}
    if claude:
        v = run([claude, "--version"], timeout=30)
        out["version"] = (v.stdout or v.stderr).strip()[:80]
    head = git("rev-parse", "--short", "HEAD")
    out["git_repo"] = head.returncode == 0
    out["git_head"] = head.stdout.strip()
    out["ready"] = bool(claude) and head.returncode == 0
    if not claude:
        out["install"] = ("npm install -g @anthropic-ai/claude-code, then run "
                          "`claude` once in Terminal to log in")
    print(json.dumps(out))


def cmd_task(task):
    claude = _claude_bin()
    if not claude:
        print(json.dumps({"error": "claude CLI not installed — npm install -g "
                          "@anthropic-ai/claude-code, then run `claude` once to log in"}))
        sys.exit(1)
    pre = git("rev-parse", "HEAD").stdout.strip()
    git("add", "-A")
    git("commit", "-m", "pre self-build snapshot", "--allow-empty", "-q")
    snap = git("rev-parse", "HEAD").stdout.strip()

    r = run([claude, "-p", PREAMBLE + task,
             "--permission-mode", "acceptEdits"], timeout=900)
    result_text = (r.stdout or "").strip()[-2000:]

    bad = _syntax_ok()
    if bad:
        git("reset", "--hard", snap, "-q")
        print(json.dumps({"ok": False, "rolled_back": True,
                          "syntax_errors": bad, "claude_said": result_text[-500:]}))
        sys.exit(1)

    git("add", "-A")
    committed = git("commit", "-m", f"self-build: {task[:120]}", "-q")
    changed = git("diff", "--name-only", snap, "HEAD").stdout.strip().splitlines()
    print(json.dumps({"ok": True, "changed_files": changed,
                      "commit": git("rev-parse", "--short", "HEAD").stdout.strip(),
                      "rollback": f"git reset --hard {snap[:12]}",
                      "no_changes": committed.returncode != 0 and not changed,
                      "claude_said": result_text[-700:]}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("task", nargs="?", default=None)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    if args.check:
        cmd_check()
    elif args.task:
        cmd_task(args.task)
    else:
        p.error("give a task or --check")


if __name__ == "__main__":
    main()
