#!/usr/bin/env python3
"""score_run.py --since <iso> — judge tonight's journey with Claude.

Feeds the rubric, the journey's own expectations, check.py's state-level
evidence and the guard/gate logs to `claude -p`; gets back a scorecard,
NEW issue rows, and verdicts on rows previously marked fix-applied.
Appends the scorecard to eval/scorecards.jsonl and the rows to
eval/issues.md. Deterministic code applies everything; the model only
judges."""
import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone

HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
EV = os.path.join(HOME, "eval")
CLAUDE = os.path.expanduser("~/.local/bin/claude")


def tail(path, n):
    try:
        return "\n".join(open(path).read().splitlines()[-n:])
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    args = ap.parse_args()

    check = subprocess.run(
        ["python3", os.path.join(EV, "check.py"), "--since", args.since],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "HERMES_NO_REEXEC": "1"}).stdout
    journey = json.load(open(os.path.join(EV, "active_journey.json")))
    issues = open(os.path.join(EV, "issues.md")).read()
    fix_applied = re.findall(r"^\|\s*(\d+)\s*\|.*fix-applied.*$", issues, re.M)

    prompt = f"""You are scoring a simulated consumer journey for the Convos travel agent.

RUBRIC:
{open(os.path.join(EV, "rubric.md")).read()}

THIS JOURNEY'S SCRIPT + EXPECTATIONS:
{json.dumps({k: journey.get(k) for k in ("name", "destination", "expectations", "messages")})[:8000]}

STATE-LEVEL EVIDENCE (delivered bubbles, filings, suppressions):
{check[:16000]}

TAG-GATE LOG (tonight): {tail(os.path.join(HOME, "logs", "tag-gate.log"), 60)}
GUARD LOG (tonight): {tail(os.path.join(HOME, "logs", "silence-guard.log"), 40)}

KNOWN ISSUES ALREADY FILED (do NOT re-report these):
{issues[-6000:]}

Rows currently marked fix-applied (verify against tonight's evidence): {fix_applied}

Output ONLY valid JSON:
{{"scores": {{"onboarding": n, "burden_saved": n, "decision_support": n, "responsiveness": n, "noise": n, "trust": n}},
 "overall": n.n, "highlights": ["..."], "evidence": ["dimension: specific evidence"],
 "new_issues": [{{"what": "...", "expected": "...", "tier": "code|prompt|platform — suggested fix"}}],
 "verify": [{{"row": <num>, "verdict": "fixed"|"reopen", "why": "..."}}]}}"""
    r = subprocess.run([CLAUDE, "-p", prompt], capture_output=True, text=True, timeout=600)
    m = re.search(r"\{.*\}", (r.stdout or "").strip(), re.S)
    data = json.loads(m.group(0))

    # 1. scorecard history
    card = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "destination": journey.get("destination", "?"),
            "journey": journey.get("name", "?"),
            **{"scores": data["scores"], "overall": data["overall"],
               "highlights": data.get("highlights", [])}}
    with open(os.path.join(EV, "scorecards.jsonl"), "a") as f:
        f.write(json.dumps(card) + "\n")

    # 2. new issue rows (numbered after current max)
    text = issues
    nums = [int(n) for n in re.findall(r"^\|\s*(\d+)\s*\|", text, re.M)]
    nxt = (max(nums) if nums else 0) + 1
    new = data.get("new_issues", [])
    if new:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        block = [f"\n## Run {day} — nightly, {journey.get('destination','?')} "
                 f"(overall {data['overall']})\n",
                 "| # | What happened | Expected | Tier / fix | Status |",
                 "|---|---|---|---|---|"]
        for it in new:
            block.append(f"| {nxt} | {it['what']} | {it['expected']} | {it['tier']} | open |")
            nxt += 1
        text += "\n".join(block) + "\n"

    # 3. fix-applied verdicts
    for v in data.get("verify", []):
        row = str(v.get("row", ""))
        pat = re.compile(rf"^(\|\s*{row}\s*\|.*\|\s*)(fix-applied[^|]*?)(\|)\s*$", re.M)

        def _rep(m):
            gh = re.search(r"gh#\d+", m.group(2))
            tag = f" ({gh.group(0)})" if gh else ""
            word = "fixed" if v.get("verdict") == "fixed" else "open — fix ineffective"
            return f"{m.group(1)}{word}{tag} {m.group(3)}"

        text = pat.sub(_rep, text)
    open(os.path.join(EV, "issues.md"), "w").write(text)
    print(json.dumps(card))


if __name__ == "__main__":
    main()
