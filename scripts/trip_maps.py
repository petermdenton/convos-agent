#!/usr/bin/env python3
"""
trip_maps.py — build shareable Google Maps URLs with multiple pins. No API key.

Google Maps has no keyless "N arbitrary markers" URL, but its directions URL
renders every stop as a pin — so a stop list works as a pin map, and it opens
natively in the Google Maps app on iPhone/Android. Max 10 stops per URL
(app limit); more stops are split into numbered parts.

Usage:
  trip_maps.py route "Stop 1" "Stop 2" "Stop 3" [--mode walking] [--title "Day 1"]
      Ordered route — one URL, pins in order.
  trip_maps.py pins "Place A" "Place B" ... [--near "Hoi An, Vietnam"] [--title ...]
      Unordered pin map — same URL mechanism + a per-pin link list.
  trip_maps.py pin "Place" [--near ...]
      Single place link.

Options:
  --near TEXT     Context appended to bare names ("Bánh Mì Phượng" →
                  "Bánh Mì Phượng, Hoi An, Vietnam") so Google resolves them in
                  the right city. Skipped for entries that look like lat,lng or
                  already contain a comma.
  --coords        Geocode each place via Nominatim (OpenStreetMap, 1 req/s) and
                  pin exact lat,lng instead of trusting Google's name lookup.
                  Slower but unambiguous. Requires network.
  --mode          driving (default) | walking | bicycling | transit (route only)
  --title TEXT    Included in the output JSON for the chat message.
  --static        If GOOGLE_MAPS_API_KEY is set, also emit a Static Maps image
                  URL (all pins on one image, sendable as an iMessage photo).

Output: JSON with `url` (or `urls` when split), `per_pin` links, `message` —
a paste-ready chat line. All commands print JSON to stdout.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

MAX_STOPS = 10  # Google Maps app cap per directions URL
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "hermes-trip-maps/1.0 (personal travel assistant)"

_LATLNG = re.compile(r"^\s*-?\d{1,2}(\.\d+)?\s*,\s*-?\d{1,3}(\.\d+)?\s*$")


def qualify(place, near):
    """Append --near context to bare names; leave lat,lng and qualified names alone."""
    p = place.strip()
    if _LATLNG.match(p) or not near:
        return p
    if near.lower() in p.lower() or "," in p:
        return p
    return f"{p}, {near}"


def seg(place):
    """Encode one URL path segment for /maps/dir/."""
    return urllib.parse.quote(place, safe="")


def geocode(place):
    """Nominatim lookup → 'lat,lng' or None. Respects the 1 req/s ToS."""
    url = NOMINATIM + "?" + urllib.parse.urlencode(
        {"q": place, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — network best-effort
        return None, f"geocode failed for {place!r}: {exc}"
    time.sleep(1.1)
    if not data:
        return None, f"no geocode result for {place!r}"
    return f"{float(data[0]['lat']):.6f},{float(data[0]['lon']):.6f}", None


def dir_urls(stops):
    """Directions-style URLs, split into ≤MAX_STOPS chunks. Overlap the last
    stop of one chunk as the first of the next so routes stay continuous."""
    urls = []
    i = 0
    while i < len(stops):
        chunk = stops[i:i + MAX_STOPS]
        url = "https://www.google.com/maps/dir/" + "/".join(seg(s) for s in chunk)
        urls.append(url)
        if i + MAX_STOPS >= len(stops):
            break
        i += MAX_STOPS - 1  # overlap one stop for continuity
    return urls


def travelmode_param(mode):
    return {"driving": "driving", "walking": "walking",
            "bicycling": "bicycling", "transit": "transit"}.get(mode, "driving")


def api_dir_url(stops, mode):
    """Official maps URLs-API form (supports travelmode; max 10 incl. origin+dest)."""
    origin, dest, way = stops[0], stops[-1], stops[1:-1]
    q = {"api": "1", "origin": origin, "destination": dest,
         "travelmode": travelmode_param(mode)}
    if way:
        q["waypoints"] = "|".join(way)
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q)


def pin_url(place):
    return ("https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote(place, safe=""))


def static_url(stops, key):
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXY"
    markers = [
        f"color:red|label:{labels[i % len(labels)]}|{s}" for i, s in enumerate(stops)
    ]
    q = [("size", "640x640"), ("scale", "2"), ("maptype", "roadmap"), ("key", key)]
    q += [("markers", m) for m in markers]
    return "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(q)


def resolve_stops(places, args):
    stops, labels, warnings = [], [], []
    for p in places:
        q = qualify(p, args.near)
        if args.coords and not _LATLNG.match(q):
            coord, err = geocode(q)
            if coord:
                stops.append(coord)
            else:
                warnings.append(err)
                stops.append(q)  # fall back to the name
        else:
            stops.append(q)
        labels.append(p.strip())
    return stops, labels, warnings


def build_message(title, urls, count, kind):
    head = title or ("Route" if kind == "route" else "Map")
    if len(urls) == 1:
        return f"{head} — {count} pins: {urls[0]}"
    parts = " · ".join(f"part {i+1}: {u}" for i, u in enumerate(urls))
    return f"{head} — {count} pins in {len(urls)} maps: {parts}"


def cmd_route(args):
    stops, labels, warnings = resolve_stops(args.places, args)
    if len(stops) < 2:
        print(json.dumps({"error": "route needs at least 2 places"}), file=sys.stderr)
        sys.exit(1)
    if len(stops) <= MAX_STOPS and args.mode != "driving":
        urls = [api_dir_url(stops, args.mode)]
    else:
        urls = dir_urls(stops)
    out = {
        "kind": "route", "title": args.title, "stops": labels,
        "urls": urls, "message": build_message(args.title, urls, len(stops), "route"),
    }
    finish(out, stops, warnings, args)


def cmd_pins(args):
    stops, labels, warnings = resolve_stops(args.places, args)
    urls = dir_urls(stops) if len(stops) > 1 else [pin_url(stops[0])]
    out = {
        "kind": "pins", "title": args.title, "pins": labels, "urls": urls,
        "per_pin": [{"name": n, "url": pin_url(s)} for n, s in zip(labels, stops)],
        "message": build_message(args.title, urls, len(stops), "pins"),
    }
    finish(out, stops, warnings, args)


def cmd_pin(args):
    stops, labels, warnings = resolve_stops([args.place], args)
    out = {"kind": "pin", "name": labels[0], "url": pin_url(stops[0]),
           "message": f"{labels[0]}: {pin_url(stops[0])}"}
    finish(out, stops, warnings, args)


def finish(out, stops, warnings, args):
    if warnings:
        out["warnings"] = warnings
    if getattr(args, "static", False):
        key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if key:
            out["static_image_url"] = static_url(stops, key)
        else:
            out.setdefault("warnings", []).append(
                "--static requested but GOOGLE_MAPS_API_KEY is not set")
    print(json.dumps(out, indent=2, ensure_ascii=False))


# ── PNG rendering: all pins on one image, OSM tiles, no API key ────────

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_SIZE = 256


def _latlng_to_xy(lat, lng, zoom):
    import math
    n = 2 ** zoom
    x = (lng + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _fit_zoom(coords, width, height, pad=60):
    """Largest zoom (≤17) where all coords fit in width×height with padding."""
    for zoom in range(17, 1, -1):
        xs, ys = zip(*(_latlng_to_xy(lat, lng, zoom) for lat, lng in coords))
        w = (max(xs) - min(xs)) * TILE_SIZE
        h = (max(ys) - min(ys)) * TILE_SIZE
        if w <= width - 2 * pad and h <= height - 2 * pad:
            return zoom
    return 2


def _fetch_tile(z, x, y):
    n = 2 ** z
    req = urllib.request.Request(
        TILE_URL.format(z=z, x=x % n, y=y % n), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def render_png(coords, labels, out_path, width=800, height=800):
    """Composite OSM tiles and draw lettered pins for every coordinate."""
    import io
    import math
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None, ("Pillow not installed — run: pip3 install pillow "
                      "--break-system-packages (one-time), then retry")
    zoom = _fit_zoom(coords, width, height)
    xs, ys = zip(*(_latlng_to_xy(lat, lng, zoom) for lat, lng in coords))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # top-left of canvas in tile units
    tlx, tly = cx - width / 2 / TILE_SIZE, cy - height / 2 / TILE_SIZE
    img = Image.new("RGB", (width, height), "#e8e6e1")
    for tx in range(math.floor(tlx), math.floor(tlx + width / TILE_SIZE) + 1):
        for ty in range(math.floor(tly), math.floor(tly + height / TILE_SIZE) + 1):
            if ty < 0 or ty >= 2 ** zoom:
                continue
            try:
                tile = Image.open(io.BytesIO(_fetch_tile(zoom, tx, ty)))
                img.paste(tile, (round((tx - tlx) * TILE_SIZE), round((ty - tly) * TILE_SIZE)))
            except Exception:  # noqa: BLE001 — missing tile → grey square
                pass
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
        small = ImageFont.truetype("DejaVuSans.ttf", 13)
    except OSError:
        font = small = ImageFont.load_default()
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i, (lat, lng) in enumerate(coords):
        px = (_latlng_to_xy(lat, lng, zoom)[0] - tlx) * TILE_SIZE
        py = (_latlng_to_xy(lat, lng, zoom)[1] - tly) * TILE_SIZE
        r = 14
        # pin: circle + point
        draw.polygon([(px, py), (px - r * 0.7, py - r * 1.2), (px + r * 0.7, py - r * 1.2)],
                     fill="#d33")
        draw.ellipse([px - r, py - r * 2.4, px + r, py - r * 0.4],
                     fill="#d33", outline="white", width=2)
        letter = letters[i % 26]
        lw = draw.textlength(letter, font=font)
        draw.text((px - lw / 2, py - r * 1.85), letter, fill="white", font=font)
    # legend strip
    legend = " · ".join(f"{letters[i % 26]}={labels[i]}" for i in range(len(labels)))
    pad = 6
    lh = 18 * (1 + len(legend) // 110)
    draw.rectangle([0, height - lh - 2 * pad, width, height], fill="white")
    # wrap legend roughly
    line, yline = "", height - lh - pad
    for chunk in legend.split(" · "):
        candidate = (line + " · " + chunk) if line else chunk
        if draw.textlength(candidate, font=small) > width - 2 * pad and line:
            draw.text((pad, yline), line, fill="#222", font=small)
            yline += 18
            line = chunk
        else:
            line = candidate
    draw.text((pad, yline), line + "  |  © OpenStreetMap", fill="#222", font=small)
    img.save(out_path, "PNG")
    return out_path, None


def cmd_render(args):
    coords, labels, warnings = [], [], []
    for p in args.places:
        q = qualify(p, args.near)
        if _LATLNG.match(q):
            lat, lng = (float(v) for v in q.split(","))
            coords.append((lat, lng))
        else:
            coord, err = geocode(q)
            if coord:
                lat, lng = (float(v) for v in coord.split(","))
                coords.append((lat, lng))
            else:
                warnings.append(err)
                continue
        labels.append(p.strip())
    if not coords:
        print(json.dumps({"error": "no places could be geocoded", "warnings": warnings}),
              file=sys.stderr)
        sys.exit(1)
    out_dir = os.path.expanduser(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (args.title or "map").lower()).strip("-") or "map"
    out_path = os.path.join(out_dir, f"{slug}.png")
    path, err = render_png(coords, labels, out_path)
    if err:
        print(json.dumps({"error": err}), file=sys.stderr)
        sys.exit(1)
    stops = [f"{lat:.6f},{lng:.6f}" for lat, lng in coords]
    urls = dir_urls(stops) if len(stops) > 1 else [pin_url(stops[0])]
    out = {
        "kind": "render", "title": args.title, "pins": labels, "image": path,
        "urls": urls,
        "message": build_message(args.title, urls, len(stops), "pins"),
        "attribution": "Map tiles © OpenStreetMap contributors",
    }
    if warnings:
        out["warnings"] = warnings
    print(json.dumps(out, indent=2, ensure_ascii=False))


def add_common(sp, plural=True):
    if plural:
        sp.add_argument("places", nargs="+")
    sp.add_argument("--near", default=None)
    sp.add_argument("--coords", action="store_true")
    sp.add_argument("--title", default=None)
    sp.add_argument("--static", action="store_true")


def main():
    p = argparse.ArgumentParser(description="Google Maps multi-pin URL builder (keyless)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("route")
    add_common(sp)
    sp.add_argument("--mode", default="driving",
                    choices=["driving", "walking", "bicycling", "transit"])
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("pins")
    add_common(sp)
    sp.set_defaults(func=cmd_pins)

    sp = sub.add_parser("pin")
    sp.add_argument("place")
    add_common(sp, plural=False)
    sp.set_defaults(func=cmd_pin)

    sp = sub.add_parser("render")
    sp.add_argument("places", nargs="+")
    sp.add_argument("--near", default=None)
    sp.add_argument("--title", default=None)
    sp.add_argument("--out-dir", default="~/.hermes/image_cache/trip-maps")
    sp.set_defaults(func=cmd_render)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
