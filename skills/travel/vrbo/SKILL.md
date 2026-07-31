---
name: vrbo
description: Search VRBO (Vrbo / Expedia Group) vacation rentals including entire homes, condos, and cabins via Patchright browser automation. VRBO sits behind Akamai Bot Manager so plain HTTP and standard Playwright get a 429 bot wall. Use for whole-home or group stays, multi-bedroom rentals, and Airbnb-vs-VRBO comparisons. Trigger phrases include search VRBO, Vrbo rentals, vacation rental, cabin rental, whole house, condo for the group.
category: hotels
summary: VRBO whole-home, condo, and cabin search via Patchright. Complements Airbnb for group stays.
api_key: None (requires Patchright)
docker_image: ghcr.io/borski/vrbo
---

# VRBO Search

Search VRBO vacation rentals via Patchright and return whole-home / condo / cabin
listings with pricing and booking links. VRBO is the natural complement to the
Airbnb MCP for group and family stays — many mountain-town and resort property
managers cross-list on both, but VRBO carries professionally-managed condos and
cabins that don't always appear on Airbnb.

**Requires Patchright** (undetected Playwright fork). VRBO sits behind **Akamai
Bot Manager** — plain HTTP, standard Playwright, `@playwright/mcp`, and
agent-browser all get a `429 Too Many Requests` ("Provisioned request rate has
been exceeded") bot wall. Patchright passes Akamai's sensor and earns the
`_abck` cookie. This is the same wall — and the same fix — as the chase-travel /
amex-travel / southwest skills.

**Must run headed (`headless=False`).** Akamai flags headless browsers. On macOS
a Chrome window briefly appears. For background / unattended runs, use Docker
(Xvfb provides the virtual display).

**No login required.** VRBO search results are public — unlike chase-travel /
amex-travel, there are no credentials. The persistent profile only caches the
Akamai cookie so repeat searches re-challenge less.

## Prerequisites

```bash
pip install patchright && patchright install chromium
```

Or use Docker (no local install, headed via Xvfb):

```bash
docker build -t vrbo skills/vrbo/
```

## When to Use

- Group / family stays where you want a whole home, condo, or cabin
- "Compare Airbnb vs VRBO in <place>"
- Multi-bedroom rentals with separate rooms (the multi-person trip pattern)
- Resort / mountain towns (Canmore, Banff-area, Whistler, Muskoka) where
  professional property managers list condos on VRBO that aren't on Airbnb

## When NOT to Use

- **Completing a booking.** This skill finds rentals and returns links only. Do
  not attempt to complete a purchase.
- **Hotels.** Use compare-hotels / serpapi / liteapi / the portal skills.
- **Single rooms / shared rooms.** VRBO is whole-property only; for private
  rooms in a shared home use the Airbnb MCP (`private_room`).

## Usage

```bash
# Local (pops up a Chrome window on macOS)
python3 skills/vrbo/scripts/search_vrbo.py \
  --dest "Canmore, Alberta, Canada" \
  --checkin 2026-06-26 --checkout 2026-07-02 --adults 3 --json

# Whole homes with 3+ bedrooms
python3 skills/vrbo/scripts/search_vrbo.py \
  --dest "Canmore, Alberta" --checkin 2026-06-26 --checkout 2026-07-02 \
  --adults 3 --min-bedrooms 3 --json

# Docker (headless host, headed browser via Xvfb)
docker run --rm vrbo \
  --dest "Canmore, Alberta" --checkin 2026-06-26 --checkout 2026-07-02 \
  --adults 3 --json
```

### Arguments

| Flag | Default | Notes |
|------|---------|-------|
| `--dest` | required | Destination keyword, e.g. `"Canmore, Alberta, Canada"`. VRBO resolves it to a regionId server-side. |
| `--checkin` / `--checkout` | required | ISO `YYYY-MM-DD`. Must be today or later. |
| `--adults` | 2 | Traveler count. |
| `--children` | 0 | |
| `--pets` | off | Pet-friendly only. |
| `--min-bedrooms` | 0 | Client-side filter (VRBO's URL filter is unreliable). |
| `--limit` | 20 | Max listings returned. |
| `--retries` | 4 | Akamai retry attempts (homepage warm-up → SERP). |
| `--json` | off | Emit JSON to stdout (use this for orchestration). |

## Output

JSON shape (with `--json`):

```json
{
  "source": "vrbo",
  "extractor": "__PLUS_REDUX_STORE__ | dom:lodging-card | dom:anchors",
  "searchUrl": "https://www.vrbo.com/search?...",
  "count": 12,
  "listings": [
    {
      "id": "1234567",
      "name": "Mountain-View Condo Steps from Main St",
      "bedrooms": 3, "bathrooms": 2, "sleeps": 6,
      "rating": 4.9, "reviewCount": 84,
      "priceText": "The current price is CA $312 CA $312 CA $2,184 total includes taxes & fees",
      "pricePerNight": 312, "priceTotal": 2184, "priceIncludesTaxes": true,
      "lat": 51.08, "lng": -115.35,
      "url": "https://www.vrbo.com/en-ca/cottage-rental/p1234567?..."
    }
  ]
}
```

`extractor` tells you which layer produced the data. `__PLUS_REDUX_STORE__` is
richest (bedrooms, coords, review counts); `dom:lodging-card` is reliable but
thinner (the common case — names, prices, URLs); `dom:anchors` is a last-ditch
id+url scrape — if you see that, the page shape changed and selectors need a
refresh (file a P2 task).

**Prices:** the SERP price string is parsed into `pricePerNight` and
`priceTotal`. Verified against live data: VRBO's SERP **total includes taxes &
fees** (`priceIncludesTaxes: true`) — `priceTotal` is the all-in for the whole
stay, `pricePerNight` is the lead nightly rate. This satisfies the
hotel-comparison standard's all-in requirement directly; still open the listing
URL to confirm before booking, since a damage deposit or optional add-ons may
not be in the SERP figure.

## Tests

The pure logic (search-URL building, Akamai block/challenge detection, SERP
price parsing, skeleton filtering, listing enrichment) is covered by stdlib
`unittest` tests — no browser, network, or Patchright needed (the `patchright`
import is lazy):

```bash
python3 -m unittest discover -s skills/vrbo/tests -v   # or: python3 -m pytest skills/vrbo/tests
```

17 tests, no third-party deps. The browser-driven path (Akamai bypass, DOM
extraction) can't be unit-tested without a live residential connection — verify
it manually with a real search (see Usage above). The repo smoke test
(`scripts/smoke-test.sh`) separately validates this skill's frontmatter and
structure.

## Known fragility

- **Akamai is IP-rate-limited.** Datacenter / CI / VPN IPs are frequently
  blocked even with Patchright. Run on a residential connection for best
  results. If every retry 429s, the script exits 1 with a clear message — that
  is an environment block, not a code bug. In practice a home/mobile/hotel-WiFi
  IP clears the wall, but *rapid repeat* searches from the same IP can start
  429-ing — space searches a few minutes apart, and don't loop it in a tight
  batch.
- **DOM selectors drift.** VRBO/Expedia revise their `data-stid` design system
  periodically. The extractor tries the embedded state blob first (most stable),
  then `data-stid` cards, then a generic anchor scrape. If `extractor` is
  `dom:anchors`, refresh the Layer-1/2 selectors.
