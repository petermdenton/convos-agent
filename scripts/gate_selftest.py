#!/usr/bin/env python3
"""Offline self-test for the tag-gate + silence-guard pipeline.
Exit 0 = pass. Run after any self-build change; a fix that breaks the
react-only law must be rolled back."""
import importlib.util
import json
import os
import sys
import time

HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
os.environ["HERMES_HOME"] = HOME
GD = os.path.join(HOME, "data", "tag_gate")
S = "selftest_sess"


def main():
    spec = importlib.util.spec_from_file_location(
        "sg", os.path.join(HOME, "plugins", "silence-guard", "__init__.py"))
    sg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sg)
    os.makedirs(GD, exist_ok=True)

    def gate(tagged):
        json.dump({"tagged": tagged, "ts": time.time()},
                  open(f"{GD}/{S}.json", "w"))

    def out(ends_q, ago):
        json.dump({"ends_q": ends_q, "ts": time.time() - ago},
                  open(f"{GD}/last_out_{S}.json", "w"))

    def clean():
        for f in (f"{GD}/{S}.json", f"{GD}/last_out_{S}.json"):
            try:
                os.remove(f)
            except OSError:
                pass

    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    clean(); gate(False)
    check("untagged suppressed", sg._transform("Nice link!", "photon", S), "NO_REPLY")
    gate(False); out(True, 60)
    check("answer window allows", sg._transform("Locked: Tokyo.", "photon", S), None)
    gate(False); out(True, 400)
    check("stale window suppressed", sg._transform("Great!", "photon", S), "NO_REPLY")
    gate(True)
    check("tagged allowed", sg._transform("Haneda, easily.", "photon", S), None)
    check("tagged ack still suppressed", sg._transform("✅ Filed.", "photon", S), "NO_REPLY")
    clean()
    check("cron exempt", sg._transform("Reminder: lottery opens 10am.", "photon", "cron_x"), None)
    gate(True); sg._transform("Know your group size?", "photon", S)
    rec = json.load(open(f"{GD}/last_out_{S}.json"))
    check("question recorded", rec.get("ends_q"), True)
    clean()

    # Hook syntax must stay importable.
    for path in ("hooks/tag-gate/handler.py", "hooks/collector-first-contact/handler.py",
                 "hooks/doc-sync/handler.py", "hooks/roster-autolink/handler.py"):
        full = os.path.join(HOME, path)
        if os.path.exists(full):
            import ast
            try:
                ast.parse(open(full).read())
            except SyntaxError as e:
                fails.append(f"syntax {path}: {e}")

    if fails:
        print("GATE SELFTEST FAIL:\n" + "\n".join(f" - {f}" for f in fails))
        sys.exit(1)
    print("gate selftest: all pass")


if __name__ == "__main__":
    main()
