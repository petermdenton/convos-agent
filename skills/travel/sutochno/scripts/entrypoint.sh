#!/bin/bash
# Sutochno is NOT behind a bot wall — plain Patchright runs headless fine, so
# unlike the vrbo entrypoint no Xvfb virtual display is needed. Just exec the
# scraper with the passed-through args.
exec python3 /app/search_sutochno.py "$@"
