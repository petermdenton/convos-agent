#!/usr/bin/env python3
"""One-time recovery sweep: reopen doc comment threads that were resolved
without ever being actioned.

Convos used to resolve comment threads silently instead of acting on them.
A thread counts as "swallowed" when it is resolved but contains NO reply
with the CONVOS: marker (i.e. Convos never stated intent or outcome).
This sweep reopens each one with a plain note — deliberately WITHOUT the
marker, so the trip-doc-comment-watch job sees it as needing attention
and actually works it.

Threads a human resolved after a CONVOS: "Done" reply are left alone.

Runs deterministically (no agent). Prints one line per reopened thread.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3  # noqa: E402

from doc_comments import (  # noqa: E402
    DB_PATH, _drive, _is_convos, _threads, _trips_with_docs)

REOPEN_NOTE = "(reopened — this was resolved without being actioned; pending)"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    drive = _drive()
    reopened = 0
    for t in _trips_with_docs(conn):
        try:
            threads = _threads(drive, t["doc_id"])
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] trip {t['id']} ({t['name']}): fetch failed: {exc}",
                  file=sys.stderr)
            continue
        for c in threads:
            if not c.get("resolved") or c.get("deleted"):
                continue
            replies = [r for r in c.get("replies", []) if not r.get("deleted")]
            if any(_is_convos(r.get("content")) for r in replies):
                continue  # Convos engaged with this one; a human closed it
            try:
                drive.replies().create(
                    fileId=t["doc_id"], commentId=c["id"],
                    body={"action": "reopen", "content": REOPEN_NOTE},
                    fields="id").execute()
                reopened += 1
                snippet = (c.get("content") or "").strip().replace("\n", " ")[:80]
                print(f"reopened: trip {t['id']} comment {c['id']} — \"{snippet}\"")
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] reopen failed for {c['id']}: {exc}", file=sys.stderr)
    print(f"\n{reopened} thread(s) reopened. The trip-doc-comment-watch job "
          f"will pick them up on its next 15-minute pass.")


if __name__ == "__main__":
    main()
