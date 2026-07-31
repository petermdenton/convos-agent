#!/usr/bin/env python3
"""
trip_tasks.py — SQLite-backed tracker for hierarchical trip-planning checklists.

DB lives at ~/.hermes/data/trips.db (override with TRIP_TASKS_DB env var).

Tasks form a two-level tree: top-level items (sections like Flights, Lodging)
contain sub-items. A task with no parent is a section; pass --parent to nest.

Usage:
  trip_tasks.py scaffold "Japan 2026" [--destination "Tokyo"] [--start 2026-04-01] [--end 2026-04-10] [--group] [--skip "Ground Transport,Budget"]
  trip_tasks.py add-trip "Japan 2026" [--destination ...] [--start ...] [--end ...]
  trip_tasks.py list-trips [--all]                  # default hides archived
  trip_tasks.py archive-trip <trip_id>
  trip_tasks.py add-task <trip_id> "Book flights" [--parent <task_id>] [--note "..."]
  trip_tasks.py list-tasks <trip_id> [--all]        # flat; default hides done/skipped
  trip_tasks.py tree <trip_id> [--all]              # hierarchical view
  trip_tasks.py complete-task <task_id> [--note "JL57, conf ABC123, $840pp"]
  trip_tasks.py uncomplete-task <task_id>
  trip_tasks.py skip-task <task_id>                 # mark not-applicable
  trip_tasks.py note-task <task_id> "note text"     # set/replace the note
  trip_tasks.py delete-task <task_id>               # cascades to sub-items
  trip_tasks.py status <trip_id>                    # per-section progress + next actions

All commands print JSON to stdout.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB",
    os.path.expanduser("~/.hermes/data/trips.db"),
)

# ── Default scaffold tree ──────────────────────────────────────────────
# (section, [sub-items]) — pruned per-trip via --skip; --group adds Group.
SCAFFOLD = [
    ("Dates", [
        "Date window confirmed with all travelers",
        "Exact dates locked",
        "Time off / calendar blocked",
    ]),
    ("Flights", [
        "Search run (kiwi + skiplagged; award search if points in play)",
        "Option chosen — route, airline, cash vs points, per-person price",
        "Fare watch cron set on chosen route",
        "Booked — confirmation saved",
        "Seats picked / bags sorted",
    ]),
    ("Lodging", [
        "Area / neighborhood chosen",
        "Search run (trivago; vrbo / premium-hotels for groups or luxe)",
        "Option chosen — per-night and total price",
        "Booked — confirmation saved",
    ]),
    ("Excursions & Activities", [
        "Shortlist researched",
        "Must-books (sell-out risk) identified and booked",
        "Day-by-day itinerary drafted",
        "Restaurant reservations for locked-in nights",
    ]),
    ("Ground Transport", [
        "Airport <-> lodging plan, both ends",
        "Intercity legs (trains / ferries)",
        "Local transit vs rental decided; rental booked if needed",
    ]),
    ("Logistics", [
        "Passport validity checked (6-month rule where it applies)",
        "Visa / entry requirements checked for every traveler",
        "Travel insurance decided",
        "Phone / data plan for destination",
        "Packing list drafted (weather-checked near departure)",
    ]),
    ("Budget", [
        "Target per person set",
        "Running total vs target — update on every booking",
    ]),
]

GROUP_SECTION = ("Group", [
    "Everyone's dates / budget / airport collected (privately)",
    "Commitment confirmed per person",
    "Who has booked what — tracked",
    "Cost-split ledger current",
])


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            destination TEXT,
            start_date TEXT,
            end_date TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )
    # Migrations for DBs created by the flat v1 schema.
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(trips)")}
    if "chat_id" not in tcols:
        conn.execute("ALTER TABLE trips ADD COLUMN chat_id TEXT")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE")
    if "note" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN note TEXT")
    if "skipped" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0")
    if "due_date" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
    if "owner" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN owner TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS intake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            destination TEXT,
            start_date TEXT,
            nights INTEGER,
            party_size INTEGER,
            budget TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            trip_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
    conn.commit()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def fail(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def get_trip(conn, trip_id):
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {trip_id}")
    return trip


def insert_task(conn, trip_id, description, parent_id=None, note=None, due_date=None):
    cur = conn.execute(
        "INSERT INTO tasks (trip_id, description, created_at, parent_id, note, due_date) VALUES (?, ?, ?, ?, ?, ?)",
        (trip_id, description, now(), parent_id, note, due_date),
    )
    return cur.lastrowid


def compute_due(start_date, t_minus):
    """start_date 'YYYY-MM-DD' minus t_minus days → 'YYYY-MM-DD', or None."""
    if not start_date or t_minus is None:
        return None
    try:
        start = date.fromisoformat(str(start_date))
        return (start - timedelta(days=int(t_minus))).isoformat()
    except (ValueError, TypeError):
        return None


# ── Stack ranking: criticality × urgency ──────────────────────────────
# Section weights: how much a slipping item in this section matters.
SECTION_WEIGHTS = {
    "dates": 10, "documents": 10, "destination research": 9, "flights": 9,
    "group": 8, "lodging": 8, "final 72 hours": 8, "intercity transport": 7,
    "insurance": 6, "health": 6, "excursions & activities": 5, "money": 5,
    "ground transport": 4, "driving": 4, "connectivity & apps": 3,
    "packing": 2, "home logistics": 2,
}


def section_weight(title):
    return SECTION_WEIGHTS.get((title or "").strip().lower(), 5)


def urgency(due_date):
    """Monotonic urgency factor from the due date."""
    if not due_date:
        return 0.3
    try:
        d = date.fromisoformat(due_date)
    except ValueError:
        return 0.3
    delta = (d - date.today()).days
    if delta < 0:
        return 2.0 + min(-delta, 30) / 10.0   # overdue: 2.0 → 5.0
    if delta <= 3:
        return 1.8
    if delta <= 7:
        return 1.4
    if delta <= 14:
        return 1.0
    return 0.5


def rank_open_tasks(conn, trip_id=None, limit=10):
    """Score every open sub-task (criticality × urgency), descending."""
    if trip_id is not None:
        trips = conn.execute("SELECT * FROM trips WHERE id = ? AND archived = 0",
                             (trip_id,)).fetchall()
    else:
        trips = conn.execute("SELECT * FROM trips WHERE archived = 0").fetchall()
    ranked = []
    for trip in trips:
        skipped_sections = {r["id"] for r in conn.execute(
            "SELECT id FROM tasks WHERE trip_id = ? AND skipped = 1 AND parent_id IS NULL",
            (trip["id"],))}
        section_names = {r["id"]: r["description"] for r in conn.execute(
            "SELECT id, description FROM tasks WHERE trip_id = ? AND parent_id IS NULL",
            (trip["id"],))}
        rows = conn.execute(
            """SELECT * FROM tasks WHERE trip_id = ? AND parent_id IS NOT NULL
               AND done = 0 AND skipped = 0""", (trip["id"],)).fetchall()
        for t in rows:
            if t["parent_id"] in skipped_sections:
                continue
            section = section_names.get(t["parent_id"], "?")
            score = round(section_weight(section) * urgency(t["due_date"]), 1)
            entry = {
                "id": t["id"], "trip_id": trip["id"], "trip": trip["name"],
                "section": section, "description": t["description"],
                "due": t["due_date"], "owner": t["owner"], "score": score,
            }
            b = due_bucket(t["due_date"])
            if b in ("overdue", "due_soon"):
                entry["due_status"] = b
            if t["note"]:
                entry["note"] = t["note"]
            ranked.append(entry)
    ranked.sort(key=lambda e: e["score"], reverse=True)
    return ranked[:limit]


def due_bucket(due_date, soon_days=14):
    """'overdue' | 'due_soon' | 'scheduled' | None for a task's due date."""
    if not due_date:
        return None
    try:
        d = date.fromisoformat(due_date)
    except ValueError:
        return None
    today = date.today()
    if d < today:
        return "overdue"
    if d <= today + timedelta(days=soon_days):
        return "due_soon"
    return "scheduled"


def load_template(path, flags):
    """Load a JSON template and return [(section_title, [(desc, t_minus), ...])]
    filtered by `when` conditions against the active flag set."""
    with open(os.path.expanduser(path)) as f:
        data = json.load(f)
    out = []
    for section in data.get("sections", []):
        when = section.get("when", "always")
        if when != "always" and when not in flags:
            continue
        tasks = [(t["desc"], t.get("t_minus")) for t in section.get("tasks", [])]
        out.append((section["title"], tasks))
    return out


def task_state(t):
    if t["skipped"]:
        return "skipped"
    return "done" if t["done"] else "todo"


# ── Intake state machine (the four-legged stool) ───────────────────────
# The model extracts entities; THIS decides what's missing, what to ask
# next, and how the ledger reads. Tone lives here, not in the prompt.

DEFAULT_TEMPLATE = os.path.expanduser(
    "~/.hermes/skills/travel/trip-scaffold/templates/core.json")

_REL = re.compile(r"^\s*(\d+)\s*(day|week|month)s?\s*$", re.I)


def resolve_start(value):
    """'2026-08-17' passes through; '3 weeks' → today + 21 days."""
    if not value:
        return None, None
    v = value.strip().lower().removeprefix("in ").strip()
    m = _REL.match(v)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return (date.today() + timedelta(days=days)).isoformat(), None
    try:
        return date.fromisoformat(value.strip()).isoformat(), None
    except ValueError:
        return None, f"could not parse start {value!r} — pass YYYY-MM-DD or e.g. '3 weeks'"


def _fmt_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def intake_view(row):
    """Ledger text, missing legs, and the exact next question."""
    # Budget is deliberately NOT a leg: never ask for it. If someone volunteers
    # a budget it's stored and shown, but intake completes without it.
    missing = [leg for leg, col in (("where", "destination"), ("when", "start_date"),
                                    ("how_many", "party_size"))
               if not row[col]]
    ledger = []
    if row["destination"]:
        ledger.append(f"Location: {row['destination']}.")
    if row["start_date"]:
        nights = f" ({row['nights']} nights)" if row["nights"] else ""
        ledger.append(f"Dates: Leaving {_fmt_date(row['start_date'])}{nights}.")
    if row["party_size"]:
        ledger.append(f"Group: {row['party_size']}.")
    if row["budget"]:
        ledger.append(f"Budget: {row['budget']}.")

    if "where" in missing:
        q = "Where to?"
    elif "when" in missing and "how_many" in missing:
        q = "Do you know dates and how many people?"
    elif "when" in missing:
        q = "When are you leaving?"
    elif "how_many" in missing:
        q = "Know your group size?"
    elif not row["nights"]:
        q = "How many nights?"
    else:
        q = None

    complete = not missing
    view = {
        "intake_id": row["id"], "status": row["status"], "missing": missing,
        "complete": complete, "ledger": "\n".join(ledger), "next_question": q,
    }
    if complete:
        parts = [row["destination"], _fmt_date(row["start_date"])]
        if row["nights"]:
            parts[-1] += f" ({row['nights']} nights)"
        parts.append(f"{row['party_size']} people" if row["party_size"] > 1 else "solo")
        if row["budget"]:
            parts.append(row["budget"])
        view["locked_line"] = "Locked:\n" + " · ".join(parts) + ".\nPulling flights now."
    return view


def _intake_apply(conn, row_id, args):
    sets, vals, warnings = ["updated_at = ?"], [now()], []
    if getattr(args, "destination", None):
        sets.append("destination = ?"); vals.append(args.destination)
    if getattr(args, "start", None):
        iso, err = resolve_start(args.start)
        if err:
            warnings.append(err)
        else:
            sets.append("start_date = ?"); vals.append(iso)
    if getattr(args, "nights", None) is not None:
        sets.append("nights = ?"); vals.append(args.nights)
    if getattr(args, "party", None) is not None:
        sets.append("party_size = ?"); vals.append(args.party)
    if getattr(args, "budget", None):
        sets.append("budget = ?"); vals.append(args.budget)
    vals.append(row_id)
    conn.execute(f"UPDATE intake SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    return warnings


def _intake_row(conn, row_id):
    row = conn.execute("SELECT * FROM intake WHERE id = ?", (row_id,)).fetchone()
    if not row:
        fail(f"no intake with id {row_id}")
    return row


def cmd_intake_start(args):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO intake (phone, created_at, updated_at) VALUES (?, ?, ?)",
        (args.phone, now(), now()))
    warnings = _intake_apply(conn, cur.lastrowid, args)
    view = intake_view(_intake_row(conn, cur.lastrowid))
    if warnings:
        view["warnings"] = warnings
    print(json.dumps(view, indent=2, ensure_ascii=False))


def cmd_intake_update(args):
    conn = get_conn()
    _intake_row(conn, args.intake_id)
    warnings = _intake_apply(conn, args.intake_id, args)
    view = intake_view(_intake_row(conn, args.intake_id))
    if warnings:
        view["warnings"] = warnings
    print(json.dumps(view, indent=2, ensure_ascii=False))


def cmd_intake_status(args):
    conn = get_conn()
    print(json.dumps(intake_view(_intake_row(conn, args.intake_id)),
                     indent=2, ensure_ascii=False))


def cmd_intake_commit(args):
    conn = get_conn()
    row = _intake_row(conn, args.intake_id)
    view = intake_view(row)
    if args.partial:
        core = [leg for leg in view["missing"] if leg in ("where", "when")]
        if core:
            fail(f"intake {row['id']}: even a partial commit needs destination and "
                 f"start date — missing: {', '.join(core)}")
    elif not view["complete"]:
        fail(f"intake {row['id']} incomplete — missing: {', '.join(view['missing'])} "
             f"(or use --partial once destination + dates are known)")
    if row["status"] == "committed":
        fail(f"intake {row['id']} already committed as trip {row['trip_id']}")
    flags = {f.strip().lower().replace('-', '_')
             for f in (args.flags or "").split(",") if f.strip()}
    if (row["party_size"] or 1) > 1:
        flags.add("group")
    end = None
    if row["nights"]:
        end = (date.fromisoformat(row["start_date"])
               + timedelta(days=row["nights"])).isoformat()
    name = args.name or f"{row['destination']} {_fmt_date(row['start_date'])}"
    ns = argparse.Namespace(
        name=name, destination=row["destination"], start=row["start_date"], end=end,
        group=False, skip=args.skip, flags=",".join(flags),
        template=args.template if args.template else (
            DEFAULT_TEMPLATE if os.path.exists(DEFAULT_TEMPLATE) else None))
    import contextlib
    import io as _io
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_scaffold(ns)
    scaffold_out = json.loads(buf.getvalue())
    trip_id = scaffold_out["trip_id"]
    conn.execute("UPDATE intake SET status = 'committed', trip_id = ?, updated_at = ? WHERE id = ?",
                 (trip_id, now(), args.intake_id))
    conn.commit()
    locked = view.get("locked_line") or (
        f"Started: {row['destination']} · {_fmt_date(row['start_date'])}. "
        "Plan doc is live — filling in the rest as answers land.")
    print(json.dumps({
        "ok": True, "trip_id": trip_id, "trip_name": name, "flags": sorted(flags),
        "partial": bool(args.partial and view["missing"]),
        "still_missing": view["missing"],
        "locked_line": locked, "sections": len(scaffold_out["sections"]),
        "budget": row["budget"], "phone": row["phone"],
        "next": "create the Living Plan doc (trip_doc.py create), link the requester "
                "as owner, keep collecting any missing legs, then run the flight search",
    }, indent=2, ensure_ascii=False))


def cmd_add_section(args):
    """Graft one template section into an existing trip (e.g. Group, once the
    party size is learned after a partial commit)."""
    conn = get_conn()
    trip = get_trip(conn, args.trip_id)
    tpl = args.template or (DEFAULT_TEMPLATE if os.path.exists(DEFAULT_TEMPLATE) else None)
    if not tpl:
        fail("no template available")
    with open(os.path.expanduser(tpl)) as f:
        data = json.load(f)
    match = next((s for s in data.get("sections", [])
                  if s["title"].lower() == args.section.lower()), None)
    if not match:
        fail(f"no section {args.section!r} in template "
             f"({', '.join(s['title'] for s in data.get('sections', []))})")
    exists = conn.execute(
        "SELECT 1 FROM tasks WHERE trip_id = ? AND parent_id IS NULL AND lower(description) = ?",
        (args.trip_id, match["title"].lower())).fetchone()
    if exists:
        fail(f"trip {args.trip_id} already has a {match['title']!r} section")
    sec_id = insert_task(conn, args.trip_id, match["title"])
    subs = []
    for t in match.get("tasks", []):
        due = compute_due(trip["start_date"], t.get("t_minus"))
        tid = insert_task(conn, args.trip_id, t["desc"], parent_id=sec_id, due_date=due)
        subs.append({"id": tid, "description": t["desc"], **({"due": due} if due else {})})
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id, "section": match["title"],
                      "section_id": sec_id, "subtasks": subs}, indent=2, ensure_ascii=False))


# ── Commands ───────────────────────────────────────────────────────────

def cmd_add_trip(args):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO trips (name, destination, start_date, end_date, created_at, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
        (args.name, args.destination, args.start, args.end, now(), args.chat),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trips WHERE id = ?", (cur.lastrowid,)).fetchone()
    print(json.dumps(row_to_dict(row)))


def cmd_scaffold(args):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO trips (name, destination, start_date, end_date, created_at) VALUES (?, ?, ?, ?, ?)",
        (args.name, args.destination, args.start, args.end, now()),
    )
    trip_id = cur.lastrowid
    skip = {s.strip().lower() for s in (args.skip or "").split(",") if s.strip()}
    flags = {f.strip().lower().replace("-", "_") for f in (args.flags or "").split(",") if f.strip()}
    if args.group:
        flags.add("group")

    if args.template:
        try:
            tree = load_template(args.template, flags)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            fail(f"template error ({args.template}): {exc}")
    else:
        tree = [(s, [(d, None) for d in subs]) for s, subs in SCAFFOLD]
        if "group" in flags:
            tree.append((GROUP_SECTION[0], [(d, None) for d in GROUP_SECTION[1]]))

    created = []
    for section, subs in tree:
        if section.lower() in skip:
            continue
        sec_id = insert_task(conn, trip_id, section)
        sec = {"id": sec_id, "section": section, "subtasks": []}
        for desc, t_minus in subs:
            due = compute_due(args.start, t_minus)
            tid = insert_task(conn, trip_id, desc, parent_id=sec_id, due_date=due)
            entry = {"id": tid, "description": desc}
            if due:
                entry["due"] = due
            sec["subtasks"].append(entry)
        created.append(sec)
    conn.commit()
    print(json.dumps({"trip_id": trip_id, "name": args.name, "flags": sorted(flags),
                      "sections": created}, indent=2))


def cmd_list_trips(args):
    conn = get_conn()
    q = "SELECT * FROM trips ORDER BY id DESC" if args.all else "SELECT * FROM trips WHERE archived = 0 ORDER BY id DESC"
    params = ()
    if args.chat:
        q = "SELECT * FROM trips WHERE archived = 0 AND chat_id = ? ORDER BY id DESC"
        params = (args.chat,)
    out = []
    for r in conn.execute(q, params).fetchall():
        d = row_to_dict(r)
        d["tasks_total"] = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE trip_id = ? AND skipped = 0", (r["id"],)).fetchone()["c"]
        d["tasks_done"] = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE trip_id = ? AND done = 1 AND skipped = 0", (r["id"],)).fetchone()["c"]
        out.append(d)
    print(json.dumps(out, indent=2))


def cmd_archive_trip(args):
    conn = get_conn()
    conn.execute("UPDATE trips SET archived = 1 WHERE id = ?", (args.trip_id,))
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id, "archived": True}))


def cmd_set_trip(args):
    """Update trip fields in place (collector mode: destination/dates surface
    from chat after the trip container already exists)."""
    conn = get_conn()
    trip = get_trip(conn, args.trip_id)
    sets, vals, warnings = [], [], []
    if args.name:
        sets.append("name = ?"); vals.append(args.name)
    if args.chat:
        sets.append("chat_id = ?"); vals.append(args.chat)
    if args.destination:
        sets.append("destination = ?"); vals.append(args.destination)
    if args.start:
        iso, err = resolve_start(args.start)
        if err:
            warnings.append(err)
        else:
            sets.append("start_date = ?"); vals.append(iso)
    if args.end:
        sets.append("end_date = ?"); vals.append(args.end)
    if not sets:
        fail("nothing to set — pass --name/--destination/--start/--end")
    vals.append(args.trip_id)
    conn.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    trip = get_trip(conn, args.trip_id)
    out = {k: trip[k] for k in ("id", "name", "destination", "start_date", "end_date")}
    if warnings:
        out["warnings"] = warnings
    if trip["start_date"]:
        out["note"] = ("dates set — existing tasks keep their dues; graft sections "
                       "with add-section to get deadline-bearing tasks computed from the new start")
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_delete_trip(args):
    """Hard delete a trip and ALL its state — for restarting a botched plan.
    (archive-trip is for finished trips; this is the do-over button.)"""
    conn = get_conn()
    trip = get_trip(conn, args.trip_id)
    counts = {}
    counts["tasks"] = conn.execute(
        "DELETE FROM tasks WHERE trip_id = ?", (args.trip_id,)).rowcount
    for table in ("intake", "plan_options", "plan_itinerary", "plan_budget",
                  "plan_log", "trip_travelers"):
        try:
            counts[table] = conn.execute(
                f"DELETE FROM {table} WHERE trip_id = ?", (args.trip_id,)).rowcount
        except sqlite3.OperationalError:
            counts[table] = 0  # table not created yet
    doc_url = trip["doc_url"] if "doc_url" in trip.keys() else None
    conn.execute("DELETE FROM trips WHERE id = ?", (args.trip_id,))
    conn.commit()
    out = {"ok": True, "deleted_trip": args.trip_id, "name": trip["name"],
           "removed": counts}
    if doc_url:
        out["note"] = f"the Google Doc still exists ({doc_url}) — trash it in Drive if unwanted"
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_add_task(args):
    conn = get_conn()
    get_trip(conn, args.trip_id)
    if args.parent is not None:
        parent = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.parent,)).fetchone()
        if not parent:
            fail(f"no task with id {args.parent}")
        if parent["trip_id"] != args.trip_id:
            fail(f"task {args.parent} belongs to trip {parent['trip_id']}, not {args.trip_id}")
        if parent["parent_id"] is not None:
            fail(f"task {args.parent} is a sub-item; only two levels are supported")
    due = args.due
    if due is None and args.t_minus is not None:
        trip = conn.execute("SELECT start_date FROM trips WHERE id = ?", (args.trip_id,)).fetchone()
        due = compute_due(trip["start_date"], args.t_minus)
    task_id = insert_task(conn, args.trip_id, args.description, parent_id=args.parent,
                          note=args.note, due_date=due)
    if args.owner:
        conn.execute("UPDATE tasks SET owner = ? WHERE id = ?", (args.owner, task_id))
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    print(json.dumps(row_to_dict(row)))


def cmd_assign(args):
    _set_task(args, "UPDATE tasks SET owner = ? WHERE id = ?", (args.owner, args.task_id))


def cmd_rank(args):
    conn = get_conn()
    ranked = rank_open_tasks(conn, trip_id=args.trip, limit=args.limit)
    unowned = [e for e in ranked if not e["owner"]]
    print(json.dumps({
        "as_of": date.today().isoformat(),
        "ranked": ranked,
        "unowned_top": unowned[:3],
        "note": "DM each owner their top item; ask the group who's taking the "
                "unowned_top items, then record answers with `assign`.",
    }, indent=2, ensure_ascii=False))


def cmd_list_tasks(args):
    conn = get_conn()
    q = "SELECT * FROM tasks WHERE trip_id = ? ORDER BY id" if args.all else \
        "SELECT * FROM tasks WHERE trip_id = ? AND done = 0 AND skipped = 0 ORDER BY id"
    print(json.dumps([row_to_dict(r) for r in conn.execute(q, (args.trip_id,)).fetchall()], indent=2))


def cmd_tree(args):
    conn = get_conn()
    trip = get_trip(conn, args.trip_id)
    rows = conn.execute("SELECT * FROM tasks WHERE trip_id = ? ORDER BY id", (args.trip_id,)).fetchall()
    subs_by_parent = {}
    sections = []
    for r in rows:
        if r["parent_id"] is None:
            sections.append(r)
        else:
            subs_by_parent.setdefault(r["parent_id"], []).append(r)

    def render(t):
        d = {"id": t["id"], "description": t["description"], "state": task_state(t)}
        if t["note"]:
            d["note"] = t["note"]
        if t["due_date"]:
            d["due"] = t["due_date"]
            if not t["done"] and not t["skipped"]:
                bucket = due_bucket(t["due_date"])
                if bucket in ("overdue", "due_soon"):
                    d["due_status"] = bucket
        return d

    out_sections = []
    for s in sections:
        subs = subs_by_parent.get(s["id"], [])
        visible = subs if args.all else [t for t in subs if not t["done"] and not t["skipped"]]
        live = [t for t in subs if not t["skipped"]]
        sec = render(s)
        sec["done"] = sum(1 for t in live if t["done"])
        sec["total"] = len(live)
        sec["subtasks"] = [render(t) for t in visible]
        if args.all or task_state(s) != "skipped":
            out_sections.append(sec)
    print(json.dumps({"trip": row_to_dict(trip), "sections": out_sections}, indent=2))


def _set_task(args, sql, params):
    conn = get_conn()
    conn.execute(sql, params)
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task_id,)).fetchone()
    if row is None:
        fail(f"no task with id {args.task_id}")
    print(json.dumps(row_to_dict(row)))


def cmd_complete_task(args):
    if args.note is not None:
        _set_task(args, "UPDATE tasks SET done = 1, completed_at = ?, note = ? WHERE id = ?",
                  (now(), args.note, args.task_id))
    else:
        _set_task(args, "UPDATE tasks SET done = 1, completed_at = ? WHERE id = ?", (now(), args.task_id))


def cmd_uncomplete_task(args):
    _set_task(args, "UPDATE tasks SET done = 0, completed_at = NULL WHERE id = ?", (args.task_id,))


def cmd_skip_task(args):
    _set_task(args, "UPDATE tasks SET skipped = 1 WHERE id = ?", (args.task_id,))


def cmd_note_task(args):
    _set_task(args, "UPDATE tasks SET note = ? WHERE id = ?", (args.note, args.task_id))


def cmd_delete_task(args):
    conn = get_conn()
    conn.execute("DELETE FROM tasks WHERE parent_id = ?", (args.task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (args.task_id,))
    conn.commit()
    print(json.dumps({"ok": True, "task_id": args.task_id, "deleted": True}))


def cmd_status(args):
    conn = get_conn()
    trip = get_trip(conn, args.trip_id)
    skipped_sections = {r["id"] for r in conn.execute(
        "SELECT id FROM tasks WHERE trip_id = ? AND skipped = 1 AND parent_id IS NULL", (args.trip_id,))}
    rows = [r for r in conn.execute(
        "SELECT * FROM tasks WHERE trip_id = ? AND skipped = 0 ORDER BY id", (args.trip_id,)).fetchall()
        if r["parent_id"] not in skipped_sections]
    sections = [r for r in rows if r["parent_id"] is None]
    subs_by_parent = {}
    for r in rows:
        if r["parent_id"] is not None:
            subs_by_parent.setdefault(r["parent_id"], []).append(r)
    sec_summary = []
    next_actions = []
    for s in sections:
        subs = subs_by_parent.get(s["id"], [])
        done = sum(1 for t in subs if t["done"])
        sec_summary.append({"section": s["description"], "done": done, "total": len(subs)})
        nxt = next((t for t in subs if not t["done"]), None)
        if nxt:
            entry = {"id": nxt["id"], "section": s["description"], "description": nxt["description"]}
            if nxt["due_date"]:
                entry["due"] = nxt["due_date"]
                bucket = due_bucket(nxt["due_date"])
                if bucket in ("overdue", "due_soon"):
                    entry["due_status"] = bucket
            next_actions.append(entry)
    next_actions.sort(key=lambda e: e.get("due") or "9999-12-31")
    all_subs = [t for t in rows if t["parent_id"] is not None]
    open_subs = [t for t in all_subs if not t["done"]]
    print(json.dumps({
        "trip": row_to_dict(trip),
        "done": sum(1 for t in all_subs if t["done"]),
        "total": len(all_subs),
        "overdue": sum(1 for t in open_subs if due_bucket(t["due_date"]) == "overdue"),
        "due_soon": sum(1 for t in open_subs if due_bucket(t["due_date"]) == "due_soon"),
        "sections": sec_summary,
        "next_actions": next_actions,
    }, indent=2))


def cmd_due(args):
    """Cross-trip sweep for the gardener: overdue + upcoming tasks on active trips."""
    conn = get_conn()
    horizon = (date.today() + timedelta(days=args.days)).isoformat()
    today = date.today().isoformat()
    trips = conn.execute("SELECT * FROM trips WHERE archived = 0 ORDER BY id").fetchall()
    out = []
    for trip in trips:
        skipped_sections = {r["id"] for r in conn.execute(
            "SELECT id FROM tasks WHERE trip_id = ? AND skipped = 1 AND parent_id IS NULL", (trip["id"],))}
        rows = conn.execute(
            """SELECT * FROM tasks WHERE trip_id = ? AND skipped = 0 AND done = 0
               AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date""",
            (trip["id"], horizon)).fetchall()
        section_names = {r["id"]: r["description"] for r in conn.execute(
            "SELECT id, description FROM tasks WHERE trip_id = ? AND parent_id IS NULL", (trip["id"],))}
        items = []
        for t in rows:
            if t["parent_id"] in skipped_sections or t["parent_id"] is None:
                continue
            items.append({
                "id": t["id"],
                "section": section_names.get(t["parent_id"], "?"),
                "description": t["description"],
                "due": t["due_date"],
                "status": "overdue" if t["due_date"] < today else "due_soon",
                **({"note": t["note"]} if t["note"] else {}),
            })
        if items:
            out.append({"trip_id": trip["id"], "trip": trip["name"],
                        "start_date": trip["start_date"], "items": items})
    print(json.dumps({"as_of": today, "horizon_days": args.days, "trips": out}, indent=2))


def main():
    p = argparse.ArgumentParser(description="Hierarchical trip-planning task tracker (SQLite)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold")
    sp.add_argument("name")
    sp.add_argument("--destination", default=None)
    sp.add_argument("--start", default=None)
    sp.add_argument("--end", default=None)
    sp.add_argument("--group", action="store_true", help="include the Group coordination section")
    sp.add_argument("--skip", default=None, help="comma-separated section names to omit")
    sp.add_argument("--template", default=None, help="JSON template path (sections gated by `when` flags, t_minus due offsets)")
    sp.add_argument("--flags", default=None, help="comma-separated trip attributes: group,international,multi_base,driving")
    sp.set_defaults(func=cmd_scaffold)

    sp = sub.add_parser("add-trip")
    sp.add_argument("name")
    sp.add_argument("--destination", default=None)
    sp.add_argument("--start", default=None)
    sp.add_argument("--end", default=None)
    sp.add_argument("--chat", default=None, help="chat id this trip belongs to (e.g. any;+;<hash>)")
    sp.set_defaults(func=cmd_add_trip)

    sp = sub.add_parser("list-trips")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--chat", default=None, help="only trips bound to this chat id")
    sp.set_defaults(func=cmd_list_trips)

    sp = sub.add_parser("archive-trip")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_archive_trip)

    sp = sub.add_parser("set-trip")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--name", default=None)
    sp.add_argument("--destination", default=None)
    sp.add_argument("--start", default=None, help="YYYY-MM-DD or relative ('3 weeks')")
    sp.add_argument("--end", default=None)
    sp.add_argument("--chat", default=None, help="bind this trip to a chat id")
    sp.set_defaults(func=cmd_set_trip)

    sp = sub.add_parser("delete-trip")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_delete_trip)

    sp = sub.add_parser("add-task")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("description")
    sp.add_argument("--parent", type=int, default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--due", default=None, help="due date YYYY-MM-DD")
    sp.add_argument("--t-minus", dest="t_minus", type=int, default=None,
                    help="due date as days before the trip's start_date")
    sp.add_argument("--owner", default=None, help="who's responsible (name)")
    sp.set_defaults(func=cmd_add_task)

    sp = sub.add_parser("assign")
    sp.add_argument("task_id", type=int)
    sp.add_argument("owner")
    sp.set_defaults(func=cmd_assign)

    sp = sub.add_parser("rank")
    sp.add_argument("--trip", type=int, default=None, help="one trip; omit for all active")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_rank)

    sp = sub.add_parser("due")
    sp.add_argument("--days", type=int, default=14, help="horizon in days (default 14)")
    sp.set_defaults(func=cmd_due)

    def _intake_common(sp):
        sp.add_argument("--destination", default=None)
        sp.add_argument("--start", default=None, help="YYYY-MM-DD or relative ('3 weeks')")
        sp.add_argument("--nights", type=int, default=None)
        sp.add_argument("--party", type=int, default=None)
        sp.add_argument("--budget", default=None, help="'$1500pp', 'cheap', 'whatever it takes'")

    sp = sub.add_parser("intake-start")
    sp.add_argument("--phone", default=None)
    _intake_common(sp)
    sp.set_defaults(func=cmd_intake_start)

    sp = sub.add_parser("intake-update")
    sp.add_argument("intake_id", type=int)
    _intake_common(sp)
    sp.set_defaults(func=cmd_intake_update)

    sp = sub.add_parser("intake-status")
    sp.add_argument("intake_id", type=int)
    sp.set_defaults(func=cmd_intake_status)

    sp = sub.add_parser("intake-commit")
    sp.add_argument("intake_id", type=int)
    sp.add_argument("--name", default=None)
    sp.add_argument("--flags", default=None, help="extra flags: international,driving,multi_base (group is automatic)")
    sp.add_argument("--skip", default=None)
    sp.add_argument("--template", default=None)
    sp.add_argument("--partial", action="store_true",
                    help="commit with just destination + start date; remaining legs keep filling in")
    sp.set_defaults(func=cmd_intake_commit)

    sp = sub.add_parser("add-section")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("section", help="template section title, e.g. Group")
    sp.add_argument("--template", default=None)
    sp.set_defaults(func=cmd_add_section)

    sp = sub.add_parser("list-tasks")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_list_tasks)

    sp = sub.add_parser("tree")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_tree)

    sp = sub.add_parser("complete-task")
    sp.add_argument("task_id", type=int)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_complete_task)

    sp = sub.add_parser("uncomplete-task")
    sp.add_argument("task_id", type=int)
    sp.set_defaults(func=cmd_uncomplete_task)

    sp = sub.add_parser("skip-task")
    sp.add_argument("task_id", type=int)
    sp.set_defaults(func=cmd_skip_task)

    sp = sub.add_parser("note-task")
    sp.add_argument("task_id", type=int)
    sp.add_argument("note")
    sp.set_defaults(func=cmd_note_task)

    sp = sub.add_parser("delete-task")
    sp.add_argument("task_id", type=int)
    sp.set_defaults(func=cmd_delete_task)

    sp = sub.add_parser("status")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
