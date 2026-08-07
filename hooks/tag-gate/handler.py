"""tag-gate hook — write the per-turn tagged/untagged record.

agent:start fires once per inbound chat message with the message text and
session id. We persist {tagged, ts} keyed by session id so the
transform_llm_output guard (which only receives session_id) can decide
whether this turn's output may be delivered at all.

Sessions with no gate record (cron reminders, API runs, doc-comment jobs)
are never gated — absence of a fresh record means "not an inbound chat
turn", and the guard allows those through.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
GATE_DIR = HOME / "data" / "tag_gate"

# "Convos" anywhere, as its own word (case-insensitive). Covers "Convos plan
# it", "@Convos", "hey convos", "Convos: ...".
_TAG = re.compile(r"\bconvos\b", re.I)


def _log(msg):
    try:
        with open(HOME / "logs" / "tag-gate.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _prune():
    """Drop gate records older than a day so the dir stays tiny."""
    try:
        cutoff = time.time() - 86400
        for p in GATE_DIR.glob("*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


async def handle(event_type, context):
    try:
        if event_type != "agent:start":
            return
        ctx = context or {}
        if ctx.get("platform") != "photon":
            return
        session_id = ctx.get("session_id") or ""
        if not session_id:
            return
        GATE_DIR.mkdir(parents=True, exist_ok=True)
        message = ctx.get("message") or ""
        tagged = bool(_TAG.search(message))
        rec = {
            "tagged": tagged,
            "ts": time.time(),
            "chat_id": ctx.get("chat_id") or "",
            "snippet": message[:80],
        }
        tmp = GATE_DIR / f"{session_id}.json.tmp"
        tmp.write_text(json.dumps(rec))
        tmp.replace(GATE_DIR / f"{session_id}.json")
        _log(f"{'TAGGED  ' if tagged else 'untagged'} {session_id[:12]} :: {message[:60]!r}")
        _prune()
    except Exception as e:  # noqa: BLE001
        _log(f"hook error: {e}")
