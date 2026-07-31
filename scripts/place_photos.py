#!/usr/bin/env python3
"""place_photos.py — fetch real photos of a place for sending into the chat.

Sources, in order:
  1. --url page's og:image / twitter:image (the place's own hero shot)
  2. Bing image search results for the query

Images are downloaded to ~/.hermes/image_cache/places/, validated (magic
bytes + minimum size), and returned as file paths ready to send as Photon
outbound attachments.

Usage:
  place_photos.py "Noah's Bagels Sacramento" [--count 3] [--url https://...]

Prints JSON: {"query": ..., "images": ["/path/1.jpg", ...], "warnings": [...]}
No API keys required.
"""
import argparse
import html as htmllib
import json
import os
import re
import sys
import urllib.request

CACHE = os.path.expanduser("~/.hermes/image_cache/places")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MIN_BYTES = 8_000  # skip favicons/trackers

MAGIC = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png",
         b"GIF8": ".gif", b"RIFF": ".webp"}


def _fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _og_images(page_url, warnings):
    try:
        html = _fetch(page_url).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"page fetch failed: {e}")
        return []
    urls = re.findall(
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\']'
        r'[^>]+content=["\']([^"\']+)', html, re.I)
    urls += re.findall(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)='
        r'["\'](?:og:image|twitter:image)', html, re.I)
    return [htmllib.unescape(u) for u in urls]


def _ddg_images(query, warnings):
    q = urllib.request.quote(query)
    try:
        page = _fetch(f"https://duckduckgo.com/?q={q}&iax=images&ia=images"
                      ).decode("utf-8", "replace")
        m = re.search(r'vqd=["\']?([\d-]+)', page)
        if not m:
            warnings.append("ddg: no vqd token")
            return []
        data = _fetch(f"https://duckduckgo.com/i.js?l=us-en&o=json&q={q}"
                      f"&vqd={m.group(1)}&f=,,,&p=1")
        results = json.loads(data).get("results", [])
        return [r["image"] for r in results if r.get("image")]
    except Exception as e:  # noqa: BLE001
        warnings.append(f"ddg search failed: {e}")
        return []


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def _download(candidates, slug, count, warnings):
    os.makedirs(CACHE, exist_ok=True)
    out, seen = [], set()
    for u in candidates:
        if len(out) >= count:
            break
        u = u.replace("\\/", "/")
        if u in seen:
            continue
        seen.add(u)
        try:
            data = _fetch(u, timeout=12)
        except Exception:  # noqa: BLE001
            continue
        if len(data) < MIN_BYTES:
            continue
        ext = next((e for m, e in MAGIC.items() if data.startswith(m)), None)
        if not ext:
            continue
        path = os.path.join(CACHE, f"{slug}-{len(out) + 1}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        out.append(path)
    if not out:
        warnings.append("no downloadable images found")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("query", help='place + city, e.g. "River City Bagels Sacramento"')
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--url", default=None, help="the place's website/listing (og:image)")
    args = p.parse_args()

    warnings, candidates = [], []
    if args.url:
        candidates += _og_images(args.url, warnings)
    candidates += _ddg_images(args.query, warnings)
    images = _download(candidates, _slug(args.query), args.count, warnings)
    print(json.dumps({"query": args.query, "images": images,
                      "warnings": warnings}))


if __name__ == "__main__":
    main()
