---
name: flight-search
description: "Research and compare flights for a user (one-way or round-trip) across multiple free sources — Google Flights and Skiplagged via browser, plus Kiwi/Skiplagged MCP tools when loaded. Use whenever asked to find, price, or compare flights."
version: 1.0.0
author: Convos
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [travel, flights, flight-search, google-flights, skiplagged, kiwi, mcp]
    related_skills: [hermes-cli-automation, maps]
---

# Flight Search

Class of task: "find/compare flights from A to B for N people on date(s) X."
Applies whether the request comes with full trip details or just a rough ask.

## Step 0 — Get the essentials before searching (one question at a time)

Never fire a search with an ambiguous trip shape. If any of these are missing, ask
— but only ONE question per message, iMessage-style, not a bulleted intake form:

1. Origin + destination (airport or city is fine, resolve to IATA yourself)
2. Outbound date
3. **Round-trip or one-way?** — always ask explicitly if not stated. This changes
   which searches you need to run (see Step 2).
4. Number of passengers (assume 1 only if truly unstated; otherwise confirm)

## Step 1 — Which tools are actually available right now

This project has two free, keyless MCP servers configured for flight search:
`kiwi` (search-flight tool, virtual interlining) and `skiplagged` (10 tools:
search-flight, flexible calendars, destination discovery, IATA resolution).
See `hermes-cli-automation` skill for how these were installed/removed.

**Known gotcha:** MCP servers added via `hermes mcp add` do NOT appear in the
*current* chat session — they only load on the next fresh session/`/reset`.
Before assuming the tools are unavailable, check whether they're actually in
your current toolset. If they aren't (new server added earlier this same
session), don't stall — fall back to browser research immediately using the
patterns below. Don't tell the user "the skill isn't installed"; just get the
answer via the fallback path.

## Step 2 — Run the searches

**If MCP tools are loaded:** call `kiwi`'s `search-flight` and `skiplagged`'s
flight-search tool directly with origin/destination/date/passengers. Compare
their outputs.

**Browser fallback (works even without MCP tools loaded):**

- Google Flights: navigate to
  `https://www.google.com/travel/flights?q=Flights%20from%20{ORIGIN}%20to%20{DEST}%20on%20{YYYY-MM-DD}%20for%20{N}%20people%20one%20way`
  (drop "one way" / adjust wording for round-trip). The results snapshot
  includes price, stops, layover airports/durations, and a "Cheapest" tab.
- Skiplagged direct: navigate to
  `https://skiplagged.com/flights/{ORIGIN_IATA}/{DEST_IATA}/{YYYY-MM-DD}?adults={N}`,
  then click "Search Flights" (`ref` for the button) — the query param alone
  doesn't trigger the search, you must click through. Skiplagged returns both
  normal single-ticket itineraries AND "self-transfer" (hidden-city / separately
  booked) itineraries in the same result list — they're visually flagged with a
  "Self-transfer" badge/button.
- Cross-check the cheapest normal (non-self-transfer) fare against Google
  Flights — if the two agree, treat that number as solid; if they diverge
  significantly, trust the lower one only if it's from a GDS/bookable source
  (see `references/multi-source-strategy.md` for the fuller source-priority
  rationale from the travel-hacking-toolkit project).
- For **round-trip requests**, also price two one-ways independently — this can
  surface a cheaper mixed-carrier construction that round-trip search hides.
  See the reference file for when round-trip vs one-way-pair typically wins.

## Step 3 — Present results

- Lead with a compact comparison table: price (for the full party, not per
  person, unless asked), airline, routing/stops, duration.
- Call out the single cheapest **legitimate, single-PNR** fare as the headline
  recommendation.
- If self-transfer / hidden-city fares came up and are meaningfully cheaper,
  mention them as a secondary option with an explicit risk disclaimer (separate
  tickets = no protection if the first leg is delayed, no through check-in,
  re-check bags, no airline recourse) — don't just report the low price without
  the caveat.
- Keep the writeup iMessage-length: a table plus 2-4 sentences, not a report.
  Offer one obvious next step (check nearby dates, check seat map, etc.) rather
  than a laundry list of options.

## Pitfalls

- Don't assume `hermes mcp add`'d tools are live in the current session —
  verify or just fall back to browser search rather than blocking on it.
- Skiplagged's URL query params pre-fill the form but don't auto-run the
  search; you still need to click the "Search Flights" button.
- Self-transfer results are mixed into skiplagged's main results list, not a
  separate tab — don't accidentally present one as a normal itinerary.

See `references/multi-source-strategy.md` for the full multi-source priority
list (Duffel, Ignav, Seats.aero, Southwest, market-selection arbitrage) drawn
from the borski/travel-hacking-toolkit project — useful background if the user
wants award/points pricing or sets up the paid API keys later.
