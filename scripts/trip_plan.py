#!/usr/bin/env python3
"""
trip_plan.py — plan CONTENT state: options, itinerary, budget, changelog.

trip_tasks.py tracks what must get done; THIS tracks what the plan actually
is — the flight options on the table, the stay shortlist, the day-by-day,
the budget. The Living Plan doc renders from here first.

Shares ~/.hermes/data/trips.db (TRIP_TASKS_DB overrides).

Usage:
  trip_plan.py option-add <trip_id> --kind flight|stay|activity|food --label "Aeroméxico" \
      [--details "SEA 7:05a → OAX 4:38p · 1 stop MEX"] [--price "$438"] \
      [--saved-by Pete] [--status option] [--note "..."]
      # status: option | shortlist | favorite | held | booked | cut
  trip_plan.py option-set <option_id> [--status held] [--price ...] [--note ...] [--details ...]
  trip_plan.py option-list <trip_id> [--kind flight]
  trip_plan.py iti-set <trip_id> <YYYY-MM-DD> <slot> "text" [--source Maya]
      # slot: morning | day | afternoon | evening | dinner | all  (re-set to overwrite)
  trip_plan.py iti-clear <itinerary_id>
  trip_plan.py iti-list <trip_id>
  trip_plan.py budget-set <trip_id> "Flights" --estimate 438 [--committed 0] [--note "live quote"]
  trip_plan.py budget-list <trip_id>
  trip_plan.py log <trip_id> "held Casa Tortuga 48h (free cancel Thu 6pm)"
  trip_plan.py log-list <trip_id> [--limit 10]

All commands print JSON.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB", os.path.expanduser("~/.hermes/data/trips.db"))

OPTION_STATUSES = ["option", "shortlist", "favorite", "held", "booked", "cut"]
SLOTS = ["morning", "day", "afternoon", "evening", "dinner", "all"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS plan_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            details TEXT,
            price TEXT,
            saved_by TEXT,
            status TEXT NOT NULL DEFAULT 'option',
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plan_itinerary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            slot TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (trip_id, day, slot)
        );
        CREATE TABLE IF NOT EXISTS plan_budget (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            line TEXT NOT NULL,
            estimate_pp REAL,
            committed_pp REAL NOT NULL DEFAULT 0,
            note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (trip_id, line)
        );
        CREATE TABLE IF NOT EXISTS plan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plan_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            section TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (trip_id, section)
        );
        CREATE TABLE IF NOT EXISTS plan_working (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            section TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (trip_id, section)
        );
        """
    )
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan_options)")}
    if "url" not in cols:
        conn.execute("ALTER TABLE plan_options ADD COLUMN url TEXT")
    if "phone" not in cols:
        conn.execute("ALTER TABLE plan_options ADD COLUMN phone TEXT")
    if "checkin" not in cols:
        conn.execute("ALTER TABLE plan_options ADD COLUMN checkin TEXT")
        conn.execute("ALTER TABLE plan_options ADD COLUMN checkout TEXT")
    if "related_to" not in cols:
        # transport (etc.) attached to another option — renders inline under it
        conn.execute("ALTER TABLE plan_options ADD COLUMN related_to INTEGER")
    if "link_ok" not in cols:
        # nightly link checker: 1 = alive, 0 = dead, NULL = never checked
        conn.execute("ALTER TABLE plan_options ADD COLUMN link_ok INTEGER")
        conn.execute("ALTER TABLE plan_options ADD COLUMN link_checked_at TEXT")
    conn.commit()
    return conn


def row_to_dict(r):
    return {k: r[k] for k in r.keys()}


def _log(conn, trip_id, message):
    conn.execute("INSERT INTO plan_log (trip_id, message, created_at) VALUES (?, ?, ?)",
                 (trip_id, message, now()))


def cmd_option_add(args):
    conn = get_conn()
    if args.status not in OPTION_STATUSES:
        fail(f"status must be one of {OPTION_STATUSES}")
    cur = conn.execute(
        """INSERT INTO plan_options (trip_id, kind, label, details, price, saved_by,
           status, note, url, phone, checkin, checkout, related_to, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (args.trip_id, args.kind, args.label, args.details, args.price,
         args.saved_by, args.status, args.note, args.url, args.phone,
         args.checkin, args.checkout, args.related_to, now(), now()))
    _log(conn, args.trip_id,
         f"filed {args.kind} option: {args.label}"
         + (f" ({args.price})" if args.price else "")
         + (f" via {args.saved_by}" if args.saved_by else ""))
    conn.commit()
    row = conn.execute("SELECT * FROM plan_options WHERE id = ?", (cur.lastrowid,)).fetchone()
    print(json.dumps(row_to_dict(row)))


def cmd_option_set(args):
    conn = get_conn()
    row = conn.execute("SELECT * FROM plan_options WHERE id = ?", (args.option_id,)).fetchone()
    if not row:
        fail(f"no option with id {args.option_id}")
    sets, vals = ["updated_at = ?"], [now()]
    for col in ("status", "price", "note", "details", "url", "phone", "checkin",
                "checkout", "label", "related_to"):
        val = getattr(args, col, None)
        if val is not None:
            if col == "status" and val not in OPTION_STATUSES:
                fail(f"status must be one of {OPTION_STATUSES}")
            sets.append(f"{col} = ?")
            vals.append(val)
    vals.append(args.option_id)
    conn.execute(f"UPDATE plan_options SET {', '.join(sets)} WHERE id = ?", vals)
    if args.status and args.status != row["status"]:
        _log(conn, row["trip_id"], f"{row['label']}: {row['status']} → {args.status}")
    conn.commit()
    row = conn.execute("SELECT * FROM plan_options WHERE id = ?", (args.option_id,)).fetchone()
    print(json.dumps(row_to_dict(row)))


def cmd_option_list(args):
    conn = get_conn()
    q = "SELECT * FROM plan_options WHERE trip_id = ?"
    params = [args.trip_id]
    if args.kind:
        q += " AND kind = ?"
        params.append(args.kind)
    rows = conn.execute(q + " ORDER BY kind, status, id", params).fetchall()
    print(json.dumps([row_to_dict(r) for r in rows], indent=2, ensure_ascii=False))


def cmd_iti_set(args):
    conn = get_conn()
    # slot may be a named slot (morning/dinner/...) OR a time ("2:05pm") —
    # itinerary entries render as a timeline, so any label is valid.
    conn.execute(
        """INSERT INTO plan_itinerary (trip_id, day, slot, text, source, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(trip_id, day, slot)
           DO UPDATE SET text = excluded.text, source = excluded.source,
                         updated_at = excluded.updated_at""",
        (args.trip_id, args.day, args.slot, args.text, args.source, now()))
    _log(conn, args.trip_id, f"itinerary: {args.day} {args.slot} → {args.text[:60]}")
    conn.commit()
    print(json.dumps({"ok": True, "day": args.day, "slot": args.slot, "text": args.text}))


def cmd_iti_clear(args):
    conn = get_conn()
    conn.execute("DELETE FROM plan_itinerary WHERE id = ?", (args.itinerary_id,))
    conn.commit()
    print(json.dumps({"ok": True, "deleted": args.itinerary_id}))


def cmd_iti_list(args):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM plan_itinerary WHERE trip_id = ? ORDER BY day, id",
        (args.trip_id,)).fetchall()
    print(json.dumps([row_to_dict(r) for r in rows], indent=2, ensure_ascii=False))


def cmd_budget_set(args):
    conn = get_conn()
    conn.execute(
        """INSERT INTO plan_budget (trip_id, line, estimate_pp, committed_pp, note, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(trip_id, line)
           DO UPDATE SET estimate_pp = COALESCE(excluded.estimate_pp, plan_budget.estimate_pp),
                         committed_pp = excluded.committed_pp,
                         note = COALESCE(excluded.note, plan_budget.note),
                         updated_at = excluded.updated_at""",
        (args.trip_id, args.line, args.estimate, args.committed or 0, args.note, now()))
    conn.commit()
    print(json.dumps({"ok": True, "line": args.line, "estimate_pp": args.estimate,
                      "committed_pp": args.committed or 0}))


def cmd_budget_list(args):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM plan_budget WHERE trip_id = ? ORDER BY id", (args.trip_id,)).fetchall()
    total_est = sum(r["estimate_pp"] or 0 for r in rows)
    total_com = sum(r["committed_pp"] or 0 for r in rows)
    print(json.dumps({"lines": [row_to_dict(r) for r in rows],
                      "total_estimate_pp": total_est,
                      "total_committed_pp": total_com}, indent=2, ensure_ascii=False))


def cmd_log(args):
    conn = get_conn()
    _log(conn, args.trip_id, args.message)
    conn.commit()
    print(json.dumps({"ok": True}))


VALID_SECTIONS = ("flights", "stay", "transport", "actives")


def cmd_summary_set(args):
    """One-line section summary shown under the section title in the doc.
    Written by Convos after filings; the renderer falls back to computed
    counts when unset. Format: "<what's in the pool> | <booking status>"."""
    conn = get_conn()
    conn.execute(
        """INSERT INTO plan_summaries (trip_id, section, text, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (trip_id, section) DO UPDATE SET text = excluded.text,
           updated_at = excluded.updated_at""",
        (args.trip_id, args.section, args.text, now()))
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id,
                      "section": args.section, "text": args.text}))


def cmd_working_set(args):
    """Live 'Convos is working on X' indicator shown in a section until
    cleared. Set it the moment you take on a request, clear it when the
    results are filed — the doc swaps the box for the data."""
    conn = get_conn()
    conn.execute(
        """INSERT INTO plan_working (trip_id, section, text, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (trip_id, section) DO UPDATE SET text = excluded.text,
           updated_at = excluded.updated_at""",
        (args.trip_id, args.section, args.text, now()))
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id,
                      "section": args.section, "text": args.text}))


def cmd_working_clear(args):
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM plan_working WHERE trip_id = ? AND section = ?",
        (args.trip_id, args.section))
    conn.commit()
    print(json.dumps({"ok": True, "cleared": cur.rowcount}))


def cmd_summary_list(args):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM plan_summaries WHERE trip_id = ? ORDER BY section",
        (args.trip_id,)).fetchall()
    print(json.dumps([row_to_dict(r) for r in rows], indent=2, ensure_ascii=False))


def cmd_log_list(args):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM plan_log WHERE trip_id = ? ORDER BY id DESC LIMIT ?",
        (args.trip_id, args.limit)).fetchall()
    print(json.dumps([row_to_dict(r) for r in rows], indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description="Trip plan content state")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("option-add")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--kind", required=True, choices=["flight", "stay", "activity", "food", "transport"])
    sp.add_argument("--label", required=True)
    sp.add_argument("--details", default=None)
    sp.add_argument("--price", default=None)
    sp.add_argument("--saved-by", dest="saved_by", default=None)
    sp.add_argument("--status", default="option")
    sp.add_argument("--note", default=None)
    sp.add_argument("--url", default=None, help="link to the original listing/booking page")
    sp.add_argument("--phone", default=None, help="phone number (hotels/restaurants)")
    sp.add_argument("--check-in", dest="checkin", default=None, help="stay check-in YYYY-MM-DD")
    sp.add_argument("--check-out", dest="checkout", default=None, help="stay check-out YYYY-MM-DD")
    sp.add_argument("--related-to", dest="related_to", type=int, default=None,
                    help="option id this one supports (e.g. transit to an activity) — renders inline under it")
    sp.set_defaults(func=cmd_option_add)

    sp = sub.add_parser("option-set")
    sp.add_argument("option_id", type=int)
    sp.add_argument("--status", default=None)
    sp.add_argument("--price", default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--details", default=None)
    sp.add_argument("--url", default=None)
    sp.add_argument("--phone", default=None)
    sp.add_argument("--check-in", dest="checkin", default=None)
    sp.add_argument("--check-out", dest="checkout", default=None)
    sp.add_argument("--label", default=None, help="rename the option")
    sp.add_argument("--related-to", dest="related_to", type=int, default=None)
    sp.set_defaults(func=cmd_option_set)

    sp = sub.add_parser("option-list")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--kind", default=None)
    sp.set_defaults(func=cmd_option_list)

    sp = sub.add_parser("iti-set")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("day")
    sp.add_argument("slot")
    sp.add_argument("text")
    sp.add_argument("--source", default=None)
    sp.set_defaults(func=cmd_iti_set)

    sp = sub.add_parser("iti-clear")
    sp.add_argument("itinerary_id", type=int)
    sp.set_defaults(func=cmd_iti_clear)

    sp = sub.add_parser("iti-list")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_iti_list)

    sp = sub.add_parser("budget-set")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("line")
    sp.add_argument("--estimate", type=float, default=None)
    sp.add_argument("--committed", type=float, default=None)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_budget_set)

    sp = sub.add_parser("budget-list")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_budget_list)

    sp = sub.add_parser("log")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("message")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("log-list")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_log_list)

    sp = sub.add_parser("summary-set")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("section", choices=list(VALID_SECTIONS))
    sp.add_argument("text")
    sp.set_defaults(func=cmd_summary_set)

    sp = sub.add_parser("summary-list")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_summary_list)

    sp = sub.add_parser("working-set")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("section", choices=list(VALID_SECTIONS))
    sp.add_argument("text")
    sp.set_defaults(func=cmd_working_set)

    sp = sub.add_parser("working-clear")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("section", choices=list(VALID_SECTIONS))
    sp.set_defaults(func=cmd_working_clear)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
