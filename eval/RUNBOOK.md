# Convos eval loop — research → simulate → fix → repeat

The improvement flywheel: real-world group-travel pain points become
simulated iMessage conversations; every simulated failure becomes an issue;
every issue becomes a code/skill fix and a commit; the scenario then re-runs
until it passes. Run by the Cowork build session (Claude), with Pete's
desktop connected.

## The loop

1. **Research.** Mine travel-planning pain from the wild — Reddit trip-
   planning threads, group-chat horror stories, travel-app comparison posts,
   YouTube "how to plan a group trip" content. Distill into pains → add or
   sharpen scenarios in `eval/scenarios.json`. Grounding so far: flaky
   commitment ("who's actually in?"), scattered confirmations, organizer
   burnout (one person does 80%), decision paralysis, money awkwardness,
   plus-one/dropout roster drama, last-minute changes, travel-day chaos.
2. **Simulate.** With Pete's desktop connected, drive Messages via the
   computer bridge: open the Convos Agent DM (or a test group), send each
   scenario's script as a real iMessage, waiting for the agent between
   messages. This exercises the REAL pipeline — sidecar, hooks, model,
   silence-guard — not a mock.
3. **Verify.** Check the three surfaces after each scenario:
   - chat: screenshot — count bubbles/reactions against `expect`
   - state: `trips.db` read-only — options, roster, itinerary, statuses
   - projections: doc HTML render + PWA build output
   Plus `logs/silence-guard.log` (leak attempts) and `logs/*.log` (errors).
4. **File.** Every miss becomes an entry in `eval/issues.md`:
   `S# · what happened · expected · root cause tier (code/canned/prompt)`.
5. **Fix.** Route by tier: renderer/state/hook bugs → build session edits
   code directly; behavior misses → skill/SOUL rule; recurring behavior
   misses → promote to code (the ratchet). Small mechanical fixes can go
   through `self_build.py` on the Mac.
6. **Commit + re-run.** Every fix is a git commit. Re-run the failed
   scenario before calling it fixed. A scenario that passes twice in a row
   is green.

## Cleanup between runs

Simulated chats create real trips. After a scenario: `trip_tasks.py
delete-trip <id>` for throwaway trips, and `/reset` the chat session (needs
`/approve` in Messages). Never simulate in the real Sacramento/group chats
— use the DM or a dedicated test group.

## Sources (research grounding)

- TRIPTI blog: How to plan a group trip without losing friends
- AOL/PEOPLE reposts of Reddit threads: girls-trip planning chaos; aunt
  drops out then wants back in; plus-ones changing the vibe
- fit2journey: 20 red flags of group travel
- Tiffany Ng UX case study: simplifying travel planning
- WePlanify: splitting group costs without drama
- TripProf: group travel app comparison (what users complain apps miss)
