#!/usr/bin/env python3
"""
doc_comments.py — watch and reply to comments on trip Living Plan docs.

Convos' comment loop, per the group-collector rules:
  - reply to comments stating intent, act on them, reply "Done", but
  - NEVER resolve a thread. Resolving is for humans.

A thread "needs attention" when it is unresolved AND its latest message
(comment or last reply) was not written by Convos (marker prefix "CONVOS:").
Replying through this script adds the marker automatically, which is also
how a thread gets marked handled — no separate state file.

Usage:
  doc_comments.py check            # digest of threads needing attention (cron pre-check)
                                   # last line is a wakeAgent JSON gate
  doc_comments.py list <trip_id>   # dump all threads on a trip's doc (debug)
  doc_comments.py reply (--trip N | --doc DOC_ID) --comment COMMENT_ID --text "..."
                                   # reply in-thread; never resolves

All commands print JSON except `check` (human-readable digest + gate line).
"""
import argparse
import json
import os
import sqlite3
import sys

# Py3.9 guard: google_api.py needs 3.10+; re-exec under the hermes venv
# python when invoked with an older interpreter (e.g. bare `python3`).
_VENV_PY = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python3.11")
if (sys.version_info < (3, 10) and os.path.exists(_VENV_PY)
        and not os.environ.get("HERMES_NO_REEXEC")):
    os.environ["HERMES_NO_REEXEC"] = "1"
    os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

DB_PATH = os.environ.get(
    "TRIP_TASKS_DB", os.path.expanduser("~/.hermes/data/trips.db"))
GWS_SCRIPTS = os.path.expanduser("~/.hermes/skills/travel/google-workspace/scripts")

MARKER = "CONVOS:"

COMMENT_FIELDS = ("comments(id,resolved,deleted,modifiedTime,content,"
                  "author(displayName,me),quotedFileContent(value),"
                  "replies(id,deleted,content,author(displayName,me),modifiedTime)),"
                  "nextPageToken")


def fail(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def _drive():
    sys.path.insert(0, GWS_SCRIPTS)
    try:
        from google_api import build_service  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        fail(f"google-workspace skill not available/authorized: {exc}")
    return build_service("drive", "v3")


def _trips_with_docs(conn):
    return conn.execute(
        "SELECT id, name, destination, doc_id, doc_url FROM trips "
        "WHERE archived = 0 AND doc_id IS NOT NULL").fetchall()


def _threads(drive, doc_id):
    """All non-deleted comment threads on a doc."""
    out, token = [], None
    while True:
        resp = drive.comments().list(
            fileId=doc_id, fields=COMMENT_FIELDS, pageSize=100,
            includeDeleted=False, pageToken=token).execute()
        out.extend(resp.get("comments", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def _is_convos(text):
    return (text or "").lstrip().upper().startswith(MARKER)


def _needs_attention(c):
    if c.get("resolved") or c.get("deleted"):
        return False
    replies = [r for r in c.get("replies", []) if not r.get("deleted")]
    last_text = replies[-1].get("content") if replies else c.get("content")
    return not _is_convos(last_text)


REOPEN_NOTE = "(reopened — this was resolved without a reply; pending)"


def _reopen_if_swallowed(drive, doc_id, c):
    """Standing guard: a resolved thread with no CONVOS reply was closed
    without being engaged (the old silent-resolve failure mode). Reopen it
    so it re-enters the needs-attention pool. Returns True if reopened."""
    if not c.get("resolved") or c.get("deleted"):
        return False
    replies = [r for r in c.get("replies", []) if not r.get("deleted")]
    if any(_is_convos(r.get("content")) for r in replies):
        return False  # Convos engaged; a human closed it legitimately
    drive.replies().create(
        fileId=doc_id, commentId=c["id"],
        body={"action": "reopen", "content": REOPEN_NOTE},
        fields="id").execute()
    c["resolved"] = False  # reflect the reopen locally so it lands in the digest
    return True


def cmd_check(_args):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    drive = _drive()
    blocks = []
    for t in _trips_with_docs(conn):
        try:
            threads = _threads(drive, t["doc_id"])
        except Exception as exc:  # noqa: BLE001 — one bad doc must not kill the sweep
            print(f"[warn] trip {t['id']} ({t['name']}): comments fetch failed: {exc}",
                  file=sys.stderr)
            continue
        for c in threads:
            try:
                if _reopen_if_swallowed(drive, t["doc_id"], c):
                    print(f"[guard] reopened silently-resolved thread {c['id']} "
                          f"on trip {t['id']}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] reopen guard failed for {c['id']}: {exc}",
                      file=sys.stderr)
            if not _needs_attention(c):
                continue
            who = (c.get("author") or {}).get("displayName") or "someone"
            quoted = ((c.get("quotedFileContent") or {}).get("value") or "").strip()
            lines = [
                f"### Trip {t['id']} — {t['name']}"
                + (f" ({t['destination']})" if t["destination"] else ""),
                f"doc_id: {t['doc_id']}",
                f"comment_id: {c['id']}",
                f"from: {who} at {c.get('modifiedTime', '?')}",
            ]
            if quoted:
                lines.append(f"anchored to: \"{quoted[:200]}\"")
            lines.append(f"comment: {c.get('content', '').strip()}")
            for r in [r for r in c.get("replies", []) if not r.get("deleted")]:
                rwho = (r.get("author") or {}).get("displayName") or "someone"
                lines.append(f"  reply ({rwho}): {r.get('content', '').strip()}")
            blocks.append("\n".join(lines))
    if not blocks:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(blocks)} doc comment thread(s) need a reply:\n")
    print("\n\n".join(blocks))
    print()
    print(json.dumps({"wakeAgent": True}))


def _resolve_doc(args, conn):
    if args.doc:
        return args.doc
    row = conn.execute("SELECT doc_id FROM trips WHERE id = ?",
                       (args.trip,)).fetchone()
    if not row or not row["doc_id"]:
        fail(f"trip {args.trip} has no doc")
    return row["doc_id"]


def cmd_reply(args):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    doc_id = _resolve_doc(args, conn)
    text = args.text.strip()
    if not _is_convos(text):
        text = f"{MARKER} {text}"
    drive = _drive()
    reply = drive.replies().create(
        fileId=doc_id, commentId=args.comment,
        body={"content": text}, fields="id,content").execute()
    # NB: deliberately no way to resolve from here — resolving is for humans.
    print(json.dumps({"ok": True, "doc_id": doc_id, "comment_id": args.comment,
                      "reply_id": reply["id"], "content": reply["content"]}))


def cmd_list(args):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT doc_id FROM trips WHERE id = ?",
                       (args.trip_id,)).fetchone()
    if not row or not row["doc_id"]:
        fail(f"trip {args.trip_id} has no doc")
    print(json.dumps(_threads(_drive(), row["doc_id"]), indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check").set_defaults(func=cmd_check)

    lp = sub.add_parser("list")
    lp.add_argument("trip_id", type=int)
    lp.set_defaults(func=cmd_list)

    rp = sub.add_parser("reply")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--trip", type=int)
    g.add_argument("--doc")
    rp.add_argument("--comment", required=True)
    rp.add_argument("--text", required=True)
    rp.set_defaults(func=cmd_reply)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
