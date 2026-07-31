---
name: car-rentals
description: Use when asked for cheap/best rental cars at a destination.
---

# Car Rentals

## Primary tool: skiplagged `sk_cars_search`

Use the `mcp__skiplagged__sk_cars_search` tool first — it's a free/keyless REST search across many suppliers (Budget, Payless, Dollar, Thrifty, etc.) with real total prices and direct booking links.

**Required params (exact camelCase names — the tool rejects snake_case):**
- `pickupLocation` — airport code (preferred, e.g. `SMF`) or `"lat,lng"`
- `pickupDate` — `YYYY-MM-DD`
- `dropoffDate` — `YYYY-MM-DD`

Optional: `pickupTime`/`dropoffTime` (default `10:00`), `dropoffLocation` (defaults to pickup), `limit` (default 12, raise to ~15-20 for a fuller sweep).

Example call:
```
sk_cars_search(pickupLocation="SMF", pickupDate="2026-09-11", dropoffDate="2026-09-14", limit=15)
```

Results are sorted cheapest-first by default. Each result includes company, vehicle class/example car, passenger/bag capacity, prepaid vs pay-at-counter, free-cancellation flag, total price, and a booking link.

## What to surface to the user

Give 3-4 tiers, not the raw table:
- Cheapest overall (note if it's prepaid/non-refundable vs pay-at-counter with free cancellation — free cancellation is worth calling out explicitly since it's usually only a few dollars more)
- A step up in vehicle size if the group is 4+ people or has bags
- Always end with a one-line recommendation ("book the $X one — free cancel, no prepay") and the search URL so they can browse the rest themselves.

## Pitfalls

- Don't book without explicit confirmation — car rental is a spend action like any other booking.
- Pay-at-counter + free cancellation is almost always worth a few extra dollars over prepaid non-refundable, unless the user is price-maxing.
- If skiplagged returns nothing useful (rare, small airports), fall back to the `ticketsatwork` skill (corporate portal, often 10-30% off) or a general web_search for the airport + "rental car deals".
