"""
chartink_scanner.py

Pulls today's scan results from Chartink (the screener shown in your
screenshot) once each morning, manually triggered (per your stated preference).

IMPORTANT - things you must know about this before relying on it:

1. Chartink has NO official public API. This works by replicating the exact
   network request the Chartink website itself makes when you click "Run Scan"
   in your browser. This can break without warning if Chartink changes their
   site - it is not a stable contract the way the Dhan API is.

2. On a FREE Chartink account, scan results are commonly reported to be
   delayed 30-45 minutes versus live market data. If you are on a free plan,
   treat the symbol LIST as your morning universe (which stocks have narrow
   CPR today), not as a live breakout signal - the actual 15-min breakout
   timing should be verified against fresh Dhan intraday data, not Chartink's
   timestamp. If you have a premium/paid Chartink plan, delay may be shorter,
   but you should verify this yourself before trusting it for entries.

3. You must get your own `scan_clause` string - this is the literal filter
   logic from YOUR screener (the one in your screenshot). To obtain it:
   a. Open your scan on chartink.com in a browser
   b. Open DevTools (F12) -> Network tab
   c. Click "Run Scan"
   d. Find the request to "process" in the network list
   e. Look at its Payload/Form Data - copy the exact `scan_clause` value
   f. Paste it into config.CHARTINK_SCAN_CLAUSE

This file will refuse to run with a clear error until that's filled in -
better that than silently scanning the wrong (or no) criteria.
"""

import requests
from bs4 import BeautifulSoup
import config


CHARTINK_SCREENER_PAGE = "https://chartink.com/screener/"


def get_scan_results(scan_clause: str = None) -> list:
    """
    Returns a list of dicts, one per matching stock, e.g.:
        [{"nsecode": "RELIANCE", "name": "...", "close": "...", ...}, ...]
    Raises RuntimeError with a clear message if scan_clause is not configured
    or if Chartink's response doesn't look as expected.
    """
    scan_clause = scan_clause or config.CHARTINK_SCAN_CLAUSE
    if not scan_clause:
        raise RuntimeError(
            "config.CHARTINK_SCAN_CLAUSE is empty. You must paste your scan's "
            "exact scan_clause string (see the docstring at the top of this "
            "file for step-by-step instructions on getting it from your browser)."
        )

    with requests.Session() as session:
        page = session.get(CHARTINK_SCREENER_PAGE, timeout=20)
        page.raise_for_status()

        soup = BeautifulSoup(page.text, "html.parser")
        token_tag = soup.select_one("[name='csrf-token']")
        if token_tag is None:
            raise RuntimeError(
                "Could not find Chartink's csrf-token on the page. Chartink "
                "may have changed their site layout - this scraper needs updating."
            )
        csrf_token = token_tag["content"]

        session.headers["x-csrf-token"] = csrf_token
        session.headers["Content-Type"] = "application/x-www-form-urlencoded"

        resp = session.post(
            config.CHARTINK_SCAN_URL,
            data={"scan_clause": scan_clause},
            timeout=20,
        )
        resp.raise_for_status()

        body = resp.json()
        if "data" not in body:
            raise RuntimeError(
                f"Unexpected response from Chartink (no 'data' key): {body}"
            )

        return body["data"]


def get_today_universe_symbols(scan_clause: str = None) -> list:
    """
    Convenience function: returns just the list of NSE symbol strings
    (e.g. ["RELIANCE", "TATASTEEL", ...]) from today's scan, ready to feed
    into the Dhan side of the pipeline for CPR + breakout monitoring.
    """
    rows = get_scan_results(scan_clause)
    symbols = [row.get("nsecode") for row in rows if row.get("nsecode")]
    return symbols


if __name__ == "__main__":
    try:
        symbols = get_today_universe_symbols()
        print(f"Found {len(symbols)} symbols in today's scan:")
        print(symbols)
    except RuntimeError as e:
        print("Chartink scan failed:", e)
