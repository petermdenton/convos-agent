# Multi-source flight search strategy (condensed from borski/travel-hacking-toolkit)

Source: https://github.com/borski/travel-hacking-toolkit — `flight-search-strategy` skill.
This is background/reference for when the user wants deeper award/points search
or sets up the paid integrations. Only Kiwi and Skiplagged MCP servers from this
toolkit are actually installed in this environment (both free/keyless).

## Full source priority list (most accurate → least)

| Priority | Source | Strengths | Blind spots | Setup needed here? |
|---|---|---|---|---|
| 1 | Duffel | Real GDS per-fare-class cash prices, bookable | No Southwest, no award pricing, offers expire 15-30min | Needs `DUFFEL_API_KEY_LIVE` — not configured |
| 2 | Ignav | Fast REST API, market-selection arbitrage | No Southwest, no award pricing | Needs `IGNAV_API_KEY` — not configured |
| 3 | Google Flights | All airlines incl. Southwest cash, free | Prices can be inflated vs GDS | Available now via browser |
| 4 | Skiplagged | Hidden city fares, zero config | No Southwest, noisy on small markets | **Installed** (MCP + browser) |
| 5 | Kiwi.com | Virtual interlining (cross-airline routing), zero config | No Southwest, garbage on small markets | **Installed** (MCP) |
| 6 | Seats.aero | Award availability across 25+ programs | Cached not live, no cash prices | Needs Seats.aero Pro key (~$8/mo) — not configured |
| 7 | Southwest (skill) | All 4 fare classes, cash + points | Needs Docker (`ghcr.io/borski/sw-fares`) or Patchright | Not configured |

## Round-trip vs one-way construction

Always price BOTH when a return date exists:
1. The round-trip fare on every source.
2. Two one-ways searched independently — surfaces mixed-carrier constructions
   (outbound carrier A, return carrier B) that round-trip search can't see.

Rules of thumb (route-dependent, never assume):
- Round trips often win **internationally** on legacy carriers (return fares
  discounted vs. two one-ways).
- One-way pairs often win **domestically**, on ULCCs, and anywhere Southwest
  flies (SW prices every leg as a one-way) — and buy flexibility since each leg
  can change independently.
- Mixed-carrier pairs can beat both when outbound/return hit different airlines'
  sale calendars.
- If the gap between round-trip and one-way-pair is under ~$50, mention the
  flexibility advantage of separate tickets before recommending the round trip.

## Market selection for international routes

Different country markets return different prices for the same route (e.g.
`&gl=TH` vs `&gl=US` on Google Flights can differ by hundreds of dollars).
Try departure-country market first, then destination-country market, then ask
the user before trying further (VPN markets, third countries). Duffel and
SerpAPI don't support market selection; ignav does via a `market` field.

## Source accuracy hierarchy when sources disagree

**Duffel > Airline website > SerpAPI/Google Flights > Skiplagged/Kiwi**

- Duffel returns real bookable GDS prices per fare class.
- SerpAPI/Google Flights often shows bundled "main cabin" prices, not the
  cheapest bookable fare class — treat as directionally useful, not gospel.
- Kiwi/Skiplagged can return noisy or garbage results on small/regional markets
  — sanity check anything that looks too cheap.

## If the user wants to go further

Point them at borski/travel-hacking-toolkit directly for: Duffel/Ignav API key
setup, Seats.aero award search, the Southwest Docker-based fare tool, and
Amex/Chase travel-portal skills. Those aren't installed here and need API keys
or Docker — don't try to fake their output.
