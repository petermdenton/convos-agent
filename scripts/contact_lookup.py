#!/usr/bin/env python3
"""contact_lookup.py — resolve a phone number or email to a name via the
macOS Contacts database (read-only).

Searches every AddressBook store under ~/Library/Application Support/AddressBook
(root + Sources/*). Phone match is on the last 10 digits; email match is
case-insensitive exact.

CLI:
  contact_lookup.py "+12064273866"     → {"name": "Peter Denton", "first": "Peter", ...}
  contact_lookup.py "kelly@icloud.com"

Prints {"name": null} when no contact matches. Requires the calling process
to have Contacts/Full Disk access (the Hermes gateway does).
"""
import glob
import json
import os
import re
import sqlite3
import sys

AB_ROOT = os.path.expanduser("~/Library/Application Support/AddressBook")


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _stores():
    pats = [os.path.join(AB_ROOT, "AddressBook-v22.abcddb"),
            os.path.join(AB_ROOT, "Sources", "*", "AddressBook-v22.abcddb")]
    out = []
    for p in pats:
        out.extend(glob.glob(p))
    return out


def _record_name(conn, zpk):
    r = conn.execute(
        "SELECT ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD WHERE Z_PK = ?",
        (zpk,)).fetchone()
    if not r:
        return None
    first, last, org = r[0] or "", r[1] or "", r[2] or ""
    name = " ".join(x for x in (first.strip(), last.strip()) if x)
    return {"name": name or (org.strip() or None),
            "first": first.strip() or None, "last": last.strip() or None}


def lookup(identifier):
    ident = (identifier or "").strip()
    is_email = "@" in ident
    want = ident.lower() if is_email else _digits(ident)[-10:]
    if not want:
        return {"name": None}
    for store in _stores():
        try:
            conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            continue
        try:
            if is_email:
                rows = conn.execute(
                    "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS "
                    "WHERE ZADDRESS IS NOT NULL").fetchall()
                for owner, addr in rows:
                    if (addr or "").strip().lower() == want:
                        hit = _record_name(conn, owner)
                        if hit and hit["name"]:
                            return hit
            else:
                rows = conn.execute(
                    "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER "
                    "WHERE ZFULLNUMBER IS NOT NULL").fetchall()
                for owner, num in rows:
                    if _digits(num)[-10:] == want:
                        hit = _record_name(conn, owner)
                        if hit and hit["name"]:
                            return hit
        except sqlite3.OperationalError:
            continue
        finally:
            conn.close()
    return {"name": None}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: contact_lookup.py <phone-or-email>"}),
              file=sys.stderr)
        sys.exit(1)
    print(json.dumps(lookup(sys.argv[1])))
