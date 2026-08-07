# Eval issues

## Run 1 — 2026-08-05, scenarios S1-variant + S2 (Convos DM)

| # | What happened | Expected | Tier / fix | Status |
|---|---|---|---|---|
| 1 | "Hi" → "Hey — what's up?" | Bound-trip greeting → NO_REPLY | promoted to code: tag-gate hook + guard v2.0 gate untagged turns | fixed (gh#1 closed) |
| 2 | "✅ Frank Fat's — filed under Food…" text ack on untagged msg | Silent filing | code — silence-guard v1.1 suppresses ✅-filing-acks | fixed (needs gateway restart) |
| 3 | "Can you resend the link?" question on untagged 404 link | File stub + note "link 404"; doc shows gap | prompt — skill URL row updated | fixed |
| 4 | "[This response was interrupted by a user correction.]" delivered | Never delivered | code — silence-guard v1.1 suppresses bracketed meta-notes | fixed (needs gateway restart) |
| 5 | Frank Fat's filed into trip 6 (Sacramento group) from the DM (trip 7) | File ONLY into the chat's bound trip | prompt — skill lookup rule hardened; PROMOTE TO CODE if recurs (validate trip_id belongs to chat in a wrapper) | fixed + watching |
| 6 | "⚠️ Gateway shutting down" bubbles in chat | Not user-visible | platform — find lifecycle-notification toggle | open (gh#2) |
| 7 | Model never emits NO_REPLY (0 suppressed in window) | Silence via token | moot — silence now code-enforced by tag-gate, token no longer load-bearing | mitigated (gh#3 closed) |

Also to migrate: Frank Fat's (option 155) from trip 6 → trip 7, or delete (it was a test drop).

## Run 2-3 — 2026-08-05, full journey simulation (fresh DM, San Diego)

| # | What happened | Expected | Tier / fix | Status |
|---|---|---|---|---|
| 8 | Stale session resurrected the REMOVED budget question; intake state machine bypassed entirely (no intake row, hand-rolled ledger) | Current skills win over old chat history | product bug for returning users; mitigated in eval by session wipe; consider periodic session refresh | open (gh#4) |
| 9 | Model's internal narration delivered as bubbles ("Need to use tool_call for these deferred MCP tools", "trip container (trip 16)") despite interim_assistant_messages: false | Config-suppressed | code — guard v1.3 suppresses internal-jargon bubbles; ALSO report to Hermes: config surface not honored | mitigated |
| 10 | intake-commit created trip 17 while trip 16 (hook-created, welcomed with ITS doc link) stayed bound to same chat → duplicate trips, consumer holds a dead doc link | Adopt the chat's existing empty bound trip | code — patch intake-commit to adopt destination-less bound trip; orphan cleanup | PRIORITY, open (gh#5) |
| 11 | Unprompted "I can get you to the booking page… that needs a card" bubble | Recommendation ends with a period; no unprompted follow-ups | prompt — watching | open (gh#6) |
| 12 | "Good — both filed and doc updated ✅…" ack on untagged booking message | Silent filing (NO_REPLY) | prompt (guard's ✅-prefix rule didn't match mid-string) — watching; widen guard if recurs | open (gh#7) |
| 13 | "⚠️ Gateway restarting / ♻️ Gateway online / shutting down" bubbles in consumer chat | Lifecycle noise never user-visible | platform — find toggle or report to Hermes | open (gh#8) |
| 14 | Tagged question "what's the hotel confirmation and check-in time?" unanswered 4+ min | Instant answer from state | investigate (turn hung? message dropped?) | open (gh#9) |
| 15 | Booking message sent during sidecar downtime was silently lost (no backfill on reconnect) | Missed-message backfill | platform — Hermes feature request | open (gh#10) |

GOOD in this run: hook first-contact 5s ✓ · fresh-session ledger correct, no budget ask ✓ · locked line + real nonstop fares in ~70s ✓ · booking claims → booked status + conf numbers in state, night-aware collapse ✓ · silence-guard v1.0 caught 2 narrated-silence leaks in the wild ✓

## Run 4 — 2026-08-05, Tokyo journey (47 msgs, fresh DM, guard v1.3 live)

| # | What happened | Expected | Tier / fix | Status |
|---|---|---|---|---|
| 16 | ~17 substantive commentary bubbles on UNTAGGED drops (every link got a mini-review: Senso-ji, AFURI, Park Hyatt, robot-restaurant chat, budget note, even a 🌸 reply) — 68 delivered bubbles for a 47-msg journey | Untagged = zero response; file silently | BUILT 2026-08-07: tag-gate hook records tagged/untagged per turn; silence-guard v2.0 gates untagged turns to NO_REPLY with a 3-min answer-window after Convos asks a question; cron/API sessions exempt. Live-verified: untagged suppressed, tagged answered | fixed (gh#11 closed) |
| 17 | Duplicate-trip bug REPRODUCED exactly: hook trip 21 bound to chat (empty, own doc), intake-commit made trip 22 with chat_id NULL holding all 21 options; group received TWO doc links, first one permanently empty | Adopt the chat's existing destination-less bound trip | same as gh#5 — repro confirms; also leaves live trip UNBOUND (roster/filing point at wrong trip) | open (gh#5 repro) |
| 18 | "Convos send us the trip app" answered with the Google DOC link; trip_pwa.py deploy never invoked, pwa_url NULL | Deploy PWA, reply with app URL, doc gets app link | prompt+code — skill rule exists but model unaware of trip_pwa; add canned handler: "trip app" intent → deploy + link | open (gh#12) |
| 19 | "Build out the day-by-day" produced a chat-only itinerary; plan_itinerary 0 rows, plan_days empty, doc Itinerary still placeholder; booked items never itinerarized | Itinerary filed to state → doc + PWA render it | prompt — file-before-you-quote applies to itineraries too; promote to code if recurs | open (gh#13) |
| 20 | Intake locked "Leaving Mar 25" from "ok late March it is" (fabricated a precise date, locked before group size), then re-locked Mar 20 when real dates arrived | Don't lock on vague dates; ask, don't invent | prompt — intake rule: never convert vague timeframes to dates | open (gh#14) |
| 21 | Destination stored "Japan" (doc title "Japan · Mar 20–Mar 28") though chat said "plan it: Tokyo" and model even replied "Locked: Tokyo" | destination=Tokyo | prompt/code — city beats country when both mentioned | open (gh#15) |
| 22 | Tagged asks answered in DUPLICATE bubbles: visa 3x (22:58:22/33, 22:59:01), Haneda 2x, Ghibli 2x | One answer per ask | code candidate — per-turn single-delivery cap in guard | open (gh#16) |
| 23 | Glance table "1 options bookmarked · leader $5,093 pp" — $5,093 is TOTAL for 4 ($1,273pp) | leader $1,273 pp or "$5,093 total" | code — trip_doc.py leader-fare cell uses total as pp | open (gh#17) |

GOOD in Run 4: welcome+doc 5s ✓ · all 19 tagged asks answered, zero hangs (gh#9 not reproduced) ✓ · visa answer CORRECT (passport beats green card) ✓ · JR-pass skip + Suica advice ✓ · Ghibli lottery mechanics right, caught that user's "March 9 reminder" was a month late, set BOTH crons (Feb 10 2027 + Mar 9 status) ✓ · 404 airbnb filed as flagged stub, no nagging ✓ · all 4 bookings → booked + confs incl. Kai's separate JAL 15 ✓ · budget note honored silently-ish, never pried, steered pick to Airbnb citing it privately ✓ · "stop" → one ack then real silence (1 post-stop leak caught by guard) → tagged re-engage clean ✓ · travel-day answers instant from state ✓ · guard suppressed 16 acks + 1 jargon leak ✓ · no gateway lifecycle bubbles this run (gh#2/#8 not reproduced) ✓
