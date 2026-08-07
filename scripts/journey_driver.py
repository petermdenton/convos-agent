#!/usr/bin/env python3
"""journey_driver.py — autonomous 45-message trip-planning journey.

Sends a realistic, corpus-grounded conversation to the Convos Agent chat
via AppleScript (real iMessages through the real pipeline), paced with
human-ish delays. Runs detached on the Mac for ~40 minutes; progress in
eval/journey_run.log. Scoring happens afterwards from state (eval/check.py).

The script covers the full arc — dreaming, feasibility, link-dump research,
decision paralysis, booking & money, roster drama, changes, pre-trip,
travel-day — with ~70% untagged drops (must be invisible) and ~30% tagged
asks (must be answered).
"""
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

LOG = os.path.expanduser("~/.hermes/eval/journey_run.log")
CHATDB = os.path.expanduser("~/Library/Messages/chat.db")


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")


CONVOS_HANDLE = "+16282895466"


def find_convos_chat():
    """Known Convos Agent handle; chat ids on this system are any;-;<handle>."""
    return "any;-;" + CONVOS_HANDLE


def send(chat_guid, text):
    escaped = text.replace('\\', '\\\\').replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set theChat to a reference to chat id "{chat_guid}"
        send "{escaped}" to theChat
    end tell'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        # fallback: send to the participant directly
        fb = (f'tell application "Messages" to send "{escaped}" to participant '
              f'"{CONVOS_HANDLE}" of (1st account whose service type = iMessage)')
        r2 = subprocess.run(["osascript", "-e", fb], capture_output=True, text=True)
        if r2.returncode != 0:
            log(f"SEND FAILED: {r.stderr.strip()[:100]} / {r2.stderr.strip()[:100]} :: {text[:60]}")
            return False
    return True


# (delay_before_seconds, message)
JOURNEY = [
    # ── Dreaming ──
    (5,  "ok new trip idea. thinking Austin for Kelly's birthday, sometime in November"),
    (45, "no wait. what about Nashville instead, flights are usually cheaper"),
    (50, "https://www.travelandleisure.com/best-things-to-do-in-nashville-7508188 this list looks fun"),
    (40, "Convos which is cheaper to fly into from Seattle in November, Austin or Nashville?"),
    (75, "ok Nashville it is"),
    # ── Feasibility ──
    (40, "Convos plan it: Nashville, November 12-15, probably 5 of us"),
    (70, "actually Dre isn't sure yet, count him as maybe"),
    (45, "Kelly says she can't leave until after her 2pm meeting on the 12th"),
    (40, "Convos any nonstops that leave SEA after 5pm on the 12th?"),
    (80, "hold that thought, Jamie's in. so 5 confirmed if Dre commits"),
    # ── Research: link dump ──
    (45, "https://www.airbnb.com/rooms/52301457 6 beds, east nashville"),
    (35, "https://www.vrbo.com/2234561 this one has a rooftop"),
    (35, "https://www.opentable.com/r/husk-nashville for a nice dinner night?"),
    (30, "someone said Hattie B's is mandatory https://hattieb.com/"),
    (35, "https://www.broadwaynashville.com honky tonk crawl obviously"),
    (30, "https://ryman.com/events check who's playing while we're there"),
    (40, "my sister said the Gulch is overrated btw"),
    (35, "https://www.airbnb.com/rooms/9982134 backup airbnb, cheaper, 12 south"),
    (45, "Convos what's playing at the Ryman Nov 12-15?"),
    (90, "Convos ok which airbnb should we grab? rooftop matters less than location"),
    # ── Money awkwardness (should NOT be pried into) ──
    (60, "fyi some of us are on a tighter budget this time"),
    (40, "let's keep dinners casual except one nice night"),
    # ── Booking & money ──
    (45, "we booked the east nashville airbnb!! conf #HMKTX8829"),
    (40, "flights booked too, Alaska 668 out on the 12th at 5:45pm, back Sunday 7am, conf #WBRR2L"),
    (45, "Convos add Husk for Saturday 7:30pm, reservation under Kelly"),
    (60, "Dre is officially IN btw, he got his flight, same one as us"),
    (40, "Convos who all is confirmed now?"),
    # ── Changes & drama ──
    (70, "ugh Jamie might bail, her dog sitter fell through"),
    (45, "ok she found one, she's back in"),
    (40, "wait the airbnb host just messaged, checkin is 4pm not 3pm"),
    (40, "Convos what time does everyone land again?"),
    (60, "my flight changed!! now landing 8:15pm not 7:40pm"),
    # ── Pre-trip ──
    (50, "Convos build out the day-by-day for the trip"),
    (120, "Convos send us the trip app"),
    (90, "what should we pack, is it cold in november?"),
    (40, "Convos is it cold in Nashville in November?"),
    (60, "https://www.pinewoodsocial.com bowling + brunch idea for Sunday?"),
    # ── Stop respect ──
    (45, "Convos stop"),
    (30, "https://www.thebluebirdcafe.com/ if we can get in lol"),
    (35, "that place is impossible, need to book like now"),
    (40, "Convos you there? can you try to get Bluebird tickets info?"),
    # ── Travel-day sim ──
    (60, "Convos what's the airbnb confirmation and address?"),
    (50, "Convos what's the door code? just kidding. what's our first thing Friday?"),
    (45, "this trip is going to be epic"),
    (30, "Convos thanks for organizing everything"),
]


def load_journey():
    """Prefer eval/active_journey.json (script-swappable runs)."""
    import json as _json
    path = os.path.expanduser("~/.hermes/eval/active_journey.json")
    if os.path.exists(path):
        data = _json.load(open(path))
        log(f"script: {data.get('name', path)}")
        return [(int(d), str(m)) for d, m in data["messages"]]
    return JOURNEY


def main():
    journey = load_journey()
    globals()['JOURNEY'] = journey
    log(f"=== journey start: {len(journey)} messages ===")
    try:
        chat = find_convos_chat()
    except Exception as e:  # noqa: BLE001
        import traceback
        log(f"CHAT LOOKUP FAILED: {e}\n{traceback.format_exc()[-400:]}")
        raise
    log(f"target chat: {chat}")
    sent = 0
    for delay, text in journey:
        time.sleep(delay)
        if send(chat, text):
            sent += 1
            log(f"[{sent}/{len(journey)}] {text[:70]}")
    log(f"=== journey complete: {sent}/{len(journey)} sent ===")


if __name__ == "__main__":
    main()
