"""silence-guard v2.0 — outbound gate + narrated-silence suppressor.

Two jobs, in order:

1. TAG GATE (new in v2.0): the react-only law, enforced in code. The
   tag-gate hook records per turn whether the inbound message addressed
   Convos. Untagged turn => this guard rewrites the ENTIRE output to
   NO_REPLY, no matter how reasonable the prose looks. One exception, the
   ANSWER WINDOW: if Convos' own last delivered bubble ended with a
   question mark and this message arrived within 3 minutes, the turn is
   treated as prompted (so intake stays conversational). Sessions with no
   fresh gate record (cron reminders, API runs) are never gated.

2. PATTERN BACKSTOPS (v1.x): even on tagged turns, suppress narrated
   silence, filing acks, bracketed meta-notes, greeting-only replies and
   internal-jargon leaks.

Fails open: any error in gate bookkeeping allows delivery — losing one
suppression beats eating a real answer.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
_GATE_DIR = os.path.join(_HOME, "data", "tag_gate")
_LOG = os.path.join(_HOME, "logs", "silence-guard.log")

_ANSWER_WINDOW_S = 180       # untagged reply to Convos' question counts as prompted
_GATE_FRESH_S = 900          # gate record older than this = not an inbound chat turn

_KEYWORDS = re.compile(
    r"no.?reply|no response|not (?:directly )?addressed|"
    r"stay(?:ing)? (?:silent|quiet)|react.?only|reaction only|"
    r"remaining silent|silent (?:mode|per)|not responding|"
    r"no message needed|nothing to (?:add|say)|per your ask|"
    r"collector mode|untagged",
    re.I,
)
_WRAPPED = re.compile(r"^\s*[*_~`]{0,3}\s*[\[\(].{0,200}[\]\)]\s*[*_~`]{0,3}\s*$", re.S)
_INTERNALS = re.compile(
    r"tool_call|deferred MCP|MCP tool|trip container|trip_(?:plan|doc|tasks|pwa)\.py|"
    r"option-(?:add|set|list)|intake-(?:start|update|commit)|iti-set|"
    r"chat_id|session[_ ]id|state machine|scaffold(?:ing)? the trip|"
    r"\btrip \d{1,3}\b|doc-sync|silence-guard|NO_REPLY token",
    re.I,
)
_FILING_ACK = re.compile(r"^\s*[✅☑️✔️]\s*.{0,180}\b(filed|added|logged|noted|updated the doc)\b", re.I | re.S)
_META_NOTE = re.compile(r"^\s*\[.{0,160}(interrupted|user correction|system|no response)"
                        r".{0,160}\]\s*$", re.I | re.S)
_GREETING_ONLY = re.compile(
    r"^\W{0,3}(hey|hi|hello|yo|hiya|howdy)\b[\s!,.…—-]*(there|all|everyone)?"
    r"[\s!,.…—-]*((what'?s up|how can i help|what can i do for you|"
    r"what are we (working on|planning))\??)?[\s!,.…—-]*\W{0,3}$", re.I)


def _log(msg):
    try:
        with open(_LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _gate_verdict(session_id):
    """Return (deliver: bool, reason: str)."""
    gate = _read_json(os.path.join(_GATE_DIR, f"{session_id}.json"))
    now = time.time()
    if not gate or now - gate.get("ts", 0) > _GATE_FRESH_S:
        return True, "no fresh gate record (cron/api turn)"
    if gate.get("tagged"):
        return True, "tagged"
    last = _read_json(os.path.join(_GATE_DIR, f"last_out_{session_id}.json"))
    if last and last.get("ends_q") and gate.get("ts", 0) - last.get("ts", 0) <= _ANSWER_WINDOW_S:
        return True, "answer window (Convos asked, they answered)"
    return False, "untagged turn"


def _record_delivery(session_id, text):
    _write_json(os.path.join(_GATE_DIR, f"last_out_{session_id}.json"),
                {"ends_q": text.rstrip().rstrip("*_~`\"'").endswith("?"), "ts": time.time()})


def _transform(response_text="", platform="", session_id="", **kwargs):
    text = (response_text or "").strip()
    if not text:
        return None

    # ── 1. Tag gate (photon chat turns only) ──
    if platform == "photon" and session_id:
        try:
            deliver, why = _gate_verdict(session_id)
        except Exception as e:  # noqa: BLE001  — fail open
            deliver, why = True, f"gate error: {e}"
        if not deliver:
            _log(f"GATED untagged turn ({session_id[:12]}): {text[:140]!r}")
            return "NO_REPLY"

    # ── 2. Pattern backstops (short bubbles only, as in v1.x) ──
    if len(text) <= 260:
        reason = None
        if _WRAPPED.match(text) and _KEYWORDS.search(text):
            reason = "narrated silence"
        elif _FILING_ACK.match(text):
            reason = "filing ack (acks are silent — the doc is the receipt)"
        elif _META_NOTE.match(text):
            reason = "bracketed meta-note"
        elif len(text) <= 60 and _GREETING_ONLY.match(text):
            reason = "greeting-only reply (the hook owns hello)"
        elif _INTERNALS.search(text):
            reason = "internal jargon leak (consumers never see the machinery)"
        if reason:
            _log(f"suppressed {reason} ({platform} {session_id[:12]}): {text[:140]!r}")
            return "NO_REPLY"

    # Delivered: remember whether it ended with a question (answer window).
    if platform == "photon" and session_id:
        try:
            _record_delivery(session_id, text)
        except Exception:  # noqa: BLE001
            pass
    return None


def register(ctx):
    ctx.register_hook("transform_llm_output", _transform)
    _log("silence-guard v2.0 registered (tag gate + pattern backstops)")
