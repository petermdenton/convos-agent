#!/usr/bin/env python3
"""
trip_pwa.py — build the trip's offline travel-day app from state.

The third surface of Convos: iMessage is input, the Google Doc is the shared
planning record, and this is the phone-native artifact for the trip itself —
an installable, fully offline PWA with the day-by-day, every confirmation
number, tap-to-call phones, and maps links. Modeled on the hand-built
Vietnam app (LinkedIn post, 4mo ago); this generalizes it: any trip, one
command, rebuilt automatically as state changes.

Like the doc, the app is a PROJECTION of trips.db — never hand-edit output.

Usage:
  trip_pwa.py build <trip_id> [--out DIR]     # write the site (default ~/.hermes/pwa/<id>)
  trip_pwa.py deploy <trip_id> [--if-deployed]
      Build + push to Netlify. First deploy creates the site (needs
      NETLIFY_AUTH_TOKEN in ~/.hermes/.env); later deploys update it in
      place, same URL. --if-deployed exits quietly unless the trip already
      has a site (used by the doc-sync hook for auto-refresh).
  trip_pwa.py url <trip_id>                   # print the deployed URL

All commands print JSON.
"""
import argparse
import hashlib
import html as htmllib
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB", os.path.expanduser("~/.hermes/data/trips.db"))
OUT_ROOT = os.path.expanduser("~/.hermes/pwa")
ENV_PATH = os.path.expanduser("~/.hermes/.env")


def fail(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def esc(v):
    return htmllib.escape(str(v)) if v is not None else ""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(trips)")}
    for col in ("pwa_url", "pwa_site_id"):
        if col not in cols:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {col} TEXT")
    conn.commit()
    return conn


def _col(row, name):
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _env_token():
    """NETLIFY_AUTH_TOKEN from the process env or ~/.hermes/.env."""
    tok = os.environ.get("NETLIFY_AUTH_TOKEN")
    if tok:
        return tok.strip()
    try:
        for line in open(ENV_PATH):
            if line.strip().startswith("NETLIFY_AUTH_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


# ── state → view model ──────────────────────────────────────────────

def load(conn, trip_id):
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {trip_id}")
    options = conn.execute(
        "SELECT * FROM plan_options WHERE trip_id = ? AND status != 'cut' "
        "ORDER BY id", (trip_id,)).fetchall()
    itinerary = conn.execute(
        "SELECT * FROM plan_itinerary WHERE trip_id = ? ORDER BY day, id",
        (trip_id,)).fetchall()
    try:
        roster = conn.execute(
            """SELECT tv.name, tv.phone FROM trip_travelers tt
               JOIN travelers tv ON tv.id = tt.traveler_id WHERE tt.trip_id = ?""",
            (trip_id,)).fetchall()
    except sqlite3.OperationalError:
        roster = []
    try:
        day_meta = {r["day"]: {"title": r["title"], "subtitle": r["subtitle"]}
                    for r in conn.execute(
                        "SELECT day, title, subtitle FROM plan_days WHERE trip_id = ?",
                        (trip_id,))}
    except sqlite3.OperationalError:
        day_meta = {}
    return trip, options, itinerary, roster, day_meta


def _days(trip, options, itinerary):
    """Ordered ISO dates the trip spans (from trip dates, stays, itinerary)."""
    dates = set()
    for src in ([trip["start_date"], trip["end_date"]]
                + [i["day"] for i in itinerary]
                + [x for o in options for x in (_col(o, "checkin"), _col(o, "checkout"))]):
        if src:
            try:
                dates.add(date.fromisoformat(src[:10]))
            except ValueError:
                pass
    if not dates:
        return []
    lo, hi = min(dates), max(dates)
    if (hi - lo).days > 45:  # safety on garbage dates
        hi = lo + timedelta(days=45)
    return [(lo + timedelta(days=i)).isoformat() for i in range((hi - lo).days + 1)]


_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*$", re.I)
_SLOT_ORDER = {"morning": "08:00", "day": "11:00", "afternoon": "14:00",
               "evening": "18:00", "dinner": "19:00", "all": "00:00"}


def _slot_sort_key(slot):
    m = _TIME_RE.match(slot or "")
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        return f"{h:02d}:{m.group(2)}"
    return _SLOT_ORDER.get((slot or "").lower(), "12:00")


def _slot_label(slot):
    m = _TIME_RE.match(slot or "")
    if m:
        return slot.strip()
    return {"morning": "Morning", "day": "Daytime", "afternoon": "Afternoon",
            "evening": "Evening", "dinner": "Dinner", "all": ""}.get(
        (slot or "").lower(), slot or "")


def _gcal(day, slot, text):
    """Add-to-calendar link; timed 1h event when the slot is a clock time."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return None
    m = _TIME_RE.match(slot or "")
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        start = datetime(d.year, d.month, d.day, h, int(m.group(2)))
        dates = (start.strftime("%Y%m%dT%H%M%S") + "/"
                 + (start + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S"))
    else:
        dates = d.strftime("%Y%m%d") + "/" + (d + timedelta(days=1)).strftime("%Y%m%d")
    q = urllib.request.quote
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={q(text)}&dates={dates}")


def _maps(label, dest):
    q = urllib.request.quote(f"{label} {dest or ''}".strip())
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def _tel_href(phone):
    return "tel:" + re.sub(r"[^\d+]", "", phone or "")


def _chips(o, dest):
    """Action chips for an option: call / map / link / booking ref."""
    out = []
    if _col(o, "phone"):
        out.append(f'<a class="chip" href="{_tel_href(o["phone"])}">☎ Call</a>')
    out.append(f'<a class="chip" href="{esc(_maps(o["label"], dest))}">📍 Map</a>')
    if o["url"]:
        out.append(f'<a class="chip" href="{esc(o["url"])}">🔗 Link</a>')
    return out


def _conf(o):
    """Confirmation/booking reference if the note looks like it carries one."""
    note = (o["note"] or "").strip()
    return note if note else None


# ── HTML ────────────────────────────────────────────────────────────
#
# Design lifted from the hand-built Vietnam 2026 app: hero photo header,
# day tabs (D1..DN) that switch views, per-day location headlines, event
# cards with emoji + time + rich details, highlighted confirmation boxes,
# Maps/Call chips. Light theme, travel-red accent.

ACCENT = "#8f1d1d"

CSS = """
:root{--acc:#8f1d1d;--accsoft:#fdf3f3;--bg:#f4f1ec;--card:#fff;--ink:#1c1c1e;
--dim:#6e6e73;--line:#e4e0d8;--green:#1e7e34;--conf:#fdf8e7;--confline:#efe3b0}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,system-ui,sans-serif;padding-bottom:50px}
.hero{position:relative;color:#fff;padding:22px 16px 14px;
background:linear-gradient(rgba(80,15,15,.55),rgba(80,15,15,.75)),var(--heroimg) center/cover,var(--acc)}
.hero h1{font-size:26px;letter-spacing:-.4px;text-shadow:0 1px 3px rgba(0,0,0,.4)}
.hero .who{font-size:13px;opacity:.92;margin-top:2px}
.hero .dates{position:absolute;right:16px;bottom:14px;font-size:13px;opacity:.92}
nav{display:flex;gap:7px;overflow-x:auto;padding:10px 12px;background:var(--acc);
position:sticky;top:0;z-index:9;scrollbar-width:none}
nav::-webkit-scrollbar{display:none}
nav button{flex:0 0 auto;border:0;border-radius:11px;padding:7px 13px;
background:rgba(255,255,255,.14);color:#fff;font:inherit;font-size:13px;
line-height:1.25;text-align:center;cursor:pointer}
nav button b{display:block;font-size:14px}
nav button.on{background:#fff;color:var(--acc)}
main{padding:16px 14px}
.dayhead h2{color:var(--acc);font-size:23px;letter-spacing:-.3px}
.dayhead .sub{color:var(--dim);font-size:14px;margin:3px 0 12px}
.evt{background:var(--card);border-radius:14px;padding:14px 14px 12px;
margin-bottom:12px;border-left:4px solid var(--acc);
box-shadow:0 1px 4px rgba(0,0,0,.06)}
.evt .time{color:var(--dim);font-size:13px}
.evt .time .em{font-size:16px;margin-right:6px}
.evt h3{font-size:17px;margin:1px 0 4px}
.evt .d{color:#3a3a3c;font-size:14.5px;white-space:pre-line}
.conf{background:var(--conf);border:1px solid var(--confline);border-radius:9px;
padding:9px 11px;margin-top:9px;font:13px ui-monospace,Menlo,monospace;
white-space:pre-line}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:11px}
.chip{padding:6px 13px;border-radius:16px;text-decoration:none;font-size:13.5px;
font-weight:600}
.chip.map{background:#e8f2e8;color:var(--green)}
.chip.call{background:#e8eefb;color:#1a56c4}
.chip.link{background:#f3e8fb;color:#7d3cc4}
.chip.cal{background:#fbf0e0;color:#a4660a}
.booked{color:var(--green);font-weight:700;font-size:11.5px;letter-spacing:.6px}
h4.sect{color:var(--dim);font-size:13px;text-transform:uppercase;
letter-spacing:1.2px;margin:20px 0 10px}
.stamp{color:var(--dim);font-size:12px;text-align:center;padding:24px 0 8px}
.offline{position:fixed;bottom:0;left:0;right:0;background:#3a3a1e;
color:#e6ee9c;text-align:center;font-size:12px;padding:5px;display:none}
.open{color:var(--dim);font-size:14.5px;padding:6px 2px}
"""

_EMOJI_RULES = (
    (("flight", "depart", "airport", "board plane", "land", "arrive"), "✈️"),
    (("check in", "check-in", "checkin", "hotel", "suite"), "🏨"),
    (("check out", "check-out", "checkout"), "🧳"),
    (("cruise", "boat", "bay", "ferry", "sail", "embark"), "🛳️"),
    (("train", "rail", "metro"), "🚆"),
    (("limo", "pickup", "transfer", "taxi", "drive", "car", "shuttle", "bus"), "🚐"),
    (("dinner", "lunch", "breakfast", "restaurant", "food", "tacos", "bagel",
      "cafe", "coffee"), "🍽️"),
    (("game", "match", "concert", "show", "tickets", "stadium", "park —"), "🎟️"),
    (("hike", "walk", "tour", "museum", "winery", "tasting"), "🥾"),
)


def _evt_emoji(text):
    low = (text or "").lower()
    for words, emoji in _EMOJI_RULES:
        if any(w in low for w in words):
            return emoji
    return "📍"



_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_when(texts, year):
    """(iso_day, '6:40pm'|None) parsed from free text like
    'Fri Sep 11, first pitch 6:40p' or 'SEA 11:54a → SMF 2:05p · Sep 11'."""
    blob = " ".join(t for t in texts if t)
    low = blob.lower()
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                  r"[a-z]*\.?\s+(\d{1,2})\b", low)
    if not m:
        return None, None
    try:
        day_iso = date(year, _MON[m.group(1)], int(m.group(2))).isoformat()
    except ValueError:
        return None, None
    tm = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm|a|p)\b", low)
    when = None
    if tm:
        ampm = "am" if tm.group(3).startswith("a") else "pm"
        when = f"{int(tm.group(1))}:{tm.group(2)}{ampm}"
    return day_iso, when


def _derived_day_events(d, options, iti_texts, dest, year):
    """Events for day ``d`` recovered from BOOKED options whose details carry
    a parseable date — the safety net for un-itinerarized bookings (a booked
    game must never be invisible in the app)."""
    out = []
    for o in options:
        if o["status"] != "booked" or o["kind"] not in ("activity", "food", "flight"):
            continue
        day_iso, when = _parse_when((o["details"], o["label"]), year)
        if day_iso != d:
            continue
        frag = o["label"].lower()[:14]
        if any(frag in t for t in iti_texts):
            continue  # already itinerarized — don't double-show
        emoji = "✈️" if o["kind"] == "flight" else _evt_emoji(
            o["label"] + " " + (o["details"] or ""))
        out.append((_slot_sort_key(when) if when else "12:00",
                    _evt_card(emoji, when or "All day", o["label"],
                              o["details"] or "", dest, conf=_conf(o),
                              booked=True, phone=_col(o, "phone"),
                              url=o["url"], cal=_gcal(d, when, o["label"]),
                              maps_label=o["label"])))
    return out


def _fmt_d(iso):
    try:
        d = date.fromisoformat(iso)
        return f"{d.strftime('%b')} {d.day}"
    except (ValueError, TypeError):
        return iso or ""


def _fmt_range(trip):
    s, e = trip["start_date"], trip["end_date"]
    if s and e:
        return f"{_fmt_d(s)} – {_fmt_d(e)}"
    return _fmt_d(s) if s else None


def _chip(kind, href, label):
    return f'<a class="chip {kind}" href="{esc(href)}">{label}</a>'


def _evt_card(emoji, when, title, details, dest, *, conf=None, booked=False,
              phone=None, url=None, cal=None, maps_label=None):
    h = ['<div class="evt">',
         f'<div class="time"><span class="em">{emoji}</span>{esc(when)}</div>',
         f'<h3>{esc(title)}' + (' <span class="booked">BOOKED</span>' if booked else "") + "</h3>"]
    if details:
        h.append(f'<div class="d">{esc(details)}</div>')
    if conf:
        h.append(f'<div class="conf">{esc(conf)}</div>')
    chips = [_chip("map", _maps(maps_label or title, dest), "📍 Maps")]
    if phone:
        chips.append(_chip("call", _tel_href(phone), "📞 Call"))
    if url:
        chips.append(_chip("link", url, "🔗 Link"))
    if cal:
        chips.append(_chip("cal", cal, "📅 Calendar"))
    h.append(f'<div class="chips">{"".join(chips)}</div></div>')
    return "".join(h)


def build_html(trip, options, itinerary, roster, day_meta=None):
    day_meta = day_meta or {}
    dest = trip["destination"] or trip["name"]
    title = dest + (f" · {_fmt_range(trip)}" if trip["start_date"] else "")
    booked = [o for o in options if o["status"] == "booked"]
    flights = [o for o in booked if o["kind"] == "flight"]
    stays = [o for o in booked if o["kind"] == "stay"]
    b_act = [o for o in booked if o["kind"] in ("activity", "food")]
    ideas = [o for o in options if o["status"] in ("favorite", "shortlist", "option")
             and o["kind"] in ("activity", "food")]
    contacts = [o for o in options if _col(o, "phone")]
    rel = {}
    for t in options:
        if t["kind"] == "transport" and _col(t, "related_to"):
            rel.setdefault(t["related_to"], []).append(t)

    who = " · ".join(r["name"].split()[0] for r in roster if r["name"]) or ""
    days = _days(trip, options, itinerary)

    h = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="{ACCENT}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc('Trip plan — ' + (who or 'the crew'))}">
<title>{esc(title)}</title>
<link rel="manifest" href="manifest.json">
<style>{CSS}
.hero{{--heroimg:url('header.jpg')}}</style></head><body>
<div class="hero"><h1>{esc(dest)}</h1>
<div class="who">{esc(who)}</div>
<div class="dates">{esc(_fmt_range(trip) or "")}</div></div>"""]

    # Day tabs + Info tab
    h.append("<nav>")
    for n, d in enumerate(days, 1):
        dd = date.fromisoformat(d)
        h.append(f'<button data-p="d{d}" id="t-d{d}"><b>D{n}</b>'
                 f'{dd.strftime("%b")} {dd.day}</button>')
    h.append('<button data-p="info" id="t-info"><b>ℹ️</b>Info</button></nav><main>')

    iti_by_day = {}
    for i in itinerary:
        iti_by_day.setdefault(i["day"], []).append(i)

    for n, d in enumerate(days, 1):
        dd = date.fromisoformat(d)
        meta = day_meta.get(d) or {}
        headline = meta.get("title") or dest
        subtitle = f"Day {n} · {dd.strftime('%A, %B %-d')}"
        if meta.get("subtitle"):
            subtitle += f" · {meta['subtitle']}"
        h.append(f'<div class="page" id="d{d}" hidden><div class="dayhead">'
                 f"<h2>{esc(headline)}</h2>"
                 f'<div class="sub">{esc(subtitle)}</div></div>')
        events = []
        iti_texts = [i["text"].lower() for i in iti_by_day.get(d, [])]
        yr = dd.year
        events.extend(_derived_day_events(d, options, iti_texts, dest, yr))
        for i in iti_by_day.get(d, []):
            events.append((_slot_sort_key(i["slot"]),
                           _evt_card(_evt_emoji(i["text"]),
                                     _slot_label(i["slot"]) or "All day",
                                     i["text"],
                                     f"({i['source']})" if i["source"] else "",
                                     dest, cal=_gcal(d, i["slot"], i["text"]))))
        for o in stays:
            if _col(o, "checkin") == d:
                events.append(("15:00", _evt_card(
                    "🏨", "3:00pm", f"Check in — {o['label']}",
                    o["details"] or "", dest, conf=_conf(o),
                    phone=_col(o, "phone"), url=o["url"], maps_label=o["label"])))
            if _col(o, "checkout") == d:
                events.append(("11:00", _evt_card(
                    "🧳", "11:00am", f"Check out — {o['label']}", "", dest,
                    phone=_col(o, "phone"), maps_label=o["label"])))
        if not events:
            h.append('<div class="open">Nothing scheduled yet — open day.</div>')
        else:
            for _, card in sorted(events, key=lambda e: e[0]):
                h.append(card)
        h.append("</div>")

    # Info page: bookings, ideas, contacts
    h.append('<div class="page" id="info" hidden><div class="dayhead">'
             f"<h2>Trip info</h2><div class=\"sub\">{esc(title)}</div></div>")
    if flights:
        h.append('<h4 class="sect">✈️ Flights</h4>')
        for o in flights:
            h.append(_evt_card("✈️", "Booked", o["label"], o["details"] or "",
                               dest, conf=_conf(o), booked=True, url=o["url"],
                               maps_label=o["label"]))
    if stays:
        h.append('<h4 class="sect">🏨 Stays</h4>')
        for o in stays:
            det = o["details"] or ""
            ci, co = _col(o, "checkin"), _col(o, "checkout")
            if ci and co:
                det = (det + "\n" if det else "") + \
                    f"Check-in {_fmt_d(ci)} · Check-out {_fmt_d(co)}"
            h.append(_evt_card("🏨", "Booked", o["label"], det, dest,
                               conf=_conf(o), booked=True,
                               phone=_col(o, "phone"), url=o["url"],
                               maps_label=o["label"]))
    if b_act:
        h.append('<h4 class="sect">🎟️ Booked activities</h4>')
        for o in b_act:
            det = o["details"] or ""
            for t in rel.get(o["id"], []):
                det = (det + "\n" if det else "") + f"Getting there: {t['label']}"
            h.append(_evt_card("🎟️", "Booked", o["label"], det, dest,
                               conf=_conf(o), booked=True,
                               phone=_col(o, "phone"), url=o["url"],
                               maps_label=o["label"]))
    if ideas:
        h.append('<h4 class="sect">💡 Ideas on the ground</h4>')
        for o in ideas[:20]:
            h.append(_evt_card(_evt_emoji(o["label"] + " " + (o["details"] or "")),
                               o["kind"].title(), o["label"], o["details"] or "",
                               dest, phone=_col(o, "phone"), url=o["url"],
                               maps_label=o["label"]))
    if contacts:
        h.append('<h4 class="sect">📞 Numbers you might need</h4>')
        for o in contacts:
            h.append(_evt_card("📞", "", o["label"], "", dest,
                               phone=o["phone"], maps_label=o["label"]))
    h.append("</div>")

    h.append(f'<div class="stamp">Maintained by Convos · rebuilt '
             f'{datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")} · '
             f'works offline once loaded</div>')
    h.append('<div class="offline" id="off">offline — running from cache</div>')
    h.append("""</main><script>
if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js');
const pages=[...document.querySelectorAll('.page')];
const tabs=[...document.querySelectorAll('nav button')];
function show(id){pages.forEach(p=>p.hidden=p.id!==id);
tabs.forEach(t=>t.classList.toggle('on',t.dataset.p===id));
const el=document.getElementById('t-'+id);if(el)el.scrollIntoView({inline:'center',block:'nearest'});}
tabs.forEach(t=>t.onclick=()=>show(t.dataset.p));
const today='d'+new Date().toISOString().slice(0,10);
show(document.getElementById(today)?today:(pages[0]?pages[0].id:'info'));
addEventListener('offline',()=>document.getElementById('off').style.display='block');
addEventListener('online',()=>document.getElementById('off').style.display='none');
</script></body></html>""")
    return title, "".join(h)


def _fetch_hero(out, trip):
    """Destination hero photo for the header (best-effort, cached)."""
    path = os.path.join(out, "header.jpg")
    if os.path.exists(path):
        return True
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from place_photos import _ddg_images, _fetch, MAGIC  # noqa: PLC0415
        dest = trip["destination"] or trip["name"] or ""
        warnings = []
        for u in _ddg_images(f"{dest} skyline travel scenic", warnings)[:6]:
            try:
                data = _fetch(u.replace("\\/", "/"), timeout=12)
            except Exception:  # noqa: BLE001
                continue
            if len(data) > 30_000 and any(data.startswith(m) for m in MAGIC):
                with open(path, "wb") as f:
                    f.write(data)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ── site assembly ───────────────────────────────────────────────────

SW = """const C='convos-%s';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>Promise.allSettled(['./','index.html','manifest.json','header.jpg','icon.png'].map(u=>c.add(u)))));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))));self.clients.claim()});
self.addEventListener('fetch',e=>{e.respondWith(caches.match(e.request,{ignoreSearch:true}).then(r=>r||fetch(e.request).then(n=>{if(e.request.method==='GET'&&n.ok){const cp=n.clone();caches.open(C).then(c=>c.put(e.request,cp))}return n}).catch(()=>caches.match('index.html'))))});
"""


def cmd_build(args, quiet=False):
    conn = get_conn()
    trip, options, itinerary, roster, day_meta = load(conn, args.trip_id)
    title, html_text = build_html(trip, options, itinerary, roster, day_meta)
    out = args.out or os.path.join(OUT_ROOT, str(args.trip_id))
    os.makedirs(out, exist_ok=True)
    _fetch_hero(out, trip)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_text)
    manifest = {
        "name": title, "short_name": (trip["destination"] or trip["name"])[:20],
        "start_url": ".", "display": "standalone",
        "background_color": "#0f1115", "theme_color": "#0f1115",
        "icons": [],
    }
    icon = _make_icon(out, trip)
    if icon:
        manifest["icons"] = [{"src": icon, "sizes": "512x512", "type": "image/png"}]
        with open(os.path.join(out, "index.html"), "a", encoding="utf-8") as f:
            f.write(f'<link rel="apple-touch-icon" href="{icon}">')
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    ver = hashlib.sha1(html_text.encode()).hexdigest()[:10]
    with open(os.path.join(out, "sw.js"), "w") as f:
        f.write(SW % f"{args.trip_id}-{ver}")
    result = {"ok": True, "trip_id": args.trip_id, "out": out, "title": title,
              "version": ver}
    if not quiet:
        print(json.dumps(result))
    return out, result


def _make_icon(out, trip):
    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415
    except ImportError:
        return None
    img = Image.new("RGB", (512, 512), "#0f1115")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([32, 32, 480, 480], radius=96, fill="#4c8bf5")
    letter = ((trip["destination"] or trip["name"] or "T").strip() or "T")[0].upper()
    try:
        from PIL import ImageFont  # noqa: PLC0415
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 300)
    except Exception:  # noqa: BLE001
        font = None
    d.text((256, 240), letter, fill="white", anchor="mm", font=font)
    img.save(os.path.join(out, "icon.png"))
    return "icon.png"


# ── Netlify deploy ──────────────────────────────────────────────────

def _zip_dir(path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in os.listdir(path):
            z.write(os.path.join(path, name), name)
    return buf.getvalue()


def _netlify(method, endpoint, token, body=None, ctype="application/json"):
    req = urllib.request.Request(
        f"https://api.netlify.com/api/v1{endpoint}", data=body, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype,
                 "User-Agent": "convos-trip-pwa"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def cmd_deploy(args):
    conn = get_conn()
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (args.trip_id,)).fetchone()
    if not trip:
        fail(f"no trip with id {args.trip_id}")
    site_id = _col(trip, "pwa_site_id")
    if args.if_deployed and not site_id:
        print(json.dumps({"ok": True, "skipped": "no site yet"}))
        return
    token = _env_token()
    if not token:
        fail("NETLIFY_AUTH_TOKEN not set in ~/.hermes/.env — create a personal "
             "access token at app.netlify.com/user/applications and add "
             "NETLIFY_AUTH_TOKEN=... (the app still builds locally via `build`)")
    out, built = cmd_build(argparse.Namespace(trip_id=args.trip_id, out=None),
                           quiet=True)
    payload = _zip_dir(out)
    if site_id:
        _netlify("POST", f"/sites/{site_id}/deploys", token, payload,
                 "application/zip")
    else:
        site = _netlify("POST", "/sites", token, payload, "application/zip")
        site_id = site["id"]
    # Re-fetch for the canonical site URL, and strip any deploy-hash prefix
    # (zip-create can return the preview URL: https://<hash>--name.netlify.app).
    site = _netlify("GET", f"/sites/{site_id}", token)
    url = site.get("ssl_url") or site.get("url") or ""
    url = re.sub(r"^(https?://)[0-9a-f]{10,}--", r"\1", url)
    conn.execute("UPDATE trips SET pwa_site_id = ?, pwa_url = ? WHERE id = ?",
                 (site_id, url, args.trip_id))
    conn.commit()
    print(json.dumps({"ok": True, "trip_id": args.trip_id, "url": url,
                      "site_id": site_id, "version": built["version"]}))


def cmd_url(args):
    conn = get_conn()
    trip = conn.execute("SELECT pwa_url FROM trips WHERE id = ?",
                        (args.trip_id,)).fetchone()
    print(json.dumps({"trip_id": args.trip_id,
                      "url": _col(trip, "pwa_url") if trip else None}))


def main():
    p = argparse.ArgumentParser(description="Trip travel-day PWA builder")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("build")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=cmd_build)
    sp = sub.add_parser("deploy")
    sp.add_argument("trip_id", type=int)
    sp.add_argument("--if-deployed", action="store_true")
    sp.set_defaults(func=cmd_deploy)
    sp = sub.add_parser("url")
    sp.add_argument("trip_id", type=int)
    sp.set_defaults(func=cmd_url)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
