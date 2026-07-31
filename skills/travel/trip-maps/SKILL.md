---
name: trip-maps
description: Build shareable Google Maps links with multiple pins and send them into the chat — no API key needed. Use whenever a trip conversation involves several places at once — a restaurant shortlist, a day plan, "where are all these?", "send a map", comparing hotels by location, or a walking route — and after building any day-by-day itinerary. Produces one tap-to-open URL that renders every stop as a pin in the Google Maps app.
category: orchestration
summary: Multi-pin Google Maps URLs for group chats, keyless.
api_key: None (optional GOOGLE_MAPS_API_KEY for static image maps)
prerequisites:
  commands: [python3]
---

# Trip Maps

`~/.hermes/scripts/trip_maps.py` — turns place lists into Google Maps URLs where every place is a pin. Keyless: it uses the directions URL format, which renders all stops as pins and opens natively in the Google Maps app on everyone's phone. 10 pins max per URL — more get split into numbered parts automatically.

```bash
M=~/.hermes/scripts/trip_maps.py

# Ordered day plan (walking route, pins in visit order):
python3 $M route "Bánh Mì Phượng" "Japanese Covered Bridge" "Reaching Out Teahouse" \
    --near "Hoi An, Vietnam" --mode walking --title "Day 2 morning"

# Unordered shortlist ("here's everything we're choosing between"):
python3 $M pins "Uffizi" "Ponte Vecchio" "Mercato Centrale" \
    --near "Florence, Italy" --title "Florence shortlist"

# One place:
python3 $M pin "Reaching Out Teahouse" --near "Hoi An, Vietnam"

# All pins visible AT ONCE, inline in the chat — renders a PNG pin map
# (OpenStreetMap tiles, lettered pins, legend; no API key):
python3 $M render "Bánh Mì Phượng" "Reaching Out Teahouse" "An Bang Beach" \
    --near "Hoi An, Vietnam" --title "Hoi An pins"
# → JSON includes "image": path to the PNG. Send it as an iMessage PHOTO
#   (Photon outbound attachment), with the interactive Google link as the caption
#   or a follow-up bubble. This is the DEFAULT for 3+ pins in a group chat —
#   the group sees the whole map without tapping anything.
```

Output JSON: `urls` (the map links), `per_pin` (individual place links), `image` (render only), and `message` — a paste-ready chat line.

**render prerequisites & recovery:** needs Pillow — if it errors with "Pillow not installed", run `pip3 install pillow --break-system-packages` once and retry. Each place is geocoded via OpenStreetMap; check `warnings` in the output — a "no geocode result" means that pin is MISSING from the image. Retry that place with its local-language name (e.g. "Japanese Covered Bridge" → "Chùa Cầu"), a fuller address, or exact `lat,lng` from the maps skill's `search` command, then re-render. Never send a map silently missing pins.

## Rules

- **Always pass `--near "<city, country>"`** when place names are bare — it stops Google from resolving "Central Market" onto the wrong continent. Skip it only for lat,lng input or already-qualified names.
- **Ambiguous or non-Latin names → add `--coords`**: geocodes each place via OpenStreetMap (1s per place) and pins exact coordinates instead of trusting Google's name match. Use it whenever a wrong pin would be embarrassing.
- **Sending to the chat:** for 3+ pins, prefer `render` → send the PNG as a photo + the interactive URL. For 1–2 pins or quick replies, the URL alone is fine. One map per message; if a list split into parts, label them ("Day 1 map / Day 2 map"). For "which one is closest to the hotel?" follow-ups, use `per_pin` links.
- **route vs pins:** `route` when order matters (a day's walking plan — supports `--mode walking/transit` for ≤10 stops); `pins` when it's a shortlist. Both render as pins either way.
- **Static image maps:** with `GOOGLE_MAPS_API_KEY` set in `~/.hermes/.env`, add `--static` to also get a `static_image_url` — download it and send as an iMessage photo (Photon supports outbound attachments) so the group sees the pins without tapping. Without the key, just send the URL.

## Composes with

- **maps skill** — `nearby`/`search` find the candidates (with lat/lon); feed the winners into `pins --coords` (or pass their `lat,lon` directly as places).
- **trip-scaffold** — when a day-by-day itinerary lands, generate one `route` URL per day and save each into `itinerary.md` and the matching task note; drop the day's map in the group chat the evening before.
- **group-trips** — lodging comparisons: `pins` with the 2–3 finalist hotels + key anchors (old town, beach) so the group can see the trade-off, not read about it.
