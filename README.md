# Convos — a travel agent that lives in iMessage

Convos is a customization layer on top of [Hermes](https://github.com/NousResearch)
that turns a local agent into a group-trip travel concierge you text like a
contact. Drop it into any iMessage chat and it creates a shared "Living Plan"
Google Doc, then silently organizes everything the group throws at it —
flights, stays, restaurants, links, screenshots — while answering when spoken
to. This repo is that layer: identity, scripts, hooks, and skills. The Hermes
app itself, secrets, and trip data are deliberately not here.

## Architecture

**Model extracts, code decides.** The model classifies messages and pulls out
entities; everything that must never fail is deterministic code.

- `SOUL.md` — identity and hard laws (react-only in chats, ledger-style
  intake, never ask about budget, ✅ is the only tapback).
- `scripts/` — the state layer, all SQLite-backed (`data/trips.db`, untracked):
  - `trip_tasks.py` — trips, intake state machine (where/when/how-many),
    2-level task trees with T-minus deadlines, stack-ranking.
  - `trip_plan.py` — plan content: options (flight/stay/food/activity/
    transport with status lifecycle option→booked), itinerary, per-section
    summaries, live "working on it" indicators, changelog.
  - `trip_doc.py` — renders the whole Google Doc from state (full re-render,
    never incremental edits): status-at-a-glance with per-night stay
    coverage, section tables with quote-age tags, comments-and-answers,
    linked related-transport (🚆 Train to Game), tap-to-call phones.
  - `doc_comments.py` — comment threads: reply in-thread (never resolve),
    auto-reopen silently-resolved threads.
  - `travelers.py`, `contact_lookup.py` — roster keyed by phone; names
    resolved from macOS Contacts → "PD, KM, LM" initials.
  - `place_photos.py` — real photos of recommended places (og:image → image
    search) for sending as iMessage attachments.
  - `linkcheck_docs.py`, `refresh_docs.py`, `trip_maps.py`,
    `photon_watchdog.sh` — freshness + ops.
- `hooks/` — deterministic behaviors that fire around the model:
  - `collector-first-contact` — first message in any chat: create trip,
    create doc, send the one welcome message. In code, before the model runs.
  - `doc-sync` — after every agent turn, fingerprint trip state and
    re-render the doc if anything changed. The model can't forget the doc.
  - `roster-autolink` — every sender is linked to the chat's trip and named
    from Contacts automatically.
- `skills/travel/` — behavior playbooks the model loads per message:
  `group-collector` (file-before-you-quote, working indicators, comment
  protocol), `onboarding` (the three-legged-stool ledger intake),
  `trip-scaffold`, `group-trips`, `trip-maps`, plus search/booking toolkit
  skills.

Recurring jobs (Hermes cron, config not in this repo): comment sweep every
15 min, nightly link check, morning price re-quotes.

## Setup on a fresh machine

1. Install Hermes and the Photon iMessage bridge; enable the platform.
2. Clone this repo INTO `~/.hermes` (it is the customization layer of that
   directory): `git clone <repo> ~/.hermes-convos && cp -R ~/.hermes-convos/. ~/.hermes/`
3. Copy `config.example.yaml` → merge into `~/.hermes/config.yaml`; create
   `.env` with your `PHOTON_*` credentials (never commit it).
4. Authorize Google once: `python3 ~/.hermes/skills/travel/google-workspace/scripts/setup.py`
5. Restart the gateway. Text the agent from any chat — the welcome + doc
   should arrive in seconds.

## Working on it

`~/.hermes` is the live install AND the repo. The `.gitignore` is a strict
whitelist — check `git status` before committing; if you see a token, a
database, or anything under `data/`, the ignore file has a hole: fix it
before anything else.
