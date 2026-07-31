#!/usr/bin/env python3
"""
Sutochno.ru (СУТОЧНО.РУ) Russian apartment-rental search via Patchright.

Sutochno is a Russian short-term-rental platform where you book apartments,
rooms, and houses directly from owners and pay in rubles (typically via Mir
card / bank transfer). It is the natural fit for Russian-speaking travellers
booking stays in Georgia / the CIS, where Airbnb and Booking have withdrawn or
don't accept Russian cards.

This skill ships on the toolkit's standard `patchright-docker` base (the same
image every browser skill builds on). Patchright is a drop-in Playwright fork,
so the browser API is identical.

Two findings drive this scraper's design (both proven against live Sutochno):

  1. NOT behind Akamai. Unlike the vrbo skill (which fights an Akamai bot wall
     and must run headed), Sutochno's server-rendered city pages load fine
     *headless* — no Akamai sensor to defeat. It rides the shared patchright
     base purely for the bundled chromium + OS deps, not for bot evasion.

  2. The JSON API is auth-walled. `https://sutochno.ru/api/geo/suggest` (and the
     other `/api/*` endpoints the SPA calls) return 403 "Application
     authentication failed" without an app token the SPA computes client-side.
     So we do NOT touch the API. Instead we scrape the server-rendered city
     pages, where the listing cards are already in the HTML.

City results live on SUBDOMAINS, one per city:
    https://tbilisi.sutochno.ru/      https://batumi.sutochno.ru/    etc.
The country directory `https://sutochno.ru/georgia` lists the supported Georgian
cities and links out to `https://{city}.sutochno.ru/<listingId>` cards.

Pass a city subdomain (e.g. `tbilisi`, `batumi`, `kutaisi`) with `--city`, or a
full city URL with `--url`. The scraper navigates to the city page, waits for the
listing cards, and extracts name / price (rubles/night) / rating / reviews /
address / url for each card, deduped by listing id.

Usage:
    # Tbilisi, human-readable table
    python3 search_sutochno.py --city tbilisi

    # Batumi, JSON, only 9.0+ rated, under 8000 rubles/night, top 15
    python3 search_sutochno.py --city batumi \
        --min-rating 9.0 --max-price 8000 --limit 15 --json

    # Explicit full URL (e.g. a city page with query filters already applied)
    python3 search_sutochno.py --url "https://kutaisi.sutochno.ru/" --json

Environment:
    SUTOCHNO_IN_DOCKER  Set to 1 by the Docker image (informational only).

Exit codes:
    0  results found (or empty result set with no navigation error)
    1  navigation / scrape failure
    2  bad arguments
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


BASE_DOMAIN = "sutochno.ru"


def in_docker():
    return Path("/.dockerenv").exists() or os.environ.get("SUTOCHNO_IN_DOCKER") == "1"


def build_city_url(city):
    """Turn a city subdomain keyword into its Sutochno city URL.

    `tbilisi` -> https://tbilisi.sutochno.ru/
    A value that already looks like a host is normalised; a bare subdomain is
    prefixed onto the base domain.
    """
    city = (city or "").strip().lower()
    if not city:
        raise ValueError("city must be a non-empty subdomain keyword")
    # Strip any scheme / trailing path the caller may have passed.
    city = re.sub(r"^https?://", "", city)
    city = city.split("/")[0]
    if city.endswith("." + BASE_DOMAIN):
        host = city
    elif city == BASE_DOMAIN:
        host = BASE_DOMAIN
    else:
        # bare subdomain like "tbilisi"
        host = f"{city}.{BASE_DOMAIN}"
    return f"https://{host}/"


def parse_price(price_text):
    """Extract per-night rubles from a Sutochno price string.

    The SERP price reads e.g. "4 890 ₽ за сутки" — note the thousands separator
    is a mixed-width / non-breaking space, so a naive `\\d+` regex truncates the
    leading "4" and returns 890. The reliable method: take the substring BEFORE
    the "₽", then strip ALL non-digits.

        "4 890 ₽ за сутки"  -> 4890
        "12 500 ₽ за сутки" -> 12500

    Returns an int, or None when no ruble amount is present.
    """
    if not price_text:
        return None
    if "₽" not in price_text:
        return None
    head = price_text.split("₽", 1)[0]
    digits = re.sub(r"\D", "", head)
    return int(digits) if digits else None


def parse_rating(rating_text):
    """Extract (rating, review_count) from a Sutochno rating string.

    The SERP rating reads e.g. "10 отзывов 9,8 (10) ..." — Russian uses a comma
    decimal, so rating = first /\\d{1,2},\\d/ match (comma -> dot), and the review
    count = the number before "отзыв".

        "10 отзывов 9,8 (10)" -> (9.8, 10)
        "1 отзыв 8,5"         -> (8.5, 1)

    Returns (rating|None, reviews|None). Either may be None if absent.
    """
    rating = None
    reviews = None
    if not rating_text:
        return (rating, reviews)
    m_rating = re.search(r"\d{1,2},\d", rating_text)
    if m_rating:
        rating = float(m_rating.group(0).replace(",", "."))
    m_reviews = re.search(r"(\d+)\s*отзыв", rating_text)
    if m_reviews:
        reviews = int(m_reviews.group(1))
    return (rating, reviews)


def listing_id_from_url(url):
    """Pull the numeric listing id from a Sutochno card href.

    `https://tbilisi.sutochno.ru/1234567`            -> "1234567"
    `https://tbilisi.sutochno.ru/hotels/9876543?x=1` -> "9876543"
    """
    if not url:
        return None
    path = url.split("?", 1)[0].rstrip("/")
    m = re.search(r"(\d{4,})$", path)
    return m.group(1) if m else None


def normalize_listing(raw):
    """Turn a raw scraped card dict into the canonical listing shape.

    `raw` carries the raw strings the page exposed: name, href, priceText,
    ratingText, area. Pure (no browser) so it is unit-testable.
    """
    url = raw.get("href")
    rating, reviews = parse_rating(raw.get("ratingText"))
    return {
        "id": listing_id_from_url(url),
        "name": (raw.get("name") or "").strip() or None,
        "area": (raw.get("area") or "").strip() or None,
        "rub_per_night": parse_price(raw.get("priceText")),
        "rating": rating,
        "reviews": reviews,
        "url": url,
    }


def is_skeleton(listing):
    """A card with no id, name, or url is a placeholder/sponsored skeleton."""
    return not (listing.get("id") or listing.get("name") or listing.get("url"))


def enrich_listings(raw_cards, min_rating=0.0, max_price=0, limit=0):
    """Normalise, dedupe by id, filter, and sort raw scraped cards. Pure.

    Sort order: rating desc (None last), then price asc (None last).
    """
    listings = [normalize_listing(c) for c in raw_cards]
    listings = [l for l in listings if not is_skeleton(l)]

    # Dedupe by id (keep first occurrence); cards without an id are kept as-is.
    seen = set()
    deduped = []
    for l in listings:
        lid = l.get("id")
        if lid is not None:
            if lid in seen:
                continue
            seen.add(lid)
        deduped.append(l)
    listings = deduped

    if min_rating:
        listings = [
            l for l in listings
            if l.get("rating") is None or l["rating"] >= min_rating
        ]
    if max_price:
        listings = [
            l for l in listings
            if l.get("rub_per_night") is None or l["rub_per_night"] <= max_price
        ]

    def sort_key(l):
        rating = l.get("rating")
        price = l.get("rub_per_night")
        # rating desc → negate; None sorts last via the (is_none) flag.
        return (
            rating is None, -(rating or 0.0),
            price is None, (price or 0),
        )

    listings.sort(key=sort_key)

    if limit:
        listings = listings[:limit]
    return listings


# ============================================================
# Extraction
# ============================================================

# Runs in the page context. On a Sutochno city page each listing card exposes:
#   * name + link:  .card-content__object-type  (an <a>; href -> the card url)
#   * price:        .card-prices__price          ("4 890 ₽ за сутки")
#   * rating:       .card-prices__rating         ("10 отзывов 9,8 (10) ...")
#   * address:      .card-content__address
# The card wrapper is the NEAREST ancestor of the name node that ALSO contains a
# `.card-prices` element (name and prices are siblings in the card, not nested),
# so we climb up from the name link until `el.querySelector('.card-prices')` is
# truthy, then read the price/rating from inside that wrapper.
# Images are lazy-loaded placeholders (lzy-img.gif) on first paint, so we do not
# rely on photos.
EXTRACT_JS = r"""
() => {
  const out = { cards: [] };
  const nameEls = Array.from(document.querySelectorAll('.card-content__object-type'));
  for (const nameEl of nameEls) {
    // Resolve the href: the name element is (or contains) the card anchor.
    let a = nameEl.matches && nameEl.matches('a[href]') ? nameEl : nameEl.querySelector('a[href]');
    if (!a) a = nameEl.closest('a[href]');
    let href = a ? a.getAttribute('href') : null;
    if (href && !href.startsWith('http')) {
      href = (location.origin + (href.startsWith('/') ? '' : '/') + href);
    }

    // Climb to the card wrapper: nearest ancestor that holds a .card-prices.
    let wrap = nameEl;
    for (let i = 0; i < 8 && wrap; i++) {
      if (wrap.querySelector && wrap.querySelector('.card-prices')) break;
      wrap = wrap.parentElement;
    }
    if (!wrap) wrap = nameEl.parentElement;

    const priceEl = wrap ? wrap.querySelector('.card-prices__price') : null;
    const ratingEl = wrap ? wrap.querySelector('.card-prices__rating') : null;
    const addrEl = wrap ? wrap.querySelector('.card-content__address') : null;

    out.cards.push({
      name: (nameEl.innerText || '').replace(/\s+/g, ' ').trim() || null,
      href: href || null,
      priceText: priceEl ? (priceEl.innerText || '').replace(/\s+/g, ' ').trim() : null,
      ratingText: ratingEl ? (ratingEl.innerText || '').replace(/\s+/g, ' ').trim() : null,
      area: addrEl ? (addrEl.innerText || '').replace(/\s+/g, ' ').trim() : null,
    });
  }
  return out;
}
"""


def search(args):
    from patchright.sync_api import sync_playwright

    if args.url:
        url = args.url
    else:
        url = build_city_url(args.city)

    raw_cards = []
    final_url = url

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = browser.new_context(
                viewport={"width": 1400, "height": 900},
                locale="ru-RU",
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                browser.close()
                print(f"ERROR: navigation to {url} failed: {e}", file=sys.stderr)
                sys.exit(1)

            # Wait for the first listing card to render. Sutochno is server-
            # rendered, so this is fast and reliable (no bot wall).
            try:
                page.wait_for_selector(".card-content__object-type", timeout=25000)
            except Exception:
                # No cards — could be an empty city or a layout change. Scroll
                # once in case of lazy render, then proceed to extract (may be 0).
                page.mouse.wheel(0, 3000)
                time.sleep(1.5)

            # Cards lazy-load as you scroll; nudge a couple of times to surface more.
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                time.sleep(1.0)

            data = page.evaluate(EXTRACT_JS)
            raw_cards = data.get("cards", [])
            final_url = page.url
        finally:
            browser.close()

    listings = enrich_listings(
        raw_cards,
        min_rating=args.min_rating,
        max_price=args.max_price,
        limit=args.limit,
    )

    return {
        "source": "sutochno",
        "searchUrl": final_url,
        "query": {
            "city": args.city,
            "url": args.url,
            "min_rating": args.min_rating,
            "max_price": args.max_price,
        },
        "count": len(listings),
        "listings": listings,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Sutochno.ru Russian apartment-rental search (Patchright)"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--city", help='City subdomain keyword, e.g. "tbilisi", "batumi"')
    g.add_argument("--url", help="Full city URL, e.g. https://tbilisi.sutochno.ru/")
    ap.add_argument("--min-rating", type=float, default=0.0, dest="min_rating",
                    help="Minimum rating (0–10 scale), e.g. 9.0")
    ap.add_argument("--max-price", type=int, default=0, dest="max_price",
                    help="Maximum price in rubles/night")
    ap.add_argument("--limit", type=int, default=20, help="Max listings to return (default 20)")
    ap.add_argument("--headed", action="store_true",
                    help="Run the browser headed (default headless — Sutochno has no bot wall)")
    ap.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    # --city must be a usable subdomain; validate early for a clean exit 2.
    if args.city is not None:
        try:
            build_city_url(args.city)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)

    result = search(args)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        where = args.city or args.url
        print(f"Sutochno — {result['count']} listings  ({where})", file=sys.stderr)
        print(f"  {result['searchUrl']}\n", file=sys.stderr)
        for l in result["listings"]:
            print(f"• {l.get('name') or l.get('id')}")
            if l.get("rub_per_night") is not None:
                print(f"    {l['rub_per_night']} ₽ / night")
            if l.get("rating") is not None:
                rev = f" ({l['reviews']} reviews)" if l.get("reviews") else ""
                print(f"    rating {l['rating']}{rev}")
            if l.get("area"):
                print(f"    {l['area']}")
            if l.get("url"):
                print(f"    {l['url']}")


if __name__ == "__main__":
    main()
