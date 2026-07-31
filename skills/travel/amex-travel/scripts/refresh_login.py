#!/usr/bin/env python3
"""Interactive session refresh for the Amex travel portal.

The travel-portal login gate is captcha-protected (see SKILL.md, Known
Limitation), so a fully automated fresh login is impossible. When
search_flights.py prints AMEX_HUMAN_LOGIN_NEEDED, run this script on your
LOCAL machine (not Docker). It opens a real Chrome window on the travel
portal; complete the login yourself (password + captcha + email code +
"Add This Device"). The script watches for the logged-in state, saves the
session cookies that the Docker runs mount, and exits.

Usage:
    python3 scripts/refresh_login.py
    python3 scripts/refresh_login.py --timeout 300
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from search_flights import (  # noqa: E402
    get_cookie_path,
    get_profile_dir,
    save_cookies,
)

# The captcha-protected travel-portal gate itself — NOT the flights page.
# Being logged in to americanexpress.com does not pass this gate; the human
# must complete the login HERE so the travel-session cookies get minted.
AMEX_TRAVEL_GATE_URL = "https://www.americanexpress.com/en-us/account/travel/login"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Seconds to wait for you to finish logging in (default 600)",
    )
    args = parser.parse_args()

    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER"):
        print(
            "ERROR: refresh_login.py must run on your local machine, not in "
            "Docker. It needs a visible Chrome window you can interact with.",
            file=sys.stderr,
        )
        sys.exit(2)

    profile_dir = get_profile_dir()
    cookie_path = get_cookie_path()
    os.makedirs(profile_dir, exist_ok=True)

    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel="chrome",
                headless=False,
                viewport={"width": 1400, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                viewport={"width": 1400, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(AMEX_TRAVEL_GATE_URL, timeout=60000)
        time.sleep(3)

        if "/account/travel/login" not in page.url.lower():
            # Gate didn't even render — travel session is already warm.
            save_cookies(ctx, cookie_path)
            print("AMEX_SESSION_REFRESHED", flush=True)
            print(
                "Travel session already warm (gate skipped). "
                f"Profile: {profile_dir}",
                file=sys.stderr,
            )
            ctx.close()
            return

        print("", file=sys.stderr)
        print(
            "A Chrome window is open on the TRAVEL LOGIN GATE.", file=sys.stderr
        )
        print(
            "Log in on this page yourself: user ID, password, any captcha or "
            'email code, and choose "Add This Device" if offered.',
            file=sys.stderr,
        )
        print(
            f"Waiting up to {args.timeout}s for the gate to clear...",
            file=sys.stderr,
        )

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                if not ctx.pages:
                    print("ERROR: Browser window closed.", file=sys.stderr)
                    sys.exit(1)
                # Success: ANY tab that is on travel content and off the gate.
                cleared = False
                for pg in ctx.pages:
                    u = pg.url.lower()
                    if "/account/travel/login" in u or u == "about:blank":
                        continue
                    if "travel" in u and "/login" not in u:
                        cleared = True
                        break
                if cleared:
                    time.sleep(5)  # let the destination page set its cookies
                    save_cookies(ctx, cookie_path)
                    print("AMEX_SESSION_REFRESHED", flush=True)
                    print(
                        f"Gate cleared. Session saved. Profile: {profile_dir}",
                        file=sys.stderr,
                    )
                    ctx.close()
                    return
            except Exception:
                pass  # page mid-navigation; keep polling
            time.sleep(2)

        print("ERROR: Timed out waiting for login.", file=sys.stderr)
        ctx.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
