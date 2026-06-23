"""
dhan_client.py

Wrapper around the OFFICIAL `dhanhq` python library (pip install dhanhq).
We use the official library instead of hand-rolled requests calls because:
  - Dhan maintains it directly, so endpoint/field changes get fixed upstream
  - it already implements scrip master lookup, intraday candles, and orders
  - it is what your existing NIFTY options bot already uses, so the
    credential/auth pattern is consistent across both bots

This module makes real network calls. Nothing here executes a trade unless
config.DRY_RUN is False AND you call place_order explicitly.

Install: pip install dhanhq --break-system-packages
"""

import os
import csv
import requests
import config

try:
    from dhanhq import DhanContext, dhanhq
except ImportError:
    DhanContext = None
    dhanhq = None


def load_credentials(path: str = None) -> dict:
    path = path or config.CREDENTIALS_FILE
    creds = {}
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Credentials file not found at {path}. Create it with client_id and access_token."
        )
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            creds[key.strip()] = val.strip()

    missing = [k for k in ("client_id", "access_token") if k not in creds or not creds[k] or creds[k].startswith("YOUR_")]
    if missing:
        raise ValueError(
            f"credentials.txt is missing real values for: {missing}. "
            "Open credentials.txt and paste your actual Dhan client_id and access_token."
        )
    return creds


class DhanClient:
    def __init__(self, credentials_path: str = None):
        if dhanhq is None:
            raise ImportError(
                "The 'dhanhq' package is not installed. Run:\n"
                "  pip install dhanhq --break-system-packages"
            )

        self.creds = load_credentials(credentials_path)
        self.client_id = self.creds["client_id"]
        self.access_token = self.creds["access_token"]

        self.context = DhanContext(self.client_id, self.access_token)
        self.dhan = dhanhq(self.context)

        self._scrip_master_cache = None

    # ------------------------------------------------------------------
    # Scrip master - needed to translate a futures symbol into a securityId.
    # We still download the raw CSV ourselves (rather than relying solely on
    # fetch_security_list) because we need to filter/sort by expiry date
    # ourselves to pick the correct front-month futures contract.
    # ------------------------------------------------------------------
    def load_scrip_master(self, force_refresh: bool = False):
        """
        Downloads (or loads cached) the Dhan DETAILED scrip master CSV.
        Refresh once per trading day - Dhan rotates contracts/IDs.

        ACTUAL columns confirmed from a live download (this CSV schema has
        changed at least once vs older Dhan docs/community posts, so trust
        this list over anything written elsewhere in this file's comments):
            EXCH_ID, SEGMENT, SECURITY_ID, ISIN, INSTRUMENT,
            UNDERLYING_SECURITY_ID, UNDERLYING_SYMBOL, SYMBOL_NAME,
            DISPLAY_NAME, INSTRUMENT_TYPE, SERIES, LOT_SIZE, SM_EXPIRY_DATE,
            EXPIRY_FLAG, STRIKE_PRICE, OPTION_TYPE, TICK_SIZE, ...
        """
        if self._scrip_master_cache is not None and not force_refresh:
            return self._scrip_master_cache

        cache_path = config.SCRIP_MASTER_LOCAL_CACHE
        if force_refresh or not os.path.exists(cache_path):
            resp = requests.get(config.SCRIP_MASTER_URL, timeout=60)
            resp.raise_for_status()
            with open(cache_path, "wb") as f:
                f.write(resp.content)

        rows = []
        with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        if rows and "SECURITY_ID" not in rows[0]:
            raise RuntimeError(
                "Scrip master CSV columns don't match what this code expects "
                f"(SECURITY_ID missing). Actual columns: {list(rows[0].keys())}. "
                "Dhan may have changed the CSV schema again - update load_scrip_master() "
                "to match before trusting any lookups from it."
            )

        self._scrip_master_cache = rows
        return rows

    def find_futures_security_id(self, underlying_symbol: str, expiry_date: str = None) -> dict:
        """
        Look up the futures contract for a given underlying symbol (e.g. "RELIANCE").
        If expiry_date is None, returns the NEAREST expiry (front-month) contract.

        NOTE on EXCH_ID: confirmed by direct inspection that EVERY single-stock
        futures row (INSTRUMENT_TYPE=FUTSTK) in this scrip master CSV has
        EXCH_ID=BSE - there are zero EXCH_ID=NSE rows for FUTSTK at all. This
        column is NOT filtered on here, and separately, exhaustive testing
        (daily candles, intraday candles, live quotes) confirmed this Dhan
        account's actual market-data feed for these contracts only returns
        data under exchange_segment="BSE_FNO", not "NSE_FNO" - see config.py's
        EXCHANGE_SEGMENT_FUTURES comment for the full finding. An earlier
        version of this function incorrectly filtered on EXCH_ID=NSE, which
        would have caused find_futures_security_id to fail for every single
        stock future - that filter has been removed.

        Raises ValueError if nothing matches - deliberately loud, since silently
        trading the wrong contract is worse than crashing.
        """
        rows = self.load_scrip_master()
        candidates = [
            r for r in rows
            if r.get("UNDERLYING_SYMBOL", "").upper() == underlying_symbol.upper()
            and r.get("INSTRUMENT_TYPE", "").upper() == config.INSTRUMENT_TYPE_FUTSTK
        ]

        if not candidates:
            raise ValueError(
                f"No futures contract found for '{underlying_symbol}' in scrip master. "
                "Double check the symbol name. Run scrip_master_inspect() to see sample rows."
            )

        if expiry_date:
            candidates = [r for r in candidates if expiry_date in r.get("SM_EXPIRY_DATE", "")]
            if not candidates:
                raise ValueError(f"No contract found for {underlying_symbol} with expiry {expiry_date} on {exch_id}")

        candidates.sort(key=lambda r: r.get("SM_EXPIRY_DATE", "9999-99-99"))
        return candidates[0]

    def scrip_master_inspect(self, instrument_type: str = "FUTSTK", n: int = 5):
        """Debug helper: print n sample rows for a given instrument type so you
        can visually confirm column names/values before trusting lookups.
        Pay special attention to EXCH_ID - both NSE and BSE rows can exist."""
        rows = self.load_scrip_master()
        matches = [r for r in rows if r.get("INSTRUMENT_TYPE", "").upper() == instrument_type.upper()][:n]
        for r in matches:
            print(f"EXCH_ID={r.get('EXCH_ID')}  UNDERLYING_SYMBOL={r.get('UNDERLYING_SYMBOL')}  "
                  f"SECURITY_ID={r.get('SECURITY_ID')}  SM_EXPIRY_DATE={r.get('SM_EXPIRY_DATE')}")
        if not matches:
            # Show what distinct INSTRUMENT_TYPE values DO exist, to help find the right one
            distinct = sorted(set(r.get("INSTRUMENT_TYPE", "") for r in rows))
            print(f"No rows matched INSTRUMENT_TYPE='{instrument_type}'. "
                  f"Distinct INSTRUMENT_TYPE values found in file: {distinct}")
        return matches

    # ------------------------------------------------------------------
    # Historical / intraday candles - via official library
    # ------------------------------------------------------------------
    def get_intraday_candles(self, security_id: str, exchange_segment: str,
                              instrument: str, from_date: str, to_date: str,
                              interval: str = "15") -> dict:
        """
        Fetch intraday OHLC candles via the official library.
        interval: "1", "5", "15", "25", "60" (minutes).
        NOTE: Dhan's intraday endpoint only returns the last 5 trading days
        of history regardless of from_date/to_date - this is a Dhan-side
        limitation, not a bug in this code.
        """
        return self.dhan.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
        )

    def get_daily_candles(self, security_id: str, exchange_segment: str,
                           instrument: str, from_date: str, to_date: str) -> dict:
        return self.dhan.historical_daily_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument,
            expiry_code=0,
            from_date=from_date,
            to_date=to_date,
        )

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def place_order(self, security_id: str, exchange_segment: str, transaction_type: str,
                     quantity: int, order_type: str = "MARKET", product_type: str = "INTRADAY",
                     price: float = 0):
        """
        Places a real order with Dhan UNLESS config.DRY_RUN is True, in which
        case it logs what WOULD have been sent and returns a simulated response.
        transaction_type: "BUY" or "SELL"
        """
        payload_preview = {
            "security_id": security_id,
            "exchange_segment": exchange_segment,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": order_type,
            "product_type": product_type,
            "price": price,
        }

        if config.DRY_RUN:
            print(f"[DRY_RUN] Would place order: {payload_preview}")
            return {"dry_run": True, "payload": payload_preview, "orderStatus": "SIMULATED"}

        return self.dhan.place_order(
            security_id=security_id,
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            product_type=product_type,
            price=price,
        )

    def get_positions(self):
        return self.dhan.get_positions()


if __name__ == "__main__":
    import sys

    # Basic connectivity smoke test - run this manually after filling in
    # credentials.txt with a real client_id and access_token.
    try:
        client = DhanClient()
        print("Credentials loaded OK for client_id:", client.client_id)
    except Exception as e:
        print("Credential check failed:", e)
        sys.exit(1)

    # Usage: python dhan_client.py inspect [INSTRUMENT_TYPE]
    # e.g.   python dhan_client.py inspect FUTSTK
    if len(sys.argv) > 1 and sys.argv[1] == "inspect":
        instrument_type = sys.argv[2] if len(sys.argv) > 2 else "FUTSTK"
        print(f"\nLooking for INSTRUMENT_TYPE='{instrument_type}' in scrip master...\n")
        client.scrip_master_inspect(instrument_type=instrument_type, n=5)
