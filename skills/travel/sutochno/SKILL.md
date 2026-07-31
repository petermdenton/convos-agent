---
name: sutochno
description: Search Sutochno.ru (СУТОЧНО.РУ), a Russian short-term apartment-rental platform where you book directly from owners and pay in rubles (Mir card / bank transfer). Best for Russian-speaking travellers booking stays in Georgia / the CIS, where Airbnb and Booking have withdrawn or do not accept Russian cards. Sutochno is NOT behind a bot wall — it ships on the toolkit standard patchright-docker base but runs the bundled chromium headless with no Akamai sensor to defeat. Trigger phrases include search Sutochno, суточно, Russian apartment rental, ruble payment, Mir card, Tbilisi apartment, Batumi apartment, Georgia CIS rental.
category: hotels
summary: Sutochno.ru Russian apartment search via Patchright. Ruble / Mir payment for Georgia / CIS stays.
api_key: None
docker_image: ghcr.io/borski/sutochno
---

# Sutochno Search

Search **Sutochno.ru** (СУТОЧНО.РУ) — a Russian short-term-rental marketplace
where you book apartments, rooms, and houses directly from owners and pay in
**rubles**, typically via **Mir card** or bank transfer. It's the natural fit
for Russian-speaking travellers booking stays in **Georgia / the CIS**, where
Airbnb and Booking have withdrawn or won't take Russian cards. It complements
the Airbnb MCP and the `vrbo` skill: those reach Western inventory, Sutochno
reaches the ruble-priced, owner-direct inventory those platforms miss.

**Sutochno is NOT behind Akamai.** Unlike the `vrbo` skill (which fights an
Akamai bot wall and must run **headed**), Sutochno's server-rendered city pages
load fine **headless** — there is no bot sensor to defeat. This skill still ships
on the toolkit's standard **patchright-docker** base (the same image every
browser skill builds on), but only for the bundled chromium + OS deps; it does
not need Patchright's stealth. Patchright is a drop-in Playwright fork, so the
browser API is identical. The default mode is headless; `--headed` is opt-in for
debugging.

**The JSON API is auth-walled — we scrape pages instead.** Sutochno's `/api/*`
endpoints (e.g. `/api/geo/suggest`) return `403 Application authentication
failed` without an app token the SPA computes client-side. So this skill does
**not** touch the API. It scrapes the **server-rendered city pages**, where the
listing cards are already present in the HTML.

**City results live on SUBDOMAINS.** Each city has its own host:
`https://tbilisi.sutochno.ru/`, `https://batumi.sutochno.ru/`,
`https://kutaisi.sutochno.ru/`, etc. The country directory
`https://sutochno.ru/georgia` lists the supported Georgian cities and links out
to `https://{city}.sutochno.ru/<listingId>` cards. Pass the city as a
**subdomain keyword** (`--city tbilisi`) or a full URL (`--url`).

**No login required.** Sutochno search results are public; the skill finds
rentals and returns links only — it does not log in or complete a booking.

## Prerequisites

```bash
pip install patchright && patchright install chromium
```

Or use Docker (no local install):

```bash
docker build -t sutochno skills/sutochno/
```

## When to Use

- A Russian-speaking traveller (or one travelling with Russian companions)
  booking accommodation that's **paid in rubles via Mir card** / bank transfer
- **Georgia / CIS** stays (Tbilisi, Batumi, Kutaisi, …) where Airbnb / Booking
  withdrew or won't accept Russian cards
- Cross-referencing owner-direct ruble inventory against Airbnb / VRBO before
  booking a multi-night stay

## When NOT to Use

- **Completing a booking.** This skill finds rentals and returns links only.
  Do not attempt to complete a purchase or contact the owner.
- **Non-CIS destinations.** For Western destinations Airbnb (MCP), the `vrbo`
  skill, and Booking have far deeper inventory and card support — use those.
- **Hotels / branded chains.** Use compare-hotels / serpapi / liteapi / the
  portal skills.

## Usage

```bash
# Tbilisi, human-readable table (headless by default)
python3 skills/sutochno/scripts/search_sutochno.py --city tbilisi

# Batumi, JSON, only 9.0+ rated, under 8000 rubles/night, top 15
python3 skills/sutochno/scripts/search_sutochno.py --city batumi \
  --min-rating 9.0 --max-price 8000 --limit 15 --json

# Explicit full URL (e.g. a city page with query filters already applied)
python3 skills/sutochno/scripts/search_sutochno.py \
  --url "https://kutaisi.sutochno.ru/" --json

# Docker
docker run --rm sutochno --city tbilisi --json
```

### Arguments

| Flag | Default | Notes |
|------|---------|-------|
| `--city` | one of city/url required | City **subdomain** keyword, e.g. `tbilisi`, `batumi`. Resolves to `https://{city}.sutochno.ru/`. |
| `--url` | one of city/url required | Full city URL — use instead of `--city` to pass a page that already carries query filters. |
| `--min-rating` | 0 | Client-side filter, 0–10 scale (e.g. `9.0`). Listings with no rating are kept. |
| `--max-price` | 0 | Client-side per-night ceiling in rubles. Listings with no price are kept. |
| `--limit` | 20 | Max listings returned (after sort). |
| `--headed` | off | Run the browser headed (debugging only — Sutochno has no bot wall, so headless is the default). |
| `--json` | off | Emit JSON to stdout (use this for orchestration). |

## Selectors (the scrape contract)

On a city page, each listing card exposes:

| Field | Selector | Raw shape | Extraction |
|-------|----------|-----------|------------|
| name + link | `.card-content__object-type` (an `<a>`) | text + `href` → `https://{city}.sutochno.ru/<id>` (or `/hotels/<id>`) | name text; id = trailing digits of href |
| price | `.card-prices__price` | `"4 890 ₽ за сутки"` | substring **before** `₽`, strip all non-digits → `4890` |
| rating + reviews | `.card-prices__rating` | `"10 отзывов 9,8 (10) …"` | rating = first `/\d{1,2},\d/` (comma → dot); reviews = `/(\d+)\s*отзыв/` |
| address | `.card-content__address` | `"Тбилиси, Старый город"` | as-is |

The card **wrapper** is the nearest ancestor of the name node that also contains
a `.card-prices` element — name and prices are siblings, not nested — so the
extractor climbs up from the name link until `el.querySelector('.card-prices')`
is truthy, then reads price/rating from inside that wrapper.

**Price gotcha:** the thousands separator in the price string is a mixed-width /
non-breaking space, so a naive `\d+` regex truncates the leading digit (returns
`890` for `4 890`). The reliable method is to **split on `₽` then strip all
non-digits** — that's what `parse_price` does.

**Images are lazy-loaded placeholders** (`lzy-img.gif`) on first paint, so the
scraper does **not** rely on photos.

## Output

JSON shape (with `--json`):

```json
{
  "source": "sutochno",
  "searchUrl": "https://tbilisi.sutochno.ru/",
  "query": { "city": "tbilisi", "url": null, "min_rating": 9.0, "max_price": 8000 },
  "count": 12,
  "listings": [
    {
      "id": "1234567",
      "name": "Уютная студия в центре",
      "area": "Тбилиси, Старый город",
      "rub_per_night": 4890,
      "rating": 9.8,
      "reviews": 10,
      "url": "https://tbilisi.sutochno.ru/1234567"
    }
  ]
}
```

Listings are deduped by `id` and sorted **rating desc, then price asc** (entries
with no rating / price sort last). `rub_per_night` is the lead nightly rate in
rubles — open the listing URL to confirm the all-in total (cleaning, deposit,
and any owner fees may not be in the SERP figure).

## Tests

The pure logic (city-URL building, ruble price parsing, rating/review parsing,
listing-id extraction, normalisation, dedupe / filter / sort) is covered by
stdlib `unittest` tests — no browser, network, or Patchright needed (the
`patchright` import is lazy):

```bash
python3 -m unittest discover -s skills/sutochno/tests -v   # or: python3 -m pytest skills/sutochno/tests
```

The browser-driven path (page navigation, DOM extraction) can't be unit-tested
offline — verify it with a real city search (see Usage above).

## Known fragility

- **DOM selectors drift.** Sutochno may revise the `card-content__*` /
  `card-prices__*` class names. If a live search returns 0 cards on a city you
  know has inventory, re-inspect a city page and refresh the selectors in
  `EXTRACT_JS` and the selector table above (file a P2 task).
- **The API stays auth-walled.** Don't be tempted back to `/api/*` — it needs
  the SPA's computed app token and returns `403 Application authentication
  failed` without it. The server-rendered pages are the supported surface.
