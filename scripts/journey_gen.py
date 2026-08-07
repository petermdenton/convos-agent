#!/usr/bin/env python3
"""journey_gen.py — generate tonight's simulated journey with Claude.

Grounds the script in the Reddit/Medium pain corpus (eval/scenarios.json),
rotates destinations (never repeats the last 5 runs), and always embeds the
canonical probes the scorer judges against. Writes eval/active_journey.json
and archives a copy in eval/journeys/. Falls back to the last archived
journey (destination intact) if generation fails — the nightly run must
never die here."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
EV = os.path.join(HOME, "eval")
CLAUDE = os.path.expanduser("~/.local/bin/claude")

PROBES = """Every journey MUST include, woven naturally into the flow:
- ~45-50 messages total, ~65-70% untagged chatter/link-drops (must get NO reply), rest tagged "Convos ..." asks
- a vague-dates dreaming phase before "Convos plan it: <city>, <real dates>, <N> of us"
- 8-12 REAL researchable links (real airbnb/vrbo search or property pages, real restaurant/venue sites) dropped untagged
- EXACTLY ONE deliberately dead link (e.g. https://www.airbnb.com/rooms/99999999999) dropped untagged
- one member with a constraint that should NOT be pried into (budget, health) mentioned untagged
- 3-4 bookings announced with confirmation codes, times, check-in/out details
- one traveler arriving separately on their own flight with airline+number+conf
- one "Convos <set a watch/reminder for a date>" ask
- "Convos build out the day-by-day" and "Convos send us the trip app"
- "Convos stop", then 2 untagged messages, then a tagged re-engage
- 2-3 travel-day recall asks (confirmation numbers, arrival times)
- realistic imperfect texting; delays 30-120s (first message delay 5)"""


def recent_destinations():
    dests = []
    try:
        for line in open(os.path.join(EV, "scorecards.jsonl")):
            try:
                dests.append(json.loads(line).get("destination", ""))
            except ValueError:
                pass
    except OSError:
        pass
    return [d for d in dests if d][-5:]


def generate():
    corpus = json.load(open(os.path.join(EV, "scenarios.json")))
    pains = [s.get("pain", "") for s in corpus.get("scenarios", []) if s.get("pain")]
    avoid = recent_destinations()
    prompt = f"""Write a simulated iMessage group-trip-planning journey to test a travel agent bot called Convos.

Consumer pain points to exercise (from Reddit/Medium research): {json.dumps(pains)}
Destinations used recently — pick something ELSE: {json.dumps(avoid)}

{PROBES}

Output ONLY valid JSON, no markdown fences, exactly this shape:
{{"name": "<city> — <one-line premise>", "destination": "<city>",
 "expectations": ["<8-14 scorer expectations specific to THIS script>"],
 "messages": [[<delay_seconds>, "<message text>"], ...]}}"""
    r = subprocess.run([CLAUDE, "-p", prompt], capture_output=True, text=True,
                       timeout=420)
    out = (r.stdout or "").strip()
    m = re.search(r"\{.*\}", out, re.S)
    data = json.loads(m.group(0))
    msgs = data["messages"]
    assert len(msgs) >= 40, f"only {len(msgs)} messages"
    joined = " ".join(t for _, t in msgs)
    assert "Convos stop" in joined, "missing stop test"
    assert "99999999999" in joined or "dead" in joined.lower(), "missing 404 probe"
    assert sum(1 for _, t in msgs if re.search(r"\bconvos\b", t, re.I)) >= 10
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def main():
    os.makedirs(os.path.join(EV, "journeys"), exist_ok=True)
    active = os.path.join(EV, "active_journey.json")
    try:
        data = generate()
    except Exception as e:  # noqa: BLE001
        print(f"generation failed ({e}); falling back to previous journey", file=sys.stderr)
        arch = sorted(os.listdir(os.path.join(EV, "journeys"))) if os.path.isdir(os.path.join(EV, "journeys")) else []
        if arch:
            shutil.copy(os.path.join(EV, "journeys", arch[-1]), active)
        print(json.dumps({"fallback": True}))
        return
    json.dump(data, open(active, "w"), indent=1)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    json.dump(data, open(os.path.join(EV, "journeys", f"{day}.json"), "w"), indent=1)
    print(json.dumps({"destination": data["destination"], "messages": len(data["messages"])}))


if __name__ == "__main__":
    main()
