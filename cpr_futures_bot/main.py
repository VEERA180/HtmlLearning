"""
main.py

Live trading orchestrator for the CPR-narrow + 15-min-breakout futures strategy.

Flow each trading day:
  1. (Manual, once in the morning) Run chartink_scanner.get_today_universe_symbols()
     to get today's candidate stock list - or just maintain your own watchlist.
  2. For each symbol, resolve its futures security_id via DhanClient.
  3. Fetch yesterday's daily HLC (for CPR) + poll today's 15-min candles.
  4. On each newly CLOSED 15-min candle, check entry_signal via cpr_engine.
  5. If signal fires AND no position open yet today for that symbol -> enter.
  6. While a position is open, feed every new candle close into the
     Position (trade_manager) and act on its SL/target/exit decisions.

SAFETY:
  - config.DRY_RUN gates ALL real order placement. Leave it True until you
    have watched this run for several live sessions and are confident in it.
  - This script polls every CPR_TIMEFRAME_MINUTES; it does not use tick data,
    consistent with your "15-min candle close" entry rule.
"""

import time
import datetime as dt
import csv
import os

import config
from dhan_client import DhanClient
from cpr_engine import calculate_cpr
from trade_manager import Position

# If a symbol's poll_and_act() errors this many times IN A ROW, drop it from
# the active watchlist for the rest of the session rather than retrying
# forever (e.g. an expired contract, a persistently bad security_id, etc.).
# A single transient network blip will NOT trigger this - the counter resets
# to 0 on any successful poll.
MAX_CONSECUTIVE_POLL_FAILURES = 5


def ensure_log_dir():
    os.makedirs(config.LOG_DIR, exist_ok=True)
    if not os.path.exists(config.TRADE_LOG_FILE):
        with open(config.TRADE_LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "entry_time", "entry_price", "exit_time",
                              "exit_price", "exit_reason", "quantity", "pnl", "state"])


def log_trade(summary: dict):
    with open(config.TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            summary.get("symbol"), summary.get("entry_time"), summary.get("entry_price"),
            summary.get("exit_time"), summary.get("exit_price"), summary.get("exit_reason"),
            summary.get("quantity"), summary.get("pnl"), summary.get("state"),
        ])


class SymbolWatcher:
    """
    Tracks CPR state + open position for ONE futures symbol through the
    trading day, enforcing the max-1-entry-per-day rule.
    """

    def __init__(self, underlying_symbol: str, client: DhanClient):
        self.underlying_symbol = underlying_symbol
        self.client = client
        self.contract = None       # resolved scrip master row
        self.cpr = None
        self.reference_close = None
        self.position: Position = None
        self.traded_today = False
        self.last_seen_candle_time = None
        self.consecutive_poll_failures = 0

    def resolve_contract(self):
        self.contract = self.client.find_futures_security_id(self.underlying_symbol)
        print(f"[{self.underlying_symbol}] Resolved security_id={self.contract.get('SECURITY_ID')} "
              f"expiry={self.contract.get('SM_EXPIRY_DATE')}")

    def compute_today_cpr(self):
        """Fetch yesterday's daily candle and compute today's CPR levels from it."""
        today = dt.date.today()
        # look back a few days to safely capture the last trading day even
        # across weekends/holidays, then take the most recent row before today
        from_date = (today - dt.timedelta(days=7)).isoformat()
        to_date = today.isoformat()

        daily = self.client.get_daily_candles(
            security_id=self.contract["SECURITY_ID"],
            exchange_segment=config.EXCHANGE_SEGMENT_FUTURES,  # BSE_FNO - see config.py for why
            instrument=config.INSTRUMENT_TYPE_FUTSTK,
            from_date=from_date,
            to_date=to_date,
        )

        # CONFIRMED actual response shape from dhanhq (not assumed): the OHLC
        # arrays are nested one level deeper under a "data" key, alongside
        # "status" and "remarks" - e.g.
        #   {'status': 'success', 'remarks': '', 'data': {'open': [...], 'high': [...], ...}}
        # An earlier version of this code read daily.get("close") directly on
        # the OUTER dict, which always failed even on a successful response.
        if daily.get("status") != "success":
            print(f"[{self.underlying_symbol}] DEBUG - raw response from get_daily_candles: {daily}")
            raise RuntimeError(f"[{self.underlying_symbol}] Daily candle request failed: "
                                f"{daily.get('remarks')}")

        candle_data = daily.get("data", {})
        if not candle_data.get("close"):
            print(f"[{self.underlying_symbol}] DEBUG - raw response from get_daily_candles: {daily}")
            raise RuntimeError(f"[{self.underlying_symbol}] No daily candle data returned for CPR calc.")

        prev_high = candle_data["high"][-1]
        prev_low = candle_data["low"][-1]
        prev_close = candle_data["close"][-1]

        self.cpr = calculate_cpr(prev_high, prev_low, prev_close)
        self.reference_close = prev_close
        is_narrow = self.cpr.is_narrow(self.reference_close)
        print(f"[{self.underlying_symbol}] CPR pivot={self.cpr.pivot:.2f} "
              f"BC={self.cpr.bc:.2f} TC={self.cpr.tc:.2f} "
              f"width%={self.cpr.width_pct_of(self.reference_close):.3f} "
              f"narrow={is_narrow}")
        return is_narrow

    def poll_and_act(self):
        """Call this once per CPR_TIMEFRAME_MINUTES during market hours."""
        today_str = dt.date.today().isoformat()
        candles_resp = self.client.get_intraday_candles(
            security_id=self.contract["SECURITY_ID"],
            exchange_segment=config.EXCHANGE_SEGMENT_FUTURES,
            instrument=config.INSTRUMENT_TYPE_FUTSTK,
            from_date=today_str,
            to_date=today_str,
            interval=str(config.CPR_TIMEFRAME_MINUTES),
        )

        # Same nested-"data" response shape as the daily endpoint - see the
        # comment in compute_today_cpr() for the confirmed structure.
        if candles_resp.get("status") != "success":
            print(f"[{self.underlying_symbol}] Intraday request failed: {candles_resp.get('remarks')}")
            return

        candles = candles_resp.get("data", {})
        if not candles.get("close"):
            print(f"[{self.underlying_symbol}] No intraday candles yet.")
            return

        latest_close = candles["close"][-1]
        latest_time = candles["timestamp"][-1]

        if latest_time == self.last_seen_candle_time:
            return  # no new closed candle yet
        self.last_seen_candle_time = latest_time

        # --- manage open position ---
        if self.position is not None and self.position.is_open():
            closed = self.position.update(latest_close, str(latest_time))
            if closed:
                summary = self.position.summary()
                print(f"[{self.underlying_symbol}] Position closed: {summary}")
                log_trade(summary)
                self._submit_exit_order()
                self.position = None
            return

        # --- check for fresh entry, max 1/day ---
        if self.traded_today:
            return

        is_narrow = self.cpr.is_narrow(self.reference_close)
        is_breakout = latest_close > self.cpr.tc

        if is_narrow and is_breakout:
            print(f"[{self.underlying_symbol}] ENTRY SIGNAL at {latest_close} "
                  f"(TC={self.cpr.tc:.2f})")
            self.position = Position(
                symbol=self.underlying_symbol,
                entry_price=latest_close,
                quantity=config.DEFAULT_QUANTITY_LOTS,
                entry_time=str(latest_time),
            )
            self.traded_today = True
            self._submit_entry_order()

    def _submit_entry_order(self):
        self.client.place_order(
            security_id=self.contract["SECURITY_ID"],
            exchange_segment=config.EXCHANGE_SEGMENT_FUTURES,
            transaction_type="BUY",
            quantity=config.DEFAULT_QUANTITY_LOTS,
            order_type="MARKET",
            product_type="INTRADAY",
        )

    def _submit_exit_order(self):
        self.client.place_order(
            security_id=self.contract["SECURITY_ID"],
            exchange_segment=config.EXCHANGE_SEGMENT_FUTURES,
            transaction_type="SELL",
            quantity=config.DEFAULT_QUANTITY_LOTS,
            order_type="MARKET",
            product_type="INTRADAY",
        )


def run_live(symbols: list, poll_seconds: int = None):
    poll_seconds = poll_seconds or (config.CPR_TIMEFRAME_MINUTES * 60)
    ensure_log_dir()

    print(f"=== Starting live session. DRY_RUN={config.DRY_RUN} ===")
    if config.DRY_RUN:
        print(">>> DRY_RUN is ON: no real orders will be placed. <<<")
    else:
        print(">>> DRY_RUN is OFF: REAL ORDERS WILL BE PLACED. <<<")

    client = DhanClient()
    watchers = [SymbolWatcher(sym, client) for sym in symbols]

    # Setup phase: resolve contract + compute today's CPR for each symbol.
    # A failure on ONE symbol (bad symbol name, no contract found, Dhan API
    # error, etc.) must NOT take down monitoring for the rest of the watchlist.
    # Failed symbols are logged clearly and simply dropped from this session.
    active_watchers = []
    failed_symbols = []
    for w in watchers:
        try:
            w.resolve_contract()
            w.compute_today_cpr()
            active_watchers.append(w)
        except Exception as e:
            print(f"[{w.underlying_symbol}] SETUP FAILED, skipping this symbol for today: {e}")
            failed_symbols.append(w.underlying_symbol)

    watchers = active_watchers
    if failed_symbols:
        print(f"\nSkipped {len(failed_symbols)} symbol(s) due to setup errors: {failed_symbols}")
    if not watchers:
        print("No symbols survived setup - nothing to monitor. Exiting.")
        return

    print(f"\nActively monitoring {len(watchers)} symbol(s): {[w.underlying_symbol for w in watchers]}")
    print(f"Polling every {poll_seconds} seconds...")
    try:
        while True:
            now = dt.datetime.now().time()
            if now < dt.time(9, 15) or now > dt.time(15, 30):
                print("Outside market hours, sleeping...")
                time.sleep(60)
                continue

            still_active = []
            for w in watchers:
                try:
                    w.poll_and_act()
                    w.consecutive_poll_failures = 0
                    still_active.append(w)
                except Exception as e:
                    w.consecutive_poll_failures += 1
                    print(f"[{w.underlying_symbol}] ERROR during poll "
                          f"({w.consecutive_poll_failures}/{MAX_CONSECUTIVE_POLL_FAILURES}): {e}")
                    if w.consecutive_poll_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                        print(f"[{w.underlying_symbol}] Too many consecutive failures - "
                              f"dropping this symbol for the rest of the session.")
                        if w.position is not None and w.position.is_open():
                            print(f"[{w.underlying_symbol}] WARNING: this symbol had an OPEN "
                                  f"position when it was dropped. Check manually in Dhan's app - "
                                  f"this bot will no longer manage its SL/target/trailing.")
                    else:
                        still_active.append(w)

            watchers = still_active
            if not watchers:
                print("All symbols have been dropped due to repeated errors. Exiting.")
                break

            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("Stopped by user.")


if __name__ == "__main__":
    # Replace with your actual watchlist for today, e.g. from
    # chartink_scanner.get_today_universe_symbols(), or a manual list.
    WATCHLIST = ["RELIANCE", "TATASTEEL"]
    run_live(WATCHLIST)
