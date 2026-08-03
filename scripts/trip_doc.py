#!/usr/bin/env python3
"""
trip_doc.py — render a trip's "Living Plan" Google Doc from trip state.

The doc is a VIEW: trips.db is the source of truth, this renders it. Humans
comment on the doc; the agent edits state and re-renders. Never hand-edit the
doc — changes belong in the tracker, then `update`.

Requires the google-workspace skill authorized once
(~/.hermes/skills/travel/google-workspace/scripts/setup.py — creates the
OAuth token). Uses that skill's client under the hood.

Usage:
  trip_doc.py render <trip_id> [--out /path.html]   # HTML only, no upload (debug)
  trip_doc.py create <trip_id> [--share commenter|reader|off]
      Creates the Google Doc (HTML → Docs conversion), link-shares it
      (default: anyone with link can COMMENT), stores doc id/url on the trip.
  trip_doc.py update <trip_id>       # re-render state into the existing doc
  trip_doc.py link <trip_id>         # print the stored doc URL

All commands print JSON.
"""
import argparse
import html
import json
import os
import sqlite3
import sys

# Py3.9 guard: google_api.py needs 3.10+; re-exec under the hermes venv
# python when invoked with an older interpreter (e.g. bare `python3`).
_VENV_PY = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python3.11")
if (sys.version_info < (3, 10) and os.path.exists(_VENV_PY)
        and not os.environ.get("HERMES_NO_REEXEC")):
    os.environ["HERMES_NO_REEXEC"] = "1"
    os.execv(_VENV_PY, [_VENV_PY] + sys.argv)
from datetime import date, datetime, timezone

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB", os.path.expanduser("~/.hermes/data/trips.db"))
GWS_SCRIPTS = os.path.expanduser("~/.hermes/skills/travel/google-workspace/scripts")

STATUS_WORDS = {  # section completion → status chip
    (True, True): "DONE", (True, False): "IN MOTION", (False, False): "OPEN",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(trips)")}
    if "doc_id" not in cols:
        conn.execute("ALTER TABLE trips ADD COLUMN doc_id TEXT")
        conn.execute("ALTER TABLE trips ADD COLUMN doc_url TEXT")
        conn.commit()
    return conn


def esc(v):
    return html.escape(str(v)) if v is not None else ""


def fmt_date(iso):
    if not iso:
        return None
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def _q(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def load_state(conn, trip_id):
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {trip_id}")
    roster = _q(conn,
                """SELECT tv.name, tv.phone, tv.home_airport, tt.committed
                   FROM trip_travelers tt
                   JOIN travelers tv ON tv.id = tt.traveler_id WHERE tt.trip_id = ?
                   ORDER BY tt.role DESC, tv.name""", (trip_id,))
    intake = conn.execute(
        "SELECT * FROM intake WHERE trip_id = ? ORDER BY id DESC LIMIT 1",
        (trip_id,)).fetchone()
    options = _q(conn, "SELECT * FROM plan_options WHERE trip_id = ? ORDER BY id",
                 (trip_id,))
    itinerary = _q(conn, "SELECT * FROM plan_itinerary WHERE trip_id = ? ORDER BY day, id",
                   (trip_id,))
    budget = _q(conn, "SELECT * FROM plan_budget WHERE trip_id = ? ORDER BY id", (trip_id,))
    log = _q(conn, "SELECT * FROM plan_log WHERE trip_id = ? ORDER BY id DESC LIMIT 8",
             (trip_id,))
    return trip, roster, intake, options, itinerary, budget, log


def load_summaries(conn, trip_id):
    """Per-section summary lines authored by Convos ({} on old-schema DBs)."""
    try:
        return {r["section"]: r["text"] for r in _q(
            conn, "SELECT section, text FROM plan_summaries WHERE trip_id = ?",
            (trip_id,))}
    except sqlite3.OperationalError:
        return {}


def load_working(conn, trip_id):
    """Per-section in-flight status ('Convos is researching…'); {} if none."""
    try:
        return {r["section"]: r["text"] for r in _q(
            conn, "SELECT section, text FROM plan_working WHERE trip_id = ?",
            (trip_id,))}
    except sqlite3.OperationalError:
        return {}


def _working_box(text):
    """Typing-indicator-style box: bordered, gray, trailing ellipsis."""
    t = text.strip()
    if not t.endswith(("…", "...")):
        t += "…"
    return ("<table style='border-collapse:collapse;width:100%'><tr>"
            "<td style='border:1px solid #ccc;padding:10px 12px;color:#9aa0a6'>"
            f"{esc(t)}</td></tr></table>")


STATUS_STYLE = "font-weight:bold"


def _initials(name):
    parts = (name or "").split()
    return "".join(p[0].upper() for p in parts[:2]) if parts else "?"


def _col(row, name):
    """Column value or None when the DB predates the column (no crash)."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _linked(label, url):
    """Option label, hyperlinked to the original listing when we have it."""
    if url:
        return f'<a href="{esc(url)}">{label}</a>'
    return label


BOOKED_STYLE = 'color:#137333;font-weight:bold'
SPACER = "<p style='margin:0'>&nbsp;</p>"


def _collapse_booked(kind_opts):
    """Once something in a category is booked, the alternatives disappear."""
    booked = [o for o in kind_opts if o["status"] == "booked"]
    return booked if booked else kind_opts


def _stay_nights(o):
    """Set of ISO night-dates a dated stay covers (empty if undated)."""
    ci, co = _col(o, "checkin"), _col(o, "checkout")
    if not ci or not co:
        return set()
    try:
        d, end = date.fromisoformat(ci), date.fromisoformat(co)
    except ValueError:
        return set()
    out = set()
    while d < end:
        out.add(d.isoformat())
        from datetime import timedelta as _td
        d += _td(days=1)
    return out


def _collapse_stays(stays):
    """Night-aware collapse: a non-booked stay disappears only when every
    night it covers is already covered by booked stays. Options covering
    still-open nights survive (e.g. Sun night open → Sat-Sun hotels stay
    visible even though Sat is covered by a friend's house)."""
    booked = [o for o in stays if o["status"] == "booked"]
    if not booked:
        return stays
    covered = set().union(*[_stay_nights(b) for b in booked]) if booked else set()
    out = list(booked)
    for o in stays:
        if o["status"] == "booked":
            continue
        nights = _stay_nights(o)
        if nights and not (nights <= covered):
            out.append(o)  # covers at least one open night — keep it
        # undated options collapse once anything is booked (old behavior)
    return out


def _green(text="BOOKED"):
    return f'<span style="color:#137333;font-weight:bold">{text}</span>'


def _status_cell(status_text, booked=False):
    if booked:
        return _green()
    return status_text


def _fmt_status_word(word):
    """Status word for the at-a-glance table — BOOKED always dark green."""
    return _green() if word == "BOOKED" else f"<b>{word}</b>"


def _mode_emoji(opt):
    """Transport mode emoji from label/details keywords."""
    blob = " ".join(filter(None, [opt["label"], opt["details"] or ""])).lower()
    for words, emoji in ((("train", "rail", "light rail", "metro", "subway"), "🚆"),
                         (("bus", "shuttle"), "🚌"),
                         (("ferry", "boat"), "⛴️"),
                         (("taxi", "uber", "lyft", "cab"), "🚕"),
                         (("walk",), "🚶"),
                         (("car", "drive", "rental"), "🚗")):
        if any(w in blob for w in words):
            return emoji
    return "🚏"


def _quote_age(o):
    """Days since this option was last touched (price refreshes bump updated_at)."""
    try:
        ts = (o["updated_at"] or o["created_at"] or "")[:10]
        return (date.today() - date.fromisoformat(ts)).days
    except (ValueError, KeyError, IndexError, TypeError):
        return 0


def _price_cell(o):
    """Price + quote age. Fresh (<1 day) prices stand alone; older ones show
    when they were quoted; 3+ days old get an amber re-check flag. Booked
    prices are settled and never flagged."""
    price = esc(o["price"] or "—")
    if not o["price"] or o["status"] == "booked":
        return f"<b>{price}</b>"
    age = _quote_age(o)
    if age < 1:
        return f"<b>{price}</b>"
    when = esc((o["updated_at"] or "")[:10])
    if age >= 3:
        tag = (f"<span style='color:#b06000;font-weight:normal'> · quoted {when}"
               f" — recheck before booking</span>")
    else:
        tag = f"<span style='color:#5f6368;font-weight:normal'> · quoted {when}</span>"
    return f"<b>{price}</b>{tag}"


def _live_link(label, o, fallback=None):
    """Hyperlink an option label, respecting the nightly link check:
    a URL flagged dead is not linked (fallback used when provided) and the
    label carries a small 'link expired' note."""
    url = o["url"]
    dead = url and _col(o, "link_ok") == 0
    if url and not dead:
        return f'<a href="{esc(url)}">{label}</a>'
    if fallback:
        label = f'<a href="{esc(fallback)}">{label}</a>'
    if dead:
        label += ("<span style='color:#b06000;font-weight:normal'>"
                  " · link expired</span>")
    return label


def _tel(phone):
    """Phone as a tap-to-call link (tel:), display text unchanged."""
    if not phone:
        return "—"
    import re as _re
    digits = _re.sub(r"[^\d+]", "", phone)
    return f'<a href="tel:{digits}">{esc(phone)}</a>'


def _pl(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def _section_sub(text):
    """Summary line rendered directly under a section title."""
    return f"<p style='color:#5f6368'>{text}</p>"


def _booked_bit(opts):
    """'BOOKED — <labels>' (green) or 'Nothing booked yet'."""
    booked = [o for o in opts if o["status"] == "booked"]
    if booked:
        return f"{_green()} — " + ", ".join(esc(b["label"]) for b in booked)
    return "Nothing booked yet"


def _stay_mix(opts):
    """'3 hotels, 1 Airbnb' from the suggestion pool."""
    rentals = hotels = 0
    for o in opts:
        blob = " ".join(filter(None, [o["label"], o["details"] or "",
                                      o["url"] or ""])).lower()
        rentals += 1 if ("airbnb" in blob or "vrbo" in blob) else 0
        hotels += 0 if ("airbnb" in blob or "vrbo" in blob) else 1
    parts = []
    if hotels:
        parts.append(_pl(hotels, "hotel"))
    if rentals:
        parts.append(f"{rentals} Airbnb" + ("s" if rentals != 1 else ""))
    return ", ".join(parts) or "0 places"


def _fetch_comment_threads(doc_id):
    """Live comment threads for the doc, oldest first. Returns [] on any
    failure — comments are best-effort garnish, never a render blocker."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from doc_comments import _drive, _threads  # noqa: PLC0415
        threads = [c for c in _threads(_drive(), doc_id) if not c.get("deleted")]
        threads.sort(key=lambda c: c.get("createdTime") or c.get("modifiedTime") or "")
        return threads
    except Exception:  # noqa: BLE001
        return []


def _strip_marker(text):
    t = (text or "").strip()
    return t[len("CONVOS:"):].strip() if t.upper().startswith("CONVOS:") else t


def render_html(conn, trip_id, comments=None):
    trip, roster, intake, options, itinerary, budget, log = load_state(conn, trip_id)
    summaries = load_summaries(conn, trip_id)
    working = load_working(conn, trip_id)
    party = max(len(roster), (intake["party_size"] or 0) if intake else 0) or None
    start, end = trip["start_date"], trip["end_date"]
    dates = fmt_date(start) or "dates TBD"
    if end:
        dates += f"–{fmt_date(end)}"
    # Title is consumer-facing: no internal "Living Plan" branding.
    title = f"{trip['destination'] or trip['name']} · {dates}" + (
        f" · {party} people" if party else "")

    flights = _collapse_booked(
        [o for o in options if o["kind"] == "flight" and o["status"] != "cut"])
    stays = [o for o in options if o["kind"] == "stay"]
    stays_live = _collapse_stays([o for o in stays if o["status"] != "cut"])
    transport = _collapse_booked(
        [o for o in options if o["kind"] == "transport" and o["status"] != "cut"
         and not _col(o, "related_to")])  # related transport renders under its parent
    pool = [o for o in options if o["kind"] in ("activity", "food")
            and o["status"] in ("option", "shortlist", "favorite")]
    placed = len(itinerary)

    def row(cells, header=False):
        tag = "th" if header else "td"
        return "<tr>" + "".join(
            f'<{tag} style="border:1px solid #ccc;padding:6px 10px;'
            + ("font-weight:bold;" if header else "") + f'">{c}</{tag}>'
            for c in cells) + "</tr>"

    def table(rows):
        return "<table style='border-collapse:collapse'>" + "".join(rows) + "</table>"

    def status_word(kind_opts, booked_word="BOOKED"):
        sts = {o["status"] for o in kind_opts}
        if "booked" in sts:
            return booked_word
        if "held" in sts:
            return "HELD"
        if "favorite" in sts:
            return "WATCHING"
        if "shortlist" in sts:
            return "VOTE OPEN"
        if kind_opts:
            return "OPTIONS IN"
        return "OPEN"

    def leader_price(kind_opts):
        import re as _re
        best = None
        for o in kind_opts:
            m = _re.search(r"[\d,]+(?:\.\d+)?", o["price"] or "")
            if m:
                v = float(m.group(0).replace(",", ""))
                best = min(best, v) if best is not None else v
        return f"${best:,.0f}" if best is not None else None

    h = [f"<html><head><meta charset='utf-8'><title>{esc(title)}</title></head><body>"]
    h.append(f"<h1><b>{esc(title)}</b></h1>")
    origin = roster[0]["home_airport"] if roster and roster[0]["home_airport"] else None
    subtitle = (f"{esc(origin)} → {esc(trip['destination'] or '')}" if origin
                else esc(trip["destination"] or ""))
    if start:
        subtitle += " · dates " + ("locked" if intake and intake["status"] == "committed"
                                   else "penciled")
    h.append(f"<p>{subtitle}</p>")
    h.append("<p><b>✈️ Maintained by Convos</b> — rendered from trip state · humans "
             "comment, Convos edits · updated "
             f"{esc(datetime.now(timezone.utc).strftime('%b %d, %H:%M UTC'))}</p>")
    h.append(SPACER)
    box = ("Send ideas in the group chat — or comment right here; "
           "either way it lands in the plan.")
    if _col(trip, "pwa_url"):
        box += (f'<br>Ready for travel? <a href="{esc(trip["pwa_url"])}">'
                f"Download your personalized trip app here.</a>")
    h.append(table([row([box])]))

    # ── Status at a glance ────────────────────────────────────────────
    h.append(SPACER)
    h.append("<h2><b>Status at a glance</b></h2>")
    rows = [row(["Section", "Where it stands", "Status"], header=True)]
    if start:
        who = f" · confirmed by all {party}" if roster and all(
            r["committed"] for r in roster) and party and len(roster) >= party else ""
        rows.append(row([f"<b>Dates</b>", f"{dates}{who}",
                         "<b>LOCKED</b>" if intake and intake["status"] == "committed"
                         else "<b>PENCILED</b>"]))
    else:
        rows.append(row(["<b>Dates</b>", "not set", "<b>OPEN</b>"]))
    lp = leader_price(flights)
    fl_stand = (f"{len(flights)} options bookmarked"
                + (f" · leader {lp} pp" if lp else "")
                + " · fares watched daily" if flights else "no options yet — searching")
    rows.append(row(["<b>Flights</b>", fl_stand, _fmt_status_word(status_word(flights))]))
    st_short = [o for o in stays_live if o["status"] in ("shortlist", "favorite", "held", "booked")]
    # Per-night coverage: which nights of the trip have a booked stay?
    night_lines, open_nights = [], 0
    if start and end:
        from datetime import timedelta as _td
        booked_stays = [o for o in stays if o["status"] == "booked"]
        night = date.fromisoformat(start)
        last = date.fromisoformat(end)
        while night < last:
            iso = night.isoformat()
            covered = any(
                _col(o, "checkin") and _col(o, "checkout")
                and _col(o, "checkin") <= iso < _col(o, "checkout")
                for o in booked_stays)
            if not covered and len(booked_stays) == 1 and not _col(booked_stays[0], "checkin"):
                covered = True  # single dateless booking — assume it covers the trip
            if covered:
                night_lines.append(f"{night.strftime('%a')} – {_green()}")
            else:
                night_lines.append(f"{night.strftime('%a')} – <b>Need stay</b>")
                open_nights += 1
            night += _td(days=1)
    if night_lines:
        st_stand = "<br>".join(night_lines)
        st_status = (_green() if open_nights == 0 else
                     f"<b>{open_nights} NIGHT{'S' if open_nights != 1 else ''} OPEN</b>")
    else:
        st_stand = (f"{len(stays)} ideas in → {len(st_short)} shortlisted" if stays
                    else "no ideas yet")
        st_status = _fmt_status_word(status_word(stays_live))
    rows.append(row(["<b>Stay</b>", st_stand, st_status]))
    acts_all = [o for o in options if o["kind"] in ("activity", "food")
                and o["status"] != "cut"]
    acts_booked = [o for o in acts_all if o["status"] == "booked"]
    ac_stand = (f"{len(acts_booked)} booked · {len(acts_all) - len(acts_booked)} ideas"
                if acts_all else "no ideas yet")
    ac_status = (_green() if acts_booked
                 else ("<b>IDEAS IN</b>" if acts_all else "<b>OPEN</b>"))
    rows.append(row(["<b>Activities</b>", ac_stand, ac_status]))
    it_stand = (f"Days drafted · {placed} ideas placed, {len(pool)} in the pool"
                if placed else f"{len(pool)} ideas in the pool · days not drafted yet")
    rows.append(row(["<b>Itinerary</b>", it_stand,
                     "<b>DRAFTED</b>" if placed else "<b>OPEN</b>"]))
    tr_booked = any(o["status"] == "booked" for o in transport)
    tr_stand = (f"{len(transport)} option{'s' if len(transport) != 1 else ''} in"
                if transport else "not sorted yet")
    rows.append(row(["<b>Transportation</b>", tr_stand,
                     _fmt_status_word(status_word(transport))]))
    if roster:
        names = ", ".join(
            _initials(r["name"]) if r["name"]
            else "…" + (_col(r, "phone") or "????")[-4:]  # unnamed: last 4 of number
            for r in roster)
        n_in = sum(1 for r in roster if r["committed"])
        rows.append(row(["<b>Travelers</b>", names,
                         "<b>LOCKED</b>" if party and n_in >= party
                         else f"<b>{n_in} OF {party or len(roster)} IN</b>"]))
    h.append(table(rows))

    # ── Flights ───────────────────────────────────────────────────────
    h.append(SPACER)
    h.append("<h2><b>✈️ Flights</b></h2>")
    if flights:
        fl_all = [o for o in options if o["kind"] == "flight" and o["status"] != "cut"]
        h.append(_section_sub(esc(summaries["flights"]) if summaries.get("flights")
                 else f"{_pl(len(fl_all), 'option')} saved | {_booked_bit(fl_all)}"))
        rows = [row(["Option", "Route", "Price", "Saved by", "Status"], header=True)]
        for o in flights:
            st = {"favorite": "GROUP FAVORITE", "held": "HELD", "booked": "BOOKED",
                  "shortlist": "shortlist", "option": "option"}[o["status"]]
            st_cell = _status_cell(f"<b>{st}</b>" if st != "option" else st,
                                   o["status"] == "booked")
            rows.append(row([f"<b>{_live_link(esc(o['label']), o)}</b>",
                             esc(o["details"] or ""),
                             _price_cell(o),
                             esc(o["saved_by"] or "—"), st_cell]))
        h.append(table(rows))
        fl_note = next((o["note"] for o in flights if o["note"]), None)
        if fl_note:
            h.append(f"<p><i>Convos: {esc(fl_note)}</i></p>")
    else:
        h.append("<p><i>Nothing bookmarked yet — first search results land here.</i></p>")
    if working.get("flights"):
        h.append(_working_box(working["flights"]))

    # ── Stay ──────────────────────────────────────────────────────────
    h.append(SPACER)
    h.append("<h2><b>🏨 Stay</b></h2>")
    if stays_live:
        stay_all = [o for o in stays if o["status"] != "cut"]
        h.append(_section_sub(esc(summaries["stay"]) if summaries.get("stay")
                 else f"{_stay_mix(stay_all)} suggested | {_booked_bit(stay_all)}"))
        show_phone = any(_col(o, "phone") for o in stays_live)
        hdr = ["Property", "Per night", "From", "Status"]
        if show_phone:
            hdr.insert(2, "Phone")
        rows = [row(hdr, header=True)]
        for o in stays_live:
            label = f"<b>{_live_link(esc(o['label']), o)}</b>"
            if o["details"]:
                label += f" — {esc(o['details'])}"
            st_map = {"held": "HELD", "booked": "BOOKED", "shortlist": "SHORTLIST",
                      "favorite": "FAVORITE", "option": "idea", "cut": "CUT"}
            st = st_map[o["status"]]
            if o["status"] == "held" and o["note"]:
                st += f" ({esc(o['note'])})"
            st_cell = _status_cell(f"<b>{st}</b>" if o["status"] != "option" else st,
                                   o["status"] == "booked")
            cells = [label, _price_cell(o),
                     esc(o["saved_by"] or "—"), st_cell]
            if show_phone:
                cells.insert(2, _tel(_col(o, "phone")))
            rows.append(row(cells))
        h.append(table(rows))
    else:
        h.append("<p><i>Send stay ideas in the chat — links, screenshots, anything.</i></p>")
    if working.get("stay"):
        h.append(_working_box(working["stay"]))

    # ── Actives — every activity & food item, status each, booked on top ─
    h.append(SPACER)
    h.append("<h2><b>🎯 Actives</b></h2>")
    actives = [o for o in options if o["kind"] in ("activity", "food")
               and o["status"] != "cut"]
    _order = {"booked": 0, "held": 1, "favorite": 2, "shortlist": 3, "option": 4}
    actives.sort(key=lambda o: (_order.get(o["status"], 5), o["id"]))
    if actives:
        n_booked = sum(1 for o in actives if o["status"] == "booked")
        n_ideas = len(actives) - n_booked
        h.append(_section_sub(esc(summaries["actives"]) if summaries.get("actives")
                 else f"{_pl(n_ideas, 'idea')} | {_booked_bit(actives)}"))
        rows = [row(["Item", "Type", "From", "Status"], header=True)]
        from urllib.parse import quote as _quote  # noqa: PLC0415
        dest = trip["destination"] or trip["name"] or ""
        for o in actives:
            # Every active gets a link: stored URL, else a Maps search for it.
            maps = ("https://www.google.com/maps/search/?api=1&query="
                    + _quote(f"{o['label']} {dest}"))
            label = (f"<b>{_live_link(esc(o['label']), o, fallback=maps)}</b>"
                     if o["url"] else f"<b>{_linked(esc(o['label']), maps)}</b>")
            if o["details"]:
                label += f" — {esc(o['details'])}"
            if _col(o, "phone"):
                label += f" ☎ {_tel(o['phone'])}"
            # attached options (e.g. transit to this activity) ride inline
            for rel in options:
                if (_col(rel, "related_to") == o["id"] and rel["status"] != "cut"):
                    label += (f"<br>{_mode_emoji(rel)} "
                              f"{_linked(esc(rel['label']), rel['url'])}")
            kind_label = "🍽️ Food" if o["kind"] == "food" else "⛰️ Activity"
            st_map = {"booked": None, "held": "HELD", "favorite": "FAVORITE",
                      "shortlist": "SHORTLIST", "option": "Idea"}
            st = (_green() if o["status"] == "booked"
                  else (f"<b>{st_map[o['status']]}</b>"
                        if o["status"] != "option" else "Idea"))
            rows.append(row([label, kind_label, esc(o["saved_by"] or "—"), st]))
        h.append(table(rows))
    else:
        h.append("<p><i>Drop restaurants, tours, and ideas in the chat — they collect here.</i></p>")
    if working.get("actives"):
        h.append(_working_box(working["actives"]))

    # ── Itinerary — the timeline (always rendered, right after Actives) ─
    h.append(SPACER)
    h.append("<h2><b>🗓️ Itinerary</b></h2>")
    if not itinerary:
        h.append("<p><i>Nothing scheduled yet — arrivals, bookings, and plans "
                 "land here as they firm up.</i></p>")
    else:
        NAMED = {"morning": "Morning", "day": "Daytime", "afternoon": "Afternoon",
                 "evening": "Evening", "dinner": "Dinner", "all": ""}
        for i in itinerary:
            d = date.fromisoformat(i["day"])
            when = NAMED.get(i["slot"], i["slot"])  # "2:05pm" passes through
            label = f"{d.strftime('%A')}" + (f" {when}" if when else "")
            line = f"<p><b>{esc(label)}</b> – {esc(i['text'])}"
            if i["source"]:
                line += f" <i>({esc(i['source'])})</i>"
            h.append(line + "</p>")

    # "What needs doing" deliberately removed from the doc (2026-07-30):
    # the task stack still exists internally (trip_tasks.py rank/tree) and
    # drives the gardener, but surfacing it in the doc read as anxiety-inducing.

    # ── Transportation ────────────────────────────────────────────────
    h.append(SPACER)
    h.append("<h2><b>🚗 Transportation</b></h2>")
    if transport:
        tr_all = [o for o in options if o["kind"] == "transport" and o["status"] != "cut"]
        h.append(_section_sub(esc(summaries["transport"]) if summaries.get("transport")
                 else f"{_pl(len(tr_all), 'option')} | {_booked_bit(tr_all)}"))
        show_phone = any(_col(o, "phone") for o in transport)
        hdr = ["Option", "Details", "Price", "Saved by", "Status"]
        if show_phone:
            hdr.insert(3, "Phone")
        rows = [row(hdr, header=True)]
        for o in transport:
            st = o["status"].upper() if o["status"] != "option" else "option"
            st_cell = _status_cell(f"<b>{st}</b>" if st != "option" else st,
                                   o["status"] == "booked")
            cells = [f"<b>{_live_link(esc(o['label']), o)}</b>",
                     esc(o["details"] or ""), _price_cell(o),
                     esc(o["saved_by"] or "—"), st_cell]
            if show_phone:
                cells.insert(3, _tel(_col(o, "phone")))
            rows.append(row(cells))
        h.append(table(rows))
    else:
        h.append("<p><i>Rental cars, transfers, and trains land here.</i></p>")
    if working.get("transport"):
        h.append(_working_box(working["transport"]))

    # ── Comments & answers ────────────────────────────────────────────
    # Rendered INTO the body because full re-renders orphan margin comment
    # anchors (Docs hides the thread once its anchored text is replaced).
    # This keeps every question and Convos's reply visible in the doc itself.
    if comments:
        h.append(SPACER)
        h.append("<h2><b>💬 Comments &amp; answers</b></h2>")
        for c in comments[-10:]:
            who = esc((c.get("author") or {}).get("displayName") or "someone")
            q = _strip_marker(c.get("content"))
            if not q or q.startswith("(reopened"):
                continue
            h.append(f"<p><b>{who}:</b> {esc(q)}</p>")
            replies = [r for r in c.get("replies", []) if not r.get("deleted")
                       and (r.get("content") or "").strip()
                       and not (r.get("content") or "").strip().startswith("(reopened")]
            if replies:
                for r in replies:
                    rtext = (r.get("content") or "").strip()
                    rwho = ("Convos" if rtext.upper().startswith("CONVOS:")
                            else esc((r.get("author") or {}).get("displayName") or "reply"))
                    h.append(f"<p style='margin-left:24px'>↳ <b>{rwho}:</b> "
                             f"{esc(_strip_marker(rtext))}</p>")
            else:
                h.append("<p style='margin-left:24px'>↳ <i>Convos is on it.</i></p>")

    # ── Changelog ─────────────────────────────────────────────────────
    if log:
        h.append(SPACER)
        h.append("<h2><b>🗒️ Convos's changelog</b></h2>")
        for e in log:
            when = esc((e["created_at"] or "")[:16].replace("T", " "))
            h.append(f"<p><b>Convos</b> · {when} — {esc(e['message'])}</p>")

    h.append("</body></html>")
    return title, "".join(h)


def _services():
    sys.path.insert(0, GWS_SCRIPTS)
    try:
        from google_api import build_service  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        fail(f"google-workspace skill not available/authorized: {exc}. "
             f"Run its setup first: python3 {GWS_SCRIPTS}/setup.py --check")
    return build_service


def _write_tmp_html(html_text):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path


def cmd_render(args):
    conn = get_conn()
    title, html_text = render_html(conn, args.trip_id)
    out = args.out or f"/tmp/trip-{args.trip_id}-plan.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(json.dumps({"title": title, "html": out, "bytes": len(html_text)}))


def cmd_create(args):
    conn = get_conn()
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (args.trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {args.trip_id}")
    if trip["doc_id"]:
        fail(f"trip {args.trip_id} already has a doc: {trip['doc_url']} — use update")
    title, html_text = render_html(conn, args.trip_id)
    build_service = _services()
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415
    path = _write_tmp_html(html_text)
    drive = build_service("drive", "v3")
    meta = {"name": title, "mimeType": "application/vnd.google-apps.document"}
    media = MediaFileUpload(path, mimetype="text/html")
    doc = drive.files().create(body=meta, media_body=media,
                               fields="id, webViewLink").execute()
    os.unlink(path)
    share = args.share
    if share != "off":
        drive.permissions().create(
            fileId=doc["id"],
            body={"type": "anyone",
                  "role": "commenter" if share == "commenter" else "reader"},
        ).execute()
    conn.execute("UPDATE trips SET doc_id = ?, doc_url = ? WHERE id = ?",
                 (doc["id"], doc["webViewLink"], args.trip_id))
    conn.commit()
    print(json.dumps({
        "ok": True, "trip_id": args.trip_id, "doc_id": doc["id"],
        "url": doc["webViewLink"], "shared": share,
        "message": f"Trip doc is live — comment on anything: {doc['webViewLink']}",
    }, indent=2, ensure_ascii=False))


def cmd_update(args):
    conn = get_conn()
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (args.trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {args.trip_id}")
    if not trip["doc_id"]:
        fail(f"trip {args.trip_id} has no doc yet — use create")
    comments = _fetch_comment_threads(trip["doc_id"])
    title, html_text = render_html(conn, args.trip_id, comments=comments)
    build_service = _services()
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415
    path = _write_tmp_html(html_text)
    drive = build_service("drive", "v3")
    media = MediaFileUpload(path, mimetype="text/html")
    # body name keeps the Drive FILE title (top-left in Docs) in sync with
    # the rendered in-doc title — otherwise it stays whatever create() named it.
    drive.files().update(fileId=trip["doc_id"], body={"name": title},
                         media_body=media).execute()
    os.unlink(path)
    print(json.dumps({"ok": True, "trip_id": args.trip_id,
                      "url": trip["doc_url"], "updated_at": now()}))


def cmd_link(args):
    conn = get_conn()
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (args.trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {args.trip_id}")
    print(json.dumps({"trip_id": args.trip_id, "url": trip["doc_url"],
                      "doc_id": trip["doc_id"]}))


def main():
    p = argparse.ArgumentParser(description="Living Plan Google Doc renderer")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("render")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("create")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--share", default="commenter",
                    choices=["commenter", "reader", "off"])
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("update")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("link")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_link)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
