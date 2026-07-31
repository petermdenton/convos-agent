---
name: trip-scaffold
description: The trip-planning engine. The moment a new trip becomes real, generate the complete critical-path task tree — invariant core + conditional modules (group, international, multi-base, driving) + destination-specific tasks from a research pass — with real due dates computed from the departure date. Then keep it current and let the daily gardener nudge when tasks come due. Invoke automatically when a user confirms a destination or dates, says "we're going", "book it", "let's plan", "new trip", or when a plan-trip / trip-planner / group-trips workflow ends with a committed trip. Also invoke for "where are we on the trip", "trip status", "what's left", "what's overdue".
category: orchestration
summary: Trip-planning engine — scaffold, enrich, deadline, and garden every trip.
api_key: None
prerequisites:
  commands: [python3]
---

# Trip Scaffold — the planning engine

Every real trip gets a plan tree with deadlines. "Real" means a committed destination + rough time window. When in doubt, ask one question: "Want me to set this up as a trip and start tracking it?"

The engine has four stages: **scaffold → enrich → track → garden**.

## The tracker

`~/.hermes/scripts/trip_tasks.py` — SQLite (DB `~/.hermes/data/trips.db`), JSON output, two-level task tree. **Single source of truth for trip progress.**

```bash
T=~/.hermes/scripts/trip_tasks.py
TPL=~/.hermes/skills/travel/trip-scaffold/templates/core.json

python3 $T scaffold "Italy crew" --destination "Rome+Florence" \
    --start 2026-09-20 --end 2026-09-30 \
    --template $TPL --flags group,international,multi_base
# flags: group (2+ travelers) | international | multi_base (2+ bases) | driving
# t_minus offsets in the template become real due dates from --start.
# Trip far out → everything scheduled. Trip soon → overdue items ARE the
# "start here immediately" list; triage them first, skip what's moot.

python3 $T status <trip_id>       # done/total + overdue/due_soon counts + next action per section, due-sorted
python3 $T due [--days 14]        # ALL active trips: overdue + upcoming — the gardener's query
python3 $T tree <trip_id> [--all]
python3 $T add-task <trip_id> "Book Uffizi timed entry" --parent <section_id> --t-minus 45 --note "sells out"
python3 $T complete-task <id> --note "AZ611, conf XYZ789, $712pp"
python3 $T skip-task <id> · note-task <id> "..." · list-trips · archive-trip <id>
```

## Stage 1 — Scaffold

**A trip request in chat NEVER calls `scaffold` directly.** The one entry point for conversational trips is the onboarding skill's intake state machine: `intake-start` → `intake-update` per reply → `intake-commit` (or `--partial` at destination+dates), which scaffolds internally with the right flags. Load the onboarding skill and follow it. Calling `scaffold` yourself skips the three-legged stool, guesses at party size, and produces exactly the broken experience this system exists to prevent.

`scaffold` directly is ONLY for non-conversational creation (importing an existing planned trip, programmatic setup).

Immediately after any commit: create the Living Plan doc (`trip_doc.py create <trip_id>`) and send its link, `link` the requester as owner, `complete-task` anything already decided, then text a 3–5 line summary + the single next action — never raw JSON or the full tree.

## Stage 2 — Enrich (the destination research pass)

The template's **Destination Research** section (international trips) is a questionnaire YOU answer, promptly, using the travel skills and web — then convert every answer into concrete tasks:

| Research task | Becomes (examples) |
|---|---|
| Visa per nationality | "All 6 apply for Vietnam e-visa on evisa.gov.vn" `--t-minus 35` / "Check ETIAS status" for Schengen |
| Passport rule | "Check passports: 6-mo beyond arrival" vs "3-mo beyond departure (Schengen)" |
| Health profile | "Travel clinic: Hep A + typhoid" `--t-minus 50` — or skip-task the Health section for low-risk destinations |
| Payment culture | "Everyone gets fee-free ATM card (cash-heavy)" vs "cards fine, budget city taxes" |
| Scarcity list | "Book Ha Long cruise (3 cabins)" `--t-minus 40` / "Last Supper tickets" `--t-minus 60` |
| Seasonal traps | "Avoid Tet week" / "August closures — reserve restaurants early" |
| Entry extras | "Onward-ticket proof PDF for check-in" |

Add each with `add-task --parent <matching section> --t-minus <lead time> --note <source/why>`, then `complete-task` the research item with a one-line answer in `--note`. Cite real lead times (when does this actually sell out / how long does this actually take), not guesses.

## Stage 3 — Track

Any time something is searched, chosen, or booked in any conversation, update the matching task in the same turn (`complete-task --note` with the concrete detail). Route chosen → set a fare-watch cron, record its id in the task note. Per-trip documents (itinerary.md, bookings/) live in `~/.hermes/trips/<YYYY-MM_slug>/`. A stale tracker is a bug.

**Plan content lives in `trip_plan.py`** (`~/.hermes/scripts/`) — the doc renders from it, so anything not filed there doesn't exist as far as the group can see:

```bash
PL=~/.hermes/scripts/trip_plan.py
# Every flight/stay option you find or the group sends in:
python3 $PL option-add <trip_id> --kind flight --label "Air Canada" \
    --details "SEA 8:10a → YYZ 3:55p · nonstop" --price '$412' --saved-by Pete --status favorite
# Status moves as the group decides: option → shortlist → favorite → held → booked (or cut):
python3 $PL option-set <option_id> --status held --note "free cancel Thu 6pm"
# Ideas placed onto days (unplaced activity/food options render as the idea pool):
python3 $PL iti-set <trip_id> 2026-08-19 dinner "Grey Gardens — hold 7:30p" --source Sarah
# Budget lines, estimate vs committed:
python3 $PL budget-set <trip_id> "Flights" --estimate 412 --note "leader option, live quote"
# Noteworthy actions (fare re-checks, holds, cuts) — the doc's changelog:
python3 $PL log <trip_id> "held King West loft 48h (free cancel Thu 6pm)"
```

Attribute everything (`--saved-by`, `--source`) — "nothing gets lost" only works if ideas keep their owner's name.

**Link drops are intake events.** Any URL anyone posts in the thread — Airbnb, Google Flights, OpenTable, a blog post, anything — gets filed the moment it lands:
1. Fetch the page (web tool) and extract: name, price, key details (beds/route/times/neighborhood).
2. `option-add` with `--url <the link>` (ALWAYS keep the link — it renders as a clickable name in the doc), `--saved-by <who dropped it>`, best-guess `--kind`.
3. Ack in one line ("filed — King West loft, $298/night, on the shortlist") — no essay.
4. If the fetch fails or the page is ambiguous, file it anyway with the raw URL and whatever the chat context says it is; a filed mystery beats a lost link.
Chat statements update state the same way ("actually the Delta one is fine" → `option-set --status favorite`; "cut the hostel" → `--status cut --note <why>`) — then the doc re-renders.

**The Living Plan doc follows the state.** `~/.hermes/scripts/trip_doc.py create <trip_id>` once at intake-commit, then `update <trip_id>` at the end of any turn that changed trip state, and daily from the gardener. It renders the Oaxaca-style format: status-at-a-glance, flights bookmarked, stay shortlist, actives, itinerary, changelog (no budget section, no task list — those stay internal). The doc is a view: never edit it directly, and treat human comments on it as inbound requests — file them via trip_plan.py, then re-render.

## Stage 4 — Garden (stack-ranked, owner-aware)

Tasks carry owners. Assign whenever responsibility is decided (`assign <task_id> "Sarah"`, or `add-task --owner`); when someone says "I'll handle the Airbnb", that's an assign in the same turn.

`rank` is the prioritizer — criticality × urgency, so a slipping Flights item outranks a slipping Packing item at equal lateness:
```bash
python3 $T rank [--trip <id>] [--limit 10]   # ranked open tasks + unowned_top
```

**On the first scaffold ever, ensure the gardener cron exists** (check your cron list first; create once, not per trip): a daily job at 09:00 local running:

> Run `python3 ~/.hermes/scripts/trip_tasks.py rank`. If nothing ranks, do nothing and send no message. Otherwise, per trip: (1) handle yourself what you can do right now — searches, fare re-checks, drafting; (2) for each of the top items WITH an owner, DM that person their item — one line, the task and why now ("⚠ your e-visa was due Friday — evisa.gov.vn, 10 min"); (3) for `unowned_top` items, ask the group thread ONE compact question ("Who's taking travel insurance? It's due this week") and `assign` whoever answers; (4) refresh each trip's Living Plan doc (`trip_doc.py update`). One DM per person, one group question per day, max — respect the no-spam rule.

Also run the `due` sweep opportunistically whenever a trip conversation starts, and reflect the `gardening` skill: near-due "booked" items are also a prompt to re-check prices and availability.

## Rules

- Status pings: headline numbers ("14 of 66 done, 3 overdue") + the 2–3 most urgent items — never the full tree in chat.
- Group privacy: individual budgets/constraints stay out of shared summaries (group-trips skill governs).
- New recurring pattern discovered mid-trip (a task type every trip needs)? Add it to the template file, not just the one trip.
- After the trip: `archive-trip`, fold durable preferences into memory.
