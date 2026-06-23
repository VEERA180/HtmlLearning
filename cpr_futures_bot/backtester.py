"""
backtester.py

Backtests the CPR narrow + 15-min breakout strategy using Yahoo Finance data
via the `yfinance` library.

Run this on a machine with internet access:
    pip install yfinance pandas --break-system-packages
    python backtester.py

IMPORTANT LIMITATIONS to know about before trusting backtest output:

1. Yahoo Finance only provides ~60 days of 15-minute intraday history per
   request (config.YF_MAX_INTRADAY_DAYS=59 is set conservatively below that).
   This means you cannot backtest this strategy over years using 15m data
   from Yahoo - only the last couple of months. For a longer backtest you'd
   need Dhan's own historical intraday data (5 years available) instead.

2. This backtest assumes the strategy trades the CASH/equity symbol (e.g.
   "RELIANCE.NS") as a stand-in for futures price action, since Yahoo does
   not provide Indian stock FUTURES data at all. Cash and futures prices
   are close but NOT identical (futures carry a basis/premium that varies
   with time-to-expiry and interest rates). Treat backtest P&L as directionally
   informative, not as an exact prediction of what the futures contract would
   have done. This is a real gap - flagging it clearly rather than pretending
   cash-data backtest results map 1:1 onto futures trading.

3. CPR for "today" is computed from the previous trading day's daily
   High/Low/Close, which this script derives correctly by resampling.
"""

import pandas as pd
import config
from cpr_engine import calculate_cpr
from trade_manager import Position

try:
    import yfinance as yf
except ImportError:
    yf = None


def fetch_intraday_15m(symbol_ns: str, period_days: int = None) -> pd.DataFrame:
    """
    symbol_ns: Yahoo ticker, e.g. "RELIANCE.NS" for NSE cash equity.
    Returns a DataFrame indexed by datetime with columns Open/High/Low/Close/Volume.
    """
    if yf is None:
        raise ImportError("yfinance is not installed. Run: pip install yfinance --break-system-packages")

    period_days = period_days or config.YF_MAX_INTRADAY_DAYS
    df = yf.download(
        symbol_ns,
        period=f"{period_days}d",
        interval=config.YF_INTRADAY_INTERVAL,
        progress=False,
    )
    if df.empty:
        raise RuntimeError(f"No data returned for {symbol_ns}. Check the symbol is correct (needs .NS suffix).")

    # yfinance sometimes returns MultiIndex columns when given certain params - flatten if so
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def compute_daily_hlc(intraday_df: pd.DataFrame) -> pd.DataFrame:
    """
    Resamples 15-min intraday data into daily High/Low/Close so we can
    compute each day's CPR from the PREVIOUS day's daily candle.
    """
    daily = intraday_df.resample("1D").agg({
        "High": "max",
        "Low": "min",
        "Close": "last",
    }).dropna()
    return daily


def run_backtest(symbol_ns: str, period_days: int = None, quantity: int = 1) -> dict:
    """
    Runs the full CPR-narrow + 15m-close-breakout strategy over historical
    15-min data for one symbol and returns a summary + list of trade records.

    Only ONE position is held at a time per symbol (no pyramiding), and only
    ONE entry is taken per symbol per day, even if price gets stopped out and
    re-triggers the breakout condition again later the same session. This is
    a deliberate risk control (confirmed with the user) to avoid repeated
    whipsaw losses on a choppy narrow-CPR day.
    """
    intraday = fetch_intraday_15m(symbol_ns, period_days)
    daily_hlc = compute_daily_hlc(intraday)

    trades = []
    open_position = None
    traded_dates = set()  # enforces max 1 entry per stock per day

    # Build a quick lookup: for each calendar date, what is the PRECEDING
    # trading day's daily H/L/C (needed for CPR)?
    daily_dates = list(daily_hlc.index.date)

    for current_time, row in intraday.iterrows():
        current_date = current_time.date()

        # find index of current_date in daily_dates, then look at the previous entry
        if current_date not in daily_dates:
            continue
        idx = daily_dates.index(current_date)
        if idx == 0:
            continue  # no previous day available yet, skip (first day in our window)

        prev_day = daily_hlc.iloc[idx - 1]
        cpr = calculate_cpr(prev_day["High"], prev_day["Low"], prev_day["Close"])
        reference_close = prev_day["Close"]

        candle_close = row["Close"]
        timestamp_str = str(current_time)

        # --- manage an open position first ---
        if open_position is not None and open_position.is_open():
            closed = open_position.update(candle_close, timestamp_str)
            if closed:
                trades.append(open_position.summary())
                open_position = None
            continue  # one position at a time - don't also evaluate entry this same candle

        # --- enforce max 1 entry per stock per day ---
        if current_date in traded_dates:
            continue

        # --- check for a fresh entry signal ---
        is_narrow = cpr.is_narrow(reference_close)
        is_breakout = candle_close > cpr.tc

        if is_narrow and is_breakout:
            open_position = Position(
                symbol=symbol_ns,
                entry_price=candle_close,
                quantity=quantity,
                entry_time=timestamp_str,
            )
            traded_dates.add(current_date)

    # If a position is still open at the end of the backtest window, record it as unrealized
    if open_position is not None and open_position.is_open():
        trades.append({**open_position.summary(), "note": "still open at end of backtest window"})

    total_pnl = sum(t["pnl"] for t in trades if t.get("pnl") is not None)
    closed_trades = [t for t in trades if t.get("pnl") is not None]
    wins = [t for t in closed_trades if t["pnl"] > 0]

    summary = {
        "symbol": symbol_ns,
        "total_trades": len(closed_trades),
        "wins": len(wins),
        "win_rate_pct": round(100 * len(wins) / len(closed_trades), 1) if closed_trades else None,
        "total_pnl_per_share": round(total_pnl, 2),
        "trades": trades,
    }
    return summary


if __name__ == "__main__":
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"Backtesting CPR breakout strategy on {symbol} ...")
    result = run_backtest(symbol)

    print(f"\nSymbol: {result['symbol']}")
    print(f"Total trades: {result['total_trades']}")
    print(f"Wins: {result['wins']}  Win rate: {result['win_rate_pct']}%")
    print(f"Total P&L per share: Rs {result['total_pnl_per_share']}")
    print("\nTrade log:")
    for t in result["trades"]:
        print(t)
