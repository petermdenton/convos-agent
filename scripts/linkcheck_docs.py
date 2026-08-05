#!/usr/bin/env python3
"""linkcheck_docs.py — nightly link health check for every live plan option.

For each active trip: GET every non-cut option's URL (short timeout, real
User-Agent). 2xx/3xx → link_ok=1; 4xx/5xx or network failure → link_ok=0.
The doc renderer drops dead hyperlinks and marks them "link expired"
(Actives fall back to a Maps search link automatically).

Deterministic — no LLM. Ends by re-rendering any doc whose link state
changed. Designed to run as a no_agent cron job.
"""
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

DB = os.path.expanduser("~/.hermes/data/trips.db")
TRIP_DOC = os.path.expanduser("~/.hermes/scripts/trip_doc.py")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _alive(url):
    """Dead means DEAD: 404/410 or an unreachable host. Bot-walls (403/405/
    429, TLS oddities, timeouts) are inconclusive — a human's browser would
    likely succeed — so they count as alive. False 'link expired' flags on
    good Hilton/SeatGeek links are worse than a missed dead link."""
    import socket
    import urllib.error
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (404, 410)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, socket.gaierror):
            return False  # domain doesn't resolve — genuinely gone
        if isinstance(reason, ConnectionRefusedError):
            return False
        return True  # timeouts / TLS quirks: inconclusive → alive
    except Exception:  # noqa: BLE001 — anything else: inconclusive
        return True


def _network_up():
    """Control probe: if we can't reach a known-good endpoint, the CHECKER
    is offline — not the links. Never run a sweep in that state (the Aug 2
    incident: Mac offline at run time → every link flagged dead)."""
    for probe in ("https://www.google.com/generate_204",
                  "https://www.apple.com/library/test/success.html"):
        try:
            req = urllib.request.Request(probe, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def main():
    if not _network_up():
        print("network unreachable from this machine — skipping sweep "
              "(no links were flagged)")
        return
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan_options)")}
    if "link_ok" not in cols:
        conn.execute("ALTER TABLE plan_options ADD COLUMN link_ok INTEGER")
        conn.execute("ALTER TABLE plan_options ADD COLUMN link_checked_at TEXT")
        conn.commit()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT po.id, po.trip_id, po.label, po.url, po.link_ok
           FROM plan_options po JOIN trips t ON t.id = po.trip_id
           WHERE t.archived = 0 AND t.doc_id IS NOT NULL
           AND po.status != 'cut' AND po.url LIKE 'http%'""").fetchall()
    verdicts = [(r, 1 if _alive(r["url"]) else 0) for r in rows]
    n_dead = sum(1 for _, ok in verdicts if ok == 0)
    if len(verdicts) >= 5 and n_dead > len(verdicts) / 2:
        print(f"SUSPICIOUS: {n_dead}/{len(verdicts)} links 'dead' — that's a "
              "network/blocking problem on this machine, not the links. "
              "Nothing was flagged.")
        return
    changed_trips, checked, died, revived = set(), 0, 0, 0
    for r, ok in verdicts:
        checked += 1
        conn.execute("UPDATE plan_options SET link_ok = ?, link_checked_at = ? "
                     "WHERE id = ?", (ok, now, r["id"]))
        if r["link_ok"] is not None and ok != r["link_ok"]:
            changed_trips.add(r["trip_id"])
            if ok == 0:
                died += 1
                print(f"DEAD: [{r['trip_id']}] {r['label']} — {r['url'][:80]}")
            else:
                revived += 1
        elif r["link_ok"] is None and ok == 0:
            changed_trips.add(r["trip_id"])
            died += 1
            print(f"DEAD: [{r['trip_id']}] {r['label']} — {r['url'][:80]}")
    conn.commit()
    conn.close()
    for tid in sorted(changed_trips):
        subprocess.run([sys.executable, TRIP_DOC, "update", str(tid)],
                       capture_output=True, text=True, timeout=180)
    print(f"\n{checked} links checked · {died} dead · {revived} revived · "
          f"{len(changed_trips)} doc(s) re-rendered")


if __name__ == "__main__":
    main()
