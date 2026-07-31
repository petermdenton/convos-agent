---
name: transfer-partners
description: Credit card to loyalty program transfer ratios. Cross-references seats.aero award prices with transfer ratios to find the cheapest path from UR, MR, TYP, Capital One, or Bilt to any mileage program.
category: loyalty
summary: Cheapest transfer path from credit card points to mileage programs.
api_key: None (local data)
---

# Transfer Partner Optimizer

Given award flight prices (from seats.aero or manual input) and your transferable point balances, find the cheapest path to book.

**No API key needed.** Uses local JSON data files.

## Data Files

| File | Purpose |
|------|---------|
| `~/.hermes/skills/travel/data/transfer-partners.json` | Credit card → loyalty program transfer ratios |
| `~/.hermes/skills/travel/data/points-valuations.json` | CPP valuations per program (floor/ceiling from TPG, UP, OMAAT, VFTW) |
| `~/.hermes/skills/travel/data/partner-awards.json` | Which programs can book which airlines |

## When to Use

- User has award availability (from seats.aero cached search, live search, or manual input)
- User wants to know "which of my points should I use?"
- User wants to compare the effective cost across their transferable currencies
- User asks about transfer partners for a specific airline or program

## Workflow

### Step 0: Check Current Transfer Bonuses (Always)

Before recommending any transfer, load the `transfer-bonuses` skill and check `~/.hermes/skills/travel/data/transfer-bonuses.json` for active bonuses on the relevant currency-to-program pair. A 30% bonus turns a 1:1 ratio into 1:1.3, which can flip the cheapest-currency calculation in Step 3 entirely. Bonuses also have expiration dates that affect timing decisions.

### Step 1: Get Award Availability

Use seats.aero (API or web) to search for award flights. Each result includes:
- `Source` (the loyalty program: "united", "aeroplan", "flyingblue", etc.)
- Points cost per cabin (e.g., 55,000 business)

### Step 2: Map Seats.aero Sources to Transfer Partners

Seats.aero source names map to `transfer-partners.json` keys:

| Seats.aero Source | Transfer Partner Key | Programs That Transfer In |
|-------------------|---------------------|---------------------------|
| `united` | `united` | Chase UR (1:1), Bilt (1:1) |
| `aeroplan` | `aeroplan` | Chase UR (1:1), Amex MR (1:1), Bilt (1:1), Capital One (1:1) |
| `flyingblue` | `flying_blue` | Chase UR (1:1), Amex MR (1:1), Bilt (1:1), Capital One (1:1), Citi TY (1:1), Wells Fargo (1:1) |
| `american` | `american` | Citi TY (1:1) |
| `alaska` | `alaska_hawaiian` | Bilt (1:1) |
| `virginatlantic` | `virgin_atlantic` | Chase UR (1:1), Amex MR (1:1), Bilt (1:1), Citi TY (1:1), Wells Fargo (1:1 via Virgin Red) |
| `delta` | `delta` | Amex MR (1:1) |
| `emirates` | `emirates` | Bilt (1:1), Amex MR (5:4), Capital One (4:3), Citi TY (5:4) |
| `etihad` | `etihad` | Bilt (1:1), Capital One (1:1), Citi TY (1:1) |
| `singapore` | `singapore` | Chase UR (1:1), Amex MR (1:1), Capital One (1:1), Citi TY (1:1) |
| `jetblue` | `jetblue` | Chase UR (1:1), Citi TY (1:1), Wells Fargo (1:1), Amex MR (250:200), Capital One (5:3) |
| `qatar` | `qatar` | Amex MR (1:1), Bilt (1:1), Capital One (1:1), Citi TY (1:1) |
| `turkish` | `turkish` | Bilt (1:1), Capital One (1:1), Citi TY (1:1) |
| `eurobonus` | (no direct transfer) | N/A |
| `aeromexico` | `aeromexico` | Amex MR (1:1.6), Capital One (1:1), Citi TY (1:1) |
| `smiles` | (no direct transfer) | N/A |
| `finnair` | `finnair` | Capital One (1:1) |
| `lufthansa` | (no direct transfer) | N/A |
| `ethiopian` | (no direct transfer) | N/A |
| `saudia` | (no direct transfer) | N/A |

Programs with "no direct transfer" can only be booked by earning miles directly or through alliance partner bookings.

### Step 3: Calculate Effective Cost

For each award option, calculate the cost in each transferable currency:

```
effective_cost = award_miles / transfer_ratio
```

Example: Aeroplan business at 55,000 miles
- Chase UR: 55,000 / 1.0 = **55,000 UR points**
- Amex MR: 55,000 / 1.0 = **55,000 MR points**
- Capital One: 55,000 / 1.0 = **55,000 Cap One miles**

Example: Emirates business at 72,500 miles
- Bilt: 72,500 / 1.0 = **72,500 Bilt points**
- Amex MR: 72,500 / 0.8 = **90,625 MR points** (worse ratio)
- Capital One: 72,500 / 0.75 = **96,667 Cap One miles** (worst ratio)

### Step 4: Calculate Opportunity Cost (CPP)

Use `points-valuations.json` to assess what each currency is "worth":

```
opportunity_cost = effective_cost × point_value_cpp / 100
```

This tells you the "cash equivalent" you're giving up. Lower is better.

### Step 5: Present Results

**Always use markdown tables.**

| Program | Miles Needed | Best Currency | Points Needed | CPP Value | Cash Equivalent |
|---------|-------------|---------------|---------------|-----------|-----------------|
| Aeroplan | 55,000 | Chase UR | 55,000 | 1.7¢ | $935 |
| United | 55,000 | Chase UR | 55,000 | 1.7¢ | $935 |
| Alaska | 37,500 | Bilt | 37,500 | 1.7¢ | $638 |
| Flying Blue | 57,000 | Any 1:1 | 57,000 | 1.7¢ | $969 |

After the table:
- **Recommendation:** "Book via Alaska using Bilt points. 37,500 Bilt = $638 opportunity cost vs next best United/Aeroplan at $935."
- **Check balance:** Verify the user has enough points in the recommended currency.
- **Transfer bonus?** Check if any current transfer bonuses apply (Roame.travel or TPG bonus tracker).

## jq Recipes

### Find all currencies that transfer to a program

```bash
jq -r '
  to_entries | .[] | select(.key != "_meta") |
  .key as $currency | .value.display_name as $name |
  (.value.airlines // {}) + (.value.hotels // {}) |
  to_entries[] | select(.key == "PROGRAM_KEY") |
  "\($name): \(.value.ratio):1 → \(.value.program)"
' ~/.hermes/skills/travel/data/transfer-partners.json
```

Replace `PROGRAM_KEY` with the key (e.g., `united`, `aeroplan`, `flying_blue`).

### Calculate effective costs for an award

```bash
# Given: 55000 miles via aeroplan
PROGRAM="aeroplan"
MILES=55000
jq -r --arg prog "$PROGRAM" --argjson miles $MILES '
  to_entries | .[] | select(.key != "_meta") |
  .key as $currency | .value.display_name as $name |
  ((.value.airlines // {}) + (.value.hotels // {}))[$prog] // null |
  select(. != null) |
  "\($name): \(($miles / .ratio) | floor) points (ratio \(.ratio):1)"
' ~/.hermes/skills/travel/data/transfer-partners.json
```

### Find cheapest path for multiple award options

```bash
# Given: united at 55000, aeroplan at 55000, alaska at 37500
echo '[
  {"program": "united", "miles": 55000},
  {"program": "aeroplan", "miles": 55000},
  {"program": "alaska_hawaiian", "miles": 37500}
]' | jq -r --slurpfile tp ~/.hermes/skills/travel/data/transfer-partners.json '
  .[] | .program as $prog | .miles as $miles |
  ($tp[0] | to_entries | .[] | select(.key != "_meta") |
    .value.display_name as $name |
    ((.value.airlines // {}) + (.value.hotels // {}))[$prog] // null |
    select(. != null) |
    {currency: $name, program: $prog, miles: $miles, points_needed: (($miles / .ratio) | floor), ratio: .ratio}
  )
' | jq -s 'sort_by(.points_needed)'
```

## Cross-Alliance Optimization

Sometimes the cheapest path involves booking through a different program than the obvious one. Check `~/.hermes/skills/travel/data/partner-awards.json` for cross-alliance highlights:

- **Virgin Atlantic → ANA:** 52.5K business from West Coast. Cheaper than any Star Alliance program.
- **Etihad → American Airlines:** Fixed chart often beats AA's dynamic pricing.
- **Flying Blue → Delta:** Often cheaper than SkyMiles, plus free stopovers.
- **Alaska → Starlux:** Only points booking option for Starlux.

## Transfer Timing Risks

Points transfers are irreversible. Timing is the most common cause of lost awards. Transfer only after you've confirmed the exact award, and only when you're ready to ticket immediately.

### Most transfers are instant, 24/7 — a handful are not
Instant transfers post within minutes, any day of the week including weekends and holidays (they run over an API, not a business-day batch). Only the slow programs below are exposed to weekend/holiday delay. Do not assume a transfer will land in time for a same-day booking unless it is on the instant list.

### Per-program transfer speeds
| Program | Speed | Notes |
|---------|-------|-------|
| Aeroplan | Instant | Any day, via API. |
| Flying Blue | Instant | Amex and Chase both post instantly. |
| United MileagePlus | Instant | Chase, Bilt. |
| Hyatt | Instant | Chase, Bilt. |
| Virgin Atlantic | Instant | Amex/Chase; rarely up to 48h. |
| BA / Iberia / Aer Lingus Avios | Instant | Pools instantly across the Avios ecosystem. Lowest-risk transfer. |
| Qatar Avios | Instant | Balance display can lag — re-login to see it. |
| Emirates Skywards | Instant | Occasionally 24h from Amex. |
| JetBlue TrueBlue | Instant | Poor ratios (Cap One 5:3, Amex/Citi 5:4). |
| Cathay Asia Miles | Instant from Amex; 24h from Capital One | |
| Etihad Guest | Instant from Capital One | Amex path ended June 30, 2026. |
| Avianca LifeMiles | Instant from Cap One/Citi | Amex posts instantly but the miles lock until the next day (Colombia time) — do not use the Amex path for a same-day booking. |
| ANA Mileage Club | ~48 hours (up to 3 days) | Amex is the only path. Never same-day. |
| Singapore KrisFlyer | Amex instant; Cap One up to 48h; Chase 1-2 days; Citi up to 5 business days | Transfer only after space is confirmed. |
| Turkish Miles & Smiles | Bilt near-instant; Cap One ~24h; Citi up to 5 business days | No Amex or Chase path. |
| Marriott Bonvoy | ~36 hours (up to a week) | Slowest mainstream target. Never transfer speculatively. |
| Atmos Rewards (was Alaska Mileage Plan) | Bilt instant; Marriott 3:1 ~2 days | No Amex, Chase, or Capital One path. |

### Weekend and holiday delay applies only to the slow programs
The instant programs above are unaffected by weekends. For the slow ones — ANA, Singapore (via Chase/Citi), Turkish (via Citi), Marriott, and Atmos (via Marriott) — a Saturday, Sunday, or holiday submission can add days. If you must use one and seats are at risk, transfer before 5pm ET on the last business day before the weekend or holiday, not the night before.

### New loyalty accounts
A brand-new loyalty account can have its first transfer delayed or fraud-flagged, and some programs enforce a hard waiting period: JAL blocks transfers for ~60 days after linking some partners, and Iberia Plus requires the account to be 90 days old. Open any new account well in advance — weeks, not days — and confirm the account number and name match your card profile exactly before transferring.

### Hold the seat first when the program allows it
Many programs let you place an award on hold before paying, which removes the transfer-timing risk entirely. **Load the `award-holds` skill for current per-program rules** before assuming you have to transfer speculatively.
- **Holds available (hold first, then transfer):** American AAdvantage (24h, free, online self-serve), Lufthansa Miles & More (5 days, phone), Flying Blue (3 days, phone), Cathay Asia Miles (up to 3 days, phone), Turkish Miles & Smiles (2 days), Virgin Atlantic (1-2 days), ANA (2 days, phone), Emirates (1 day, phone), Singapore KrisFlyer (agent discretion). AA's free online hold also works for most partner awards.
- **No holds — transfer is speculative:** United, Delta, Aeroplan, Atmos (Alaska), British Airways, Iberia, Qatar, Korean, Etihad.

When no hold is available, only transfer once:
1. You've confirmed the exact award on the airline's own booking site (not just seats.aero)
2. You're ready to ticket immediately after the miles land
3. `ComputedLastSeen` on seats.aero is fresh — a stale cache means the seat may already be gone

## Notes

- Transfer ratios rarely change, but verify against issuer websites before large transfers.
- Transfers are irreversible. See Transfer Timing Risks above before committing.
- Some programs run transfer bonuses (10-30% extra). Use the `transfer-bonuses` skill (live data, weekly auto-refresh) instead of guessing.
- The "best" currency depends on what you have the most of AND what you value it at. A 1:1 transfer from a currency you value at 2.0 cpp costs more in opportunity than a 1:1 from one you value at 1.5 cpp.
