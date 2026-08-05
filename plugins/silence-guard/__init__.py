"""silence-guard — outbound narrated-silence suppressor.

The react-only law says: when not addressed, the model's entire reply is the
NO_REPLY token (which the gateway suppresses). Models sometimes narrate
instead — "[No response — staying silent until directly addressed.]" — and
that prose is delivered as a real message. This hook is the deterministic
backstop: when the ENTIRE response is a short bracketed/parenthesized
meta-note containing silence language, rewrite it to NO_REPLY so the
gateway's intentional-silence filter eats it.

Deliberately narrow: the whole response must be wrapped in []/() (optionally
with markdown emphasis) and short. Real answers that merely mention silence
("No response from the hotel yet — I'll chase them") are untouched.
"""
import os
import re
from datetime import datetime, timezone

_KEYWORDS = re.compile(
    r"no.?reply|no response|not (?:directly )?addressed|"
    r"stay(?:ing)? (?:silent|quiet)|react.?only|reaction only|"
    r"remaining silent|silent (?:mode|per)|not responding|"
    r"no message needed|nothing to (?:add|say)|per your ask|"
    r"collector mode|untagged",
    re.I,
)
_WRAPPED = re.compile(r"^\s*[*_~`]{0,3}\s*[\[\(].{0,200}[\]\)]\s*[*_~`]{0,3}\s*$", re.S)

_LOG = os.path.expanduser("~/.hermes/logs/silence-guard.log")


def _log(msg):
    try:
        with open(_LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _transform(response_text="", platform="", session_id="", **kwargs):
    text = (response_text or "").strip()
    if not text or len(text) > 240:
        return None
    if _WRAPPED.match(text) and _KEYWORDS.search(text):
        _log(f"suppressed narrated silence ({platform} {session_id[:12]}): {text[:140]!r}")
        return "NO_REPLY"
    return None


def register(ctx):
    ctx.register_hook("transform_llm_output", _transform)
