#!/usr/bin/env python3
"""nightly_eval.py — the whole improvement loop, one night, one process.

reset -> generate journey -> drive it (~40 min) -> score -> file GitHub
issues -> apply ONE fix (self_build + gate self-test) -> restart gateway
if the fix touched live code. Runs detached via nightly_launch.py from a
2am cron job; everything logs to eval/nightly.log. The 8am digest cron
job reads the results and messages Pete."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
EV = os.path.join(HOME, "eval")
S = os.path.join(HOME, "scripts")
CHAT = "any;-;+12064273866"
PID = os.path.join(EV, "nightly.pid")
LOG = os.path.join(EV, "nightly.log")


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")


def run(args, timeout=300, **kw):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
    return r


def restart_gateway():
    for cand in ("hermes", os.path.expanduser("~/.local/bin/hermes"),
                 os.path.join(HOME, "hermes-agent/venv/bin/hermes")):
        path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if path:
            env = {k: v for k, v in os.environ.items() if k != "_HERMES_GATEWAY"}
            r = run([path, "gateway", "restart"], timeout=180, env=env)
            log(f"gateway restart: {(r.stdout or r.stderr).strip()[-100:]}")
            return


def reset():
    subprocess.run(["pkill", "-f", "journey_driver.py"], capture_output=True)
    conn = sqlite3.connect(os.path.join(HOME, "data", "trips.db"), timeout=15)
    tids = [r[0] for r in conn.execute(
        "SELECT id FROM trips WHERE chat_id = ? AND archived = 0", (CHAT,))]
    # dup-bug orphans: unbound trips from the last 48h are eval debris
    tids += [r[0] for r in conn.execute(
        "SELECT id FROM trips WHERE chat_id IS NULL AND archived = 0 "
        "AND created_at > datetime('now', '-2 days')")]
    conn.close()
    for tid in tids:
        run(["python3", os.path.join(S, "trip_tasks.py"), "delete-trip", str(tid)])
        log(f"reset: removed eval trip {tid}")
    st = sqlite3.connect(os.path.join(HOME, "state.db"), timeout=20)
    sids = [r[0] for r in st.execute("SELECT id FROM sessions WHERE chat_id = ?", (CHAT,))]
    for sid in sids:
        st.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
    st.execute("DELETE FROM sessions WHERE chat_id = ?", (CHAT,))
    st.commit(); st.close()
    log(f"reset: wiped {len(sids)} session(s)")
    restart_gateway()
    time.sleep(45)


def main():
    if os.path.exists(PID):
        try:
            os.kill(int(open(PID).read().strip()), 0)
            log("previous nightly still running — aborting this one")
            return
        except (OSError, ValueError):
            pass
    open(PID, "w").write(str(os.getpid()))
    summary = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    try:
        log("=== NIGHTLY LOOP START ===")
        reset()

        g = run(["python3", os.path.join(S, "journey_gen.py")], timeout=480)
        log(f"journey_gen: {g.stdout.strip()[:120]} {g.stderr.strip()[:120]}")
        since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")

        d = run(["python3", os.path.join(S, "journey_driver.py")], timeout=4500)
        log(f"driver done rc={d.returncode}")
        time.sleep(150)  # let the last turns finish

        sc = run(["python3", os.path.join(S, "score_run.py"), "--since", since],
                 timeout=900)
        log(f"score: {sc.stdout.strip()[:200]} {sc.stderr.strip()[:150]}")
        try:
            summary["scorecard"] = json.loads(sc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            summary["scorecard"] = None

        fi = run(["python3", os.path.join(S, "file_issues.py")], timeout=300)
        log(f"file_issues: {fi.stdout.strip()[:150]}")

        im = run(["python3", os.path.join(S, "improve.py")], timeout=1500)
        log(f"improve: {im.stdout.strip()[:250]}")
        try:
            summary["fix"] = json.loads(im.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            summary["fix"] = None
        if summary.get("fix") and summary["fix"].get("build_ok"):
            restart_gateway()  # load fixed hooks/plugins for tomorrow

        log("=== NIGHTLY LOOP DONE ===")
    except Exception as e:  # noqa: BLE001
        import traceback
        log(f"NIGHTLY FAILED: {e}\n{traceback.format_exc()[-400:]}")
        summary["error"] = str(e)[:200]
    finally:
        json.dump(summary, open(os.path.join(EV, "last_night.json"), "w"), indent=1)
        try:
            os.remove(PID)
        except OSError:
            pass


if __name__ == "__main__":
    main()
