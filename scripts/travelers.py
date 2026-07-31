#!/usr/bin/env python3
"""
travelers.py — structured traveler profiles for the trip engine.

Shares the trips DB (~/.hermes/data/trips.db; TRIP_TASKS_DB overrides) so
rosters can join trips ↔ travelers. Soft/personality facts belong in Hermes
memory; this table holds the logistics the engine has to QUERY: "all six home
airports", "who hasn't confirmed", "whose passport is unchecked".

Phone numbers (E.164) are the natural key — iMessage hands them to Photon.

Usage:
  travelers.py upsert --phone +14155550123 [--name Sarah] [--airport SFO]
      [--loyalty "AS MVP Gold, Bonvoy"] [--seat aisle] [--dietary vegetarian]
      [--passport-until 2031-05-01] [--note "hates redeyes"]
  travelers.py get <phone>
  travelers.py list
  travelers.py forget <phone>                    # full delete (privacy request)
  travelers.py link <trip_id> <phone> [--role owner] [--committed]
  travelers.py unlink <trip_id> <phone>
  travelers.py set-commit <trip_id> <phone> yes|no
  travelers.py roster <trip_id>                  # who's on the trip + gaps

All commands print JSON. `upsert` only overwrites fields you pass; --note
appends (timestamped) rather than replacing.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB",
    os.path.expanduser("~/.hermes/data/trips.db"),
)

FIELDS = ["name", "home_airport", "loyalty", "seat_pref", "dietary",
          "passport_ok_until", "notes"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS travelers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            name TEXT,
            home_airport TEXT,
            loyalty TEXT,
            seat_pref TEXT,
            dietary TEXT,
            passport_ok_until TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trip_travelers (
            trip_id INTEGER NOT NULL,
            traveler_id INTEGER NOT NULL REFERENCES travelers(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'traveler',
            committed INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL,
            PRIMARY KEY (trip_id, traveler_id)
        );
        """
    )
    conn.commit()
    return conn


def fail(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def norm_phone(phone):
    p = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not p.startswith("+"):
        p = "+" + p
    return p


def find_traveler(conn, phone):
    return conn.execute(
        "SELECT * FROM travelers WHERE phone = ?", (norm_phone(phone),)).fetchone()


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def cmd_upsert(args):
    conn = get_conn()
    phone = norm_phone(args.phone)
    existing = find_traveler(conn, phone)
    if existing:
        sets, vals = ["updated_at = ?"], [now()]
        mapping = {
            "name": args.name, "home_airport": args.airport, "loyalty": args.loyalty,
            "seat_pref": args.seat, "dietary": args.dietary,
            "passport_ok_until": args.passport_until,
        }
        for col, val in mapping.items():
            if val is not None:
                sets.append(f"{col} = ?")
                vals.append(val)
        if args.note:
            stamped = f"[{now()[:10]}] {args.note}"
            merged = (existing["notes"] + "\n" + stamped) if existing["notes"] else stamped
            sets.append("notes = ?")
            vals.append(merged)
        vals.append(phone)
        conn.execute(f"UPDATE travelers SET {', '.join(sets)} WHERE phone = ?", vals)
    else:
        notes = f"[{now()[:10]}] {args.note}" if args.note else None
        conn.execute(
            """INSERT INTO travelers (phone, name, home_airport, loyalty, seat_pref,
               dietary, passport_ok_until, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (phone, args.name, args.airport, args.loyalty, args.seat, args.dietary,
             args.passport_until, notes, now(), now()))
    conn.commit()
    print(json.dumps(row_to_dict(find_traveler(conn, phone))))


def cmd_get(args):
    conn = get_conn()
    row = find_traveler(conn, args.phone)
    if not row:
        fail(f"no traveler with phone {norm_phone(args.phone)}")
    d = row_to_dict(row)
    d["trips"] = [dict(r) for r in conn.execute(
        """SELECT tt.trip_id, t.name AS trip_name, tt.role, tt.committed
           FROM trip_travelers tt JOIN trips t ON t.id = tt.trip_id
           WHERE tt.traveler_id = ? ORDER BY tt.trip_id DESC""", (row["id"],))]
    print(json.dumps(d, indent=2))


def cmd_list(args):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM travelers ORDER BY name, id").fetchall()
    print(json.dumps([row_to_dict(r) for r in rows], indent=2))


def cmd_forget(args):
    conn = get_conn()
    row = find_traveler(conn, args.phone)
    if not row:
        fail(f"no traveler with phone {norm_phone(args.phone)}")
    conn.execute("DELETE FROM travelers WHERE id = ?", (row["id"],))
    conn.commit()
    print(json.dumps({"ok": True, "forgot": norm_phone(args.phone)}))


def _require_trip(conn, trip_id):
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {trip_id}")
    return trip


def cmd_link(args):
    conn = get_conn()
    _require_trip(conn, args.trip_id)
    row = find_traveler(conn, args.phone)
    if not row:
        # auto-create a stub so linking never blocks on profile completeness
        conn.execute(
            "INSERT INTO travelers (phone, created_at, updated_at) VALUES (?, ?, ?)",
            (norm_phone(args.phone), now(), now()))
        row = find_traveler(conn, args.phone)
    conn.execute(
        """INSERT INTO trip_travelers (trip_id, traveler_id, role, committed, added_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(trip_id, traveler_id)
           DO UPDATE SET role = excluded.role""",
        (args.trip_id, row["id"], args.role, 1 if args.committed else 0, now()))
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id,
                      "traveler": row["phone"], "role": args.role}))


def cmd_unlink(args):
    conn = get_conn()
    row = find_traveler(conn, args.phone)
    if not row:
        fail(f"no traveler with phone {norm_phone(args.phone)}")
    conn.execute("DELETE FROM trip_travelers WHERE trip_id = ? AND traveler_id = ?",
                 (args.trip_id, row["id"]))
    conn.commit()
    print(json.dumps({"ok": True, "unlinked": row["phone"], "trip_id": args.trip_id}))


def cmd_set_commit(args):
    conn = get_conn()
    row = find_traveler(conn, args.phone)
    if not row:
        fail(f"no traveler with phone {norm_phone(args.phone)}")
    val = 1 if args.value.lower() in ("yes", "y", "true", "1") else 0
    cur = conn.execute(
        "UPDATE trip_travelers SET committed = ? WHERE trip_id = ? AND traveler_id = ?",
        (val, args.trip_id, row["id"]))
    if cur.rowcount == 0:
        fail(f"{row['phone']} is not linked to trip {args.trip_id}")
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id,
                      "traveler": row["phone"], "committed": bool(val)}))


def cmd_roster(args):
    conn = get_conn()
    trip = _require_trip(conn, args.trip_id)
    rows = conn.execute(
        """SELECT tv.*, tt.role, tt.committed FROM trip_travelers tt
           JOIN travelers tv ON tv.id = tt.traveler_id
           WHERE tt.trip_id = ? ORDER BY tt.role DESC, tv.name""",
        (args.trip_id,)).fetchall()
    roster, gaps = [], []
    for r in rows:
        d = row_to_dict(r)
        missing = [f for f in ("name", "home_airport") if not r[f]]
        if not r["passport_ok_until"]:
            missing.append("passport_ok_until")
        if missing:
            gaps.append({"phone": r["phone"], "name": r["name"], "missing": missing})
        roster.append(d)
    print(json.dumps({
        "trip": {"id": trip["id"], "name": trip["name"], "start_date": trip["start_date"]},
        "count": len(roster),
        "committed": sum(1 for r in rows if r["committed"]),
        "roster": roster,
        "profile_gaps": gaps,
    }, indent=2))


def main():
    p = argparse.ArgumentParser(description="Traveler profiles for the trip engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("upsert")
    sp.add_argument("--phone", required=True)
    sp.add_argument("--name", default=None)
    sp.add_argument("--airport", default=None, help="home airport IATA code")
    sp.add_argument("--loyalty", default=None)
    sp.add_argument("--seat", default=None)
    sp.add_argument("--dietary", default=None)
    sp.add_argument("--passport-until", dest="passport_until", default=None,
                    help="passport expiry YYYY-MM-DD (or 'unchecked')")
    sp.add_argument("--note", default=None, help="appended, timestamped")
    sp.set_defaults(func=cmd_upsert)

    sp = sub.add_parser("get")
    sp.add_argument("phone")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("list")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("forget")
    sp.add_argument("phone")
    sp.set_defaults(func=cmd_forget)

    sp = sub.add_parser("link")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("phone")
    sp.add_argument("--role", default="traveler", choices=["owner", "traveler"])
    sp.add_argument("--committed", action="store_true")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("unlink")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("phone")
    sp.set_defaults(func=cmd_unlink)

    sp = sub.add_parser("set-commit")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("phone")
    sp.add_argument("value")
    sp.set_defaults(func=cmd_set_commit)

    sp = sub.add_parser("roster")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_roster)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
