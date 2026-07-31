#!/usr/bin/env python3
"""
VRBO (Vrbo / Expedia Group) vacation-rental search via Patchright.

VRBO sits behind Akamai Bot Manager ("Provisioned request rate has been
exceeded" / obfuscated sensor script). Standard Playwright, agent-browser, and
plain HTTP all get a 429 bot wall. Patchright (undetected Playwright fork),
running *headed* with a persistent profile, passes Akamai's sensor and earns
the `_abck` cookie — the same approach the chase-travel / amex-travel /
southwest skills use for their bot walls.

No login is required: VRBO search results are public. The persistent profile
just lets the Akamai cookie survive between runs so repeat searches are faster
and less likely to re-challenge.

Usage:
    # Local (pops up a Chrome window on macOS)
    python3 search_vrbo.py --dest "Canmore, Alberta, Canada" \
        --checkin 2026-06-26 --checkout 2026-07-02 --adults 3

    # Filter to whole homes only, set a price ceiling (per-night, listing currency)
    python3 search_vrbo.py --dest "Canmore, Alberta" \
        --checkin 2026-06-26 --checkout 2026-07-02 --adults 3 \
        --min-bedrooms 3 --max-price 400

    # JSON output (for orchestration by compare-hotels / trip-planner)
    python3 search_vrbo.py --dest "Canmore, Alberta" \
        --checkin 2026-06-26 --checkout 2026-07-02 --adults 3 --json

Environment:
    VRBO_PROFILE   Profile directory (default: ~/.vrbo-profiles/default).
                   In Docker, a tmp profile is used automatically.

Exit codes:
    0  results found (or empty result set with no error)
    1  blocked by Akamai after retries / navigation failure
    2  bad arguments
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


SEARCH_URL = "https://www.vrbo.com/search"
HOME_URL = "https://www.vrbo.com/"

# Akamai serves two flavours of block:
#   1. Hard 429 page — title/body carry these strings.
#   2. Soft interstitial challenge (Press & Hold / sensor / "verify you are
#      human") — returns 200 with no listings, so we must detect it explicitly,
#      otherwise it looks like an empty result set.
BLOCK_MARKERS = (
    "Too Many Requests",
    "Provisioned request rate has been exceeded",
    "Access Denied",
    "verify you are human",
    "Press & Hold",
    "px-captcha",
    "Pardon Our Interruption",
)


def in_docker():
    return Path("/.dockerenv").exists() or os.environ.get("VRBO_IN_DOCKER") == "1"


def get_profile_dir():
    if in_docker():
        return tempfile.mkdtemp(prefix="vrbo-")
    env_dir = os.environ.get("VRBO_PROFILE")
    if env_dir:
        return env_dir
    return str(Path.home() / ".vrbo-profiles" / "default")


def build_search_url(dest, checkin, checkout, adults, children, pets):
    # VRBO accepts a destination keyword plus ISO dates and traveler counts.
    # It resolves the keyword to a regionId server-side and 302s to the canonical
    # SERP, so we don't need to pre-resolve the region.
    params = [
        ("destination", dest),
        ("startDate", checkin),
        ("endDate", checkout),
        ("d1", checkin),
        ("d2", checkout),
        ("adults", str(adults)),
    ]
    if children:
        params.append(("children", str(children)))
    if pets:
        params.append(("petIncluded", "true"))
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params)
    return f"{SEARCH_URL}?{query}"


def text_has_block_marker(text):
    """True if `text` contains any Akamai block/challenge marker (case-insensitive)."""
    if not text:
        return False
    low = text.lower()
    return any(marker.lower() in low for marker in BLOCK_MARKERS)


def parse_price(price_text):
    """Parse VRBO's SERP price string into structured fields.

    VRBO renders e.g. "The current price is CA $867 CA $867 CA $6,841 total
    includes taxes & fees" — the smallest amount is the per-night lead price,
    the largest is the all-in total (taxes + fees INCLUDED on the SERP).
    """
    result = {"pricePerNight": None, "priceTotal": None, "priceIncludesTaxes": None}
    if not price_text:
        return result
    amounts = re.findall(r"\$\s?([\d,]+)", price_text)
    nums = [int(a.replace(",", "")) for a in amounts if a.replace(",", "").isdigit()]
    if nums:
        result["pricePerNight"] = min(nums)
        result["priceTotal"] = max(nums)
        result["priceIncludesTaxes"] = "taxes" in price_text.lower()
    return result


def is_skeleton(listing):
    """A card with no id, name, or url is a placeholder/sponsored skeleton."""
    return not (listing.get("id") or listing.get("name") or listing.get("url"))


def clean_name(name):
    """Strip VRBO's "Photo gallery for " screen-reader prefix.

    When the title is scraped from a card's image-carousel <h3> (its
    accessibility label) instead of the listing-title element, the name comes
    back as "Photo gallery for <real title>". The real title is everything
    after the prefix, so strip it. No-op for already-clean names.
    """
    if not name:
        return name
    return re.sub(r"^\s*Photo gallery for\s+", "", name, flags=re.IGNORECASE).strip()


def enrich_listings(listings, min_bedrooms=0, limit=0):
    """Filter skeletons, parse prices, apply client-side filters. Pure (no browser)."""
    out = [l for l in listings if not is_skeleton(l)]
    for l in out:
        l.update(parse_price(l.get("priceText")))
        if l.get("name"):
            l["name"] = clean_name(l["name"])
    if min_bedrooms:
        out = [
            l for l in out
            if (l.get("bedrooms") is None) or (l.get("bedrooms") and l["bedrooms"] >= min_bedrooms)
        ]
    if limit:
        out = out[:limit]
    return out


def is_blocked(page):
    title = (page.title() or "")
    if text_has_block_marker(title):
        return True
    # Akamai sometimes returns 200 with the block/challenge text in the body.
    try:
        body = page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
        return text_has_block_marker(body)
    except Exception:
        return False


# ============================================================
# Extraction
# ============================================================

# Runs in the page context. VRBO/Expedia share the "Lodging" design system, so
# listing cards are tagged with stable data-stid attributes. We try three layers,
# most-structured first, and stop at the first that yields rows:
#   1. The embedded Apollo/Redux state blob (richest: ids, coords, review counts)
#   2. data-stid="lodging-card-responsive" DOM cards (reliable, less rich)
#   3. Generic anchor scrape to /<region>/<propertyId> (last-ditch)
EXTRACT_JS = r"""
() => {
  const out = { source: null, listings: [] };

  // --- Layer 1: embedded state blob ----------------------------------------
  const stateKeys = ['__PLUS_REDUX_STORE__', '__APOLLO_STATE__', '__INITIAL_STATE__'];
  for (const k of stateKeys) {
    const blob = window[k];
    if (!blob) continue;
    try {
      const text = typeof blob === 'string' ? blob : JSON.stringify(blob);
      // Property listings carry a propertyId + a unitName/headline. Pull the
      // objects rather than guessing the exact tree shape, which VRBO changes.
      const flat = typeof blob === 'string' ? JSON.parse(blob) : blob;
      const found = [];
      const walk = (node) => {
        if (!node || typeof node !== 'object') return;
        const id = node.propertyId || node.listingId || node.id;
        const name = node.headline || node.unitName || node.name || node.title;
        const priceObj = node.price || node.priceSummary || node.lead || node.displayPrice;
        if (id && name && priceObj) {
          found.push({
            id: String(id),
            name: String(name),
            bedrooms: node.bedrooms ?? node.bedroomCount ?? null,
            bathrooms: node.bathrooms ?? node.bathroomCount ?? null,
            sleeps: node.sleeps ?? node.maxOccupancy ?? null,
            rating: node.averageRating ?? node.reviewScore ?? null,
            reviewCount: node.reviewCount ?? node.totalReviews ?? null,
            priceText: typeof priceObj === 'string'
              ? priceObj
              : (priceObj.formatted || priceObj.amount || priceObj.lead || JSON.stringify(priceObj)).toString(),
            lat: node.latitude ?? node.lat ?? (node.geoLocation && node.geoLocation.latitude) ?? null,
            lng: node.longitude ?? node.lng ?? (node.geoLocation && node.geoLocation.longitude) ?? null,
            url: id ? `https://www.vrbo.com/${String(id)}` : null,
          });
        }
        for (const v of Object.values(node)) {
          if (v && typeof v === 'object') walk(v);
        }
      };
      walk(flat);
      // Dedupe by id.
      const seen = new Set();
      for (const r of found) {
        if (seen.has(r.id)) continue;
        seen.add(r.id);
        out.listings.push(r);
      }
      if (out.listings.length) { out.source = k; return out; }
    } catch (e) { /* fall through to DOM */ }
  }

  // --- Layer 2: data-stid cards --------------------------------------------
  const cards = document.querySelectorAll('[data-stid="lodging-card-responsive"], [data-stid="property-listing-results"] [data-stid*="card"]');
  if (cards.length) {
    out.source = 'dom:lodging-card';
    cards.forEach((card) => {
      const a = card.querySelector('a[href]');
      const href = a ? a.getAttribute('href') : null;
      if (!href) return;  // skeleton / sponsored placeholder — skip
      // Prefer the real title element; querySelector on a comma-list returns
      // the first match in DOM order, and the gallery's h3 ("Photo gallery
      // for …") precedes the title, so query the title data-stid explicitly
      // first, then fall back to h3/h4. Python clean_name() strips any residual
      // "Photo gallery for " prefix as a safety net.
      const titleEl = card.querySelector('[data-stid="content-hotel-title"]') || card.querySelector('h3, h4');
      const name = titleEl ? titleEl.innerText : null;
      const priceEl = card.querySelector('[data-stid="price-summary"], [data-test-id="price-summary"], [class*="price"]');
      const priceText = priceEl ? priceEl.innerText.replace(/\s+/g, ' ').trim() : null;
      const ratingEl = card.querySelector('[aria-label*="out of"], [data-stid*="rating"]');
      out.listings.push({
        id: href ? (href.split('?')[0].split('/').filter(Boolean).pop() || null) : null,
        name: name ? name.trim() : null,
        priceText,
        rating: ratingEl ? (ratingEl.getAttribute('aria-label') || ratingEl.innerText) : null,
        url: href ? (href.startsWith('http') ? href : `https://www.vrbo.com${href}`) : null,
      });
    });
    if (out.listings.length) return out;
  }

  // --- Layer 3: anchor scrape ----------------------------------------------
  const anchors = Array.from(document.querySelectorAll('a[href*="/"]'))
    .map(a => a.getAttribute('href'))
    .filter(h => h && /^\/?\d{5,}/.test(h.replace('https://www.vrbo.com', '')));
  if (anchors.length) {
    out.source = 'dom:anchors';
    const seen = new Set();
    anchors.forEach(h => {
      const id = h.split('?')[0].split('/').filter(Boolean).pop();
      if (seen.has(id)) return;
      seen.add(id);
      out.listings.push({ id, name: null, priceText: null, url: h.startsWith('http') ? h : `https://www.vrbo.com${h}` });
    });
  }
  return out;
}
"""


def search(args):
    profile_dir = get_profile_dir()
    os.makedirs(profile_dir, exist_ok=True)

    from patchright.sync_api import sync_playwright

    url = build_search_url(
        args.dest, args.checkin, args.checkout, args.adults, args.children, args.pets
    )

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel="chrome" if not in_docker() else None,
                headless=False,  # Akamai detects headless — must run headed (Xvfb in Docker)
                viewport={"width": 1400, "height": 900},
                locale="en-CA",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                viewport={"width": 1400, "height": 900},
                locale="en-CA",
                args=["--disable-blink-features=AutomationControlled"],
            )

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Warm the Akamai cookie on the homepage first, then hit the SERP.
        # Retry the pair a few times — the first sensor round often 429s, then
        # clears once _abck is minted.
        blocked = True
        for attempt in range(1, args.retries + 1):
            try:
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
                time.sleep(2.5)
                if is_blocked(page):
                    print(f"[attempt {attempt}] homepage 429, retrying…", file=sys.stderr)
                    time.sleep(4 * attempt)
                    continue
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # Wait for either listing cards or the block page.
                try:
                    page.wait_for_selector(
                        '[data-stid="lodging-card-responsive"], [data-stid="property-listing-results"]',
                        timeout=20000,
                    )
                except Exception:
                    pass
                time.sleep(2.0)
                if is_blocked(page):
                    print(f"[attempt {attempt}] SERP challenge/429, retrying…", file=sys.stderr)
                    time.sleep(4 * attempt)
                    continue
                # Cards lazy-load — scroll to trigger render, then poll a few times.
                cards_present = False
                for _ in range(4):
                    n = page.evaluate(
                        "() => document.querySelectorAll('[data-stid=\"lodging-card-responsive\"]').length"
                    )
                    if n:
                        cards_present = True
                        break
                    page.mouse.wheel(0, 4000)
                    time.sleep(1.5)
                if not cards_present:
                    print(f"[attempt {attempt}] no cards rendered (soft challenge?), retrying…", file=sys.stderr)
                    time.sleep(4 * attempt)
                    continue
                blocked = False
                break
            except Exception as e:
                print(f"[attempt {attempt}] navigation error: {e}", file=sys.stderr)
                time.sleep(3 * attempt)

        if blocked:
            ctx.close()
            print(
                "ERROR: VRBO blocked by Akamai after retries. Run headed on a "
                "residential connection, or retry later — the bot wall is "
                "IP-rate-limited. (Datacenter / CI IPs are frequently blocked.)",
                file=sys.stderr,
            )
            sys.exit(1)

        data = page.evaluate(EXTRACT_JS)
        final_url = page.url
        ctx.close()

    # Filter skeletons, parse prices, apply client-side filters (pure helper).
    listings = enrich_listings(
        data.get("listings", []), min_bedrooms=args.min_bedrooms, limit=args.limit
    )

    result = {
        "source": "vrbo",
        "extractor": data.get("source"),
        "searchUrl": final_url,
        "query": {
            "destination": args.dest,
            "checkin": args.checkin,
            "checkout": args.checkout,
            "adults": args.adults,
            "children": args.children,
            "pets": args.pets,
        },
        "count": len(listings),
        "listings": listings,
    }
    return result


def main():
    ap = argparse.ArgumentParser(description="VRBO vacation-rental search via Patchright")
    ap.add_argument("--dest", required=True, help='Destination keyword, e.g. "Canmore, Alberta, Canada"')
    ap.add_argument("--checkin", required=True, help="Check-in date YYYY-MM-DD")
    ap.add_argument("--checkout", required=True, help="Check-out date YYYY-MM-DD")
    ap.add_argument("--adults", type=int, default=2, help="Number of adults (default 2)")
    ap.add_argument("--children", type=int, default=0, help="Number of children")
    ap.add_argument("--pets", action="store_true", help="Pet-friendly only")
    ap.add_argument("--min-bedrooms", type=int, default=0, help="Minimum bedrooms")
    ap.add_argument("--max-price", type=float, default=0, help="(informational) per-night ceiling for the agent to apply")
    ap.add_argument("--limit", type=int, default=20, help="Max listings to return (default 20)")
    ap.add_argument("--retries", type=int, default=4, help="Akamai retry attempts (default 4)")
    ap.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    result = search(args)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"VRBO — {result['count']} listings  ({result['extractor']})", file=sys.stderr)
        print(f"  {result['searchUrl']}\n", file=sys.stderr)
        for l in result["listings"]:
            bedbath = ""
            if l.get("bedrooms"):
                bedbath = f"  {l['bedrooms']}BR"
                if l.get("bathrooms"):
                    bedbath += f"/{l['bathrooms']}BA"
            print(f"• {l.get('name') or l.get('id')}{bedbath}")
            if l.get("priceTotal"):
                tax = " incl. taxes & fees" if l.get("priceIncludesTaxes") else ""
                per = f"  (~${l['pricePerNight']}/night)" if l.get("pricePerNight") else ""
                print(f"    ${l['priceTotal']} total{tax}{per}")
            elif l.get("priceText"):
                print(f"    {l['priceText']}")
            if l.get("rating"):
                print(f"    {l['rating']}")
            if l.get("url"):
                print(f"    {l['url']}")


if __name__ == "__main__":
    main()
