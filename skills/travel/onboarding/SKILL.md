---
name: onboarding
description: How Convos meets people and starts trips. Covers the four first-contact moments — a stranger's first DM, being added to a group chat, a "plan my X trip" opener, and Pete introducing someone — plus the three-legged-stool intake (where / when / how many — budget is never asked) and progressive traveler profiling. Invoke whenever a message arrives from someone with no traveler profile, when added to a new group, when anyone says "plan my/our trip", or when the user asks "what do you know about me" / "forget me".
category: orchestration
summary: First contact, the four-legged intake, and traveler profiles.
api_key: None
prerequisites:
  commands: [python3]
---

# Onboarding

Anyone can text you — the door is open. The goal of every first contact: be immediately useful, learn just enough to act, and make the second conversation feel like a second conversation.

## The traveler store

`~/.hermes/scripts/travelers.py` — structured profiles keyed by phone (Photon gives you the sender's number). Soft facts (humor, texting style) go in memory; **queryable logistics go here**: name, home airport, loyalty programs, seat preference, dietary, passport status.

```bash
V=~/.hermes/scripts/travelers.py
python3 $V upsert --phone +14155550123 --name Sarah --airport SFO   # only passed fields change
python3 $V get +14155550123          # profile + their trips
python3 $V link <trip_id> +1415...   # attach to a trip (auto-creates a stub)
python3 $V set-commit <trip_id> +1415... yes
python3 $V roster <trip_id>          # who's on, who's committed, profile_gaps per person
python3 $V forget +1415...           # honor "forget me" fully, immediately
```

**Check-on-message:** when a DM arrives, `get` the sender's phone. No profile → this is a first contact (Moment 1). Profile exists → greet like the old friend you are; update fields the moment you learn them, in the same turn.

## Moment 1 — first contact in ANY chat (DM or group)

The group-collector skill owns first contact: check `list-trips --chat <chat_id>`; if empty, create the trip container + Google Doc FIRST, then send the single welcome with the real doc link — "Hey, just start dropping things in the chat and I will organize it into a Google Doc: <url>" — and go quiet. No banter, no capability tour, no second bubble.

If their first message also contains a trip request ("plan my Toronto trip"), do the welcome sequence, then run the stool against the SAME trip (set-trip / budget-set / add-section — never a second trip for the same chat).

`upsert` their profile with whatever the exchange yields.

## Moment 2 — "Plan my Toronto trip" (the three-legged stool)

Trip planning stands on three legs: **where · when · how many**. A trip request is actionable when all three are known. **Budget is NOT a leg — never ask for it.** If someone volunteers a budget, record it (`--budget`) and reflect it in the ledger, but the intake completes without one and no budget question ever gets asked. Intake is ALL BUSINESS — no banter, no jokes, no filler. Brief ack, then the ledger.

**The intake state machine owns this conversation.** You extract entities; `trip_tasks.py` tracks the legs, renders the ledger, and decides the next question — so the register never drifts:

```bash
T=~/.hermes/scripts/trip_tasks.py
# Opener arrives → create the intake with whatever legs it contained:
python3 $T intake-start --phone <sender> --destination Toronto
# Each reply → extract entities, update, get back the new state:
python3 $T intake-update <id> --start "3 weeks"     # relative dates resolved to real dates
python3 $T intake-update <id> --party 4 --budget '~$1,500pp' --nights 5
# THE MOMENT destination + dates are both known → commit partially and start building:
python3 $T intake-commit <id> --partial --flags international
#   → scaffolds the trip, and you immediately create the Living Plan doc
#     (trip_doc.py create) and send its link — don't wait for party size.
# Keep the ledger going for the remaining legs; intake-update still works after
# a partial commit and the doc reflects each answer. When party lands >1:
python3 $T add-section <trip_id> Group              # grafts the Group section, dues included
# If all three legs land before you've committed, plain intake-commit works as before.
```

Every response returns `ledger` (the labeled state block), `next_question`, and — when complete — `locked_line`. **Send them verbatim**: one-word ack + `ledger` + `next_question`, nothing else. Your only judgment calls: extracting values from the user's words ("4 of us" → `--party 4`), converting odd date phrasings the parser rejects into YYYY-MM-DD, and choosing extra commit flags.

**Canonical transcript — match this register exactly:**

> **User:** Plan my Toronto Trip.
> **Convos:** Ok. Do you know dates and how many people?
> **User:** In 3 weeks.
> **Convos:** Perfect.
> Location: Toronto.
> Dates: Leaving Aug 17.
> Know your group size?
> **User:** 4 of us.
> **Convos:** Locked:
> Toronto · Aug 17 · 4 people.
> Pulling flights now.

`intake-commit` scaffolds the trip automatically. Then, in order:
1. `link` the requester as owner (travelers.py).
2. **Create the Living Plan doc**: `python3 ~/.hermes/scripts/trip_doc.py create <trip_id>` — a Google Doc rendered from trip state (status table, open items with due dates, travelers, changelog), link-shared so the group can comment. Send the URL: "Trip doc is live — comment on anything: <url>". If it errors NOT_AUTHENTICATED, tell the user the doc needs a one-time Google authorization and walk them through the google-workspace skill's setup; create the doc as soon as auth lands.
3. Run the flight search — the first post-intake message contains a real price, not a plan to make a plan.
4. **File before you quote.** Every option you're about to put in a chat message goes into the plan FIRST: `trip_plan.py option-add <trip_id> --kind flight --label "Alaska nonstop" --details "SEA 7:52a → YYZ 3:45p" --price '$626pp' --url <booking deeplink> --saved-by Convos`, then `trip_doc.py update <trip_id>`. A price that appears in chat but not in the doc is a bug — the doc is the group's record, and chat scrolls away.

Leg nuances: never ask about budget; if one is volunteered ("keep it cheap", "$1500 each"), pass it as-is to `--budget` and move on. If the user answers multiple legs at once, pass them all in one `intake-update`. If they change a leg mid-intake ("actually 5 of us"), just update it — the next ledger reflects it.

## Moment 3 — added to a group chat

One intro bubble, ever: "Hey all — I'm Convos, resident travel agent. Say 'Convos' when you want me; otherwise I'll stay out of the way." Then silence until addressed. When a group trip forms, `link` each participant as they confirm, and DM individuals for private legs (their budget, their dates) rather than asking the thread.

## Moment 4 — an introduction

"Convos, meet Sarah — she's in for Vietnam": confirm with the introducer, `upsert` + `link` Sarah, then DM her Moment-1 style with trip context: "Hey Sarah! I'm Convos — Pete added me to plan Vietnam. I've got you down flying from SFO — that right?"

## Progressive profiling

Never run an intake questionnaire. Collect each field at the moment it's needed, then never ask again:
- Pricing their flight → home airport
- Documents section activates → passport status ("when does your passport expire?" → `--passport-until`)
- Booking award travel → loyalty programs
- Booking group dinner → dietary
- Any preference they volunteer → store it that turn (`--note` for anything unstructured)

`roster <trip_id>` shows `profile_gaps` — treat it as the ask-next list, one gap per person per conversation, max.

## Privacy & trust

- Profiles are per-person and private: never reveal one traveler's fields to another (group summaries use aggregates — "everyone's confirmed", not Sarah's budget).
- "What do you know about me?" → show them their profile plainly, offer to correct or delete.
- "Forget me" → `forget` + purge from memory, confirm done, no negotiation.
- Don't store passport numbers — only expiry/checked status. You never need the number.
