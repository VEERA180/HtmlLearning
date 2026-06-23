"""
VWAP Touch-and-Reverse signal logic, adapted from the backtest to run
candle-by-candle in a live loop instead of vectorized over a full
historical DataFrame.

This module ONLY decides "LONG signal" / "SHORT signal" / "no signal"
based on NIFTY's own price action vs its session VWAP. It knows nothing
about options, orders, or money - that separation is intentional so the
signal logic can be unit-tested/reasoned about on its own.

Mirrors the backtest's pandas-vectorized logic:
    for i in range(2, len(df)):
        row, prev_row, prev2_row = df.iloc[i], df.iloc[i-1], df.iloc[i-2]
        came_from_below = prev2_row.close < prev2_row.vwap
        came_from_above  = prev2_row.close > prev2_row.vwap
        touched_last     = prev_row.touched_vwap
        if came_from_below and touched_last and row.close > row.vwap: LONG
        if came_from_above and touched_last and row.close < row.vwap: SHORT

Here, "row" = the newest candle just closed, "prev_row" = one before it,
"prev2_row" = two before it. Kept as an explicit list (not minus1/minus2
fields) specifically because shifting fields by hand was a source of an
off-by-one bug during testing - a list with clear index semantics is
harder to get wrong.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import config


@dataclass
class Candle:
    timestamp: datetime
    close: float
    high: float
    low: float
    vwap_at_time: Optional[float] = None   # VWAP value AS OF this candle's close


@dataclass
class VwapState:
    """Rolling VWAP-proxy state for the current trading session (resets daily)."""
    session_date: object = None
    cum_typical_sum: float = 0.0
    cum_count: int = 0
    current_vwap: Optional[float] = None

    # Most recent candles, OLDEST FIRST. We only ever need the last 3 to
    # evaluate a signal, so we cap the list at 3 entries.
    history: List[Candle] = field(default_factory=list)

    def reset_for_new_day(self, session_date):
        self.session_date = session_date
        self.cum_typical_sum = 0.0
        self.cum_count = 0
        self.current_vwap = None
        self.history = []

    def update_with_candle(self, candle: Candle):
        """
        Feed one completed 5-min candle into the rolling VWAP-proxy.
        Stamps candle.vwap_at_time with the VWAP value AFTER this candle
        is absorbed (matching the backtest, where each row's VWAP
        includes that row's own typical price).
        """
        candle_date = candle.timestamp.date()
        if self.session_date != candle_date:
            self.reset_for_new_day(candle_date)

        typical = (candle.high + candle.low + candle.close) / 3.0
        self.cum_typical_sum += typical
        self.cum_count += 1
        self.current_vwap = self.cum_typical_sum / self.cum_count
        candle.vwap_at_time = self.current_vwap

        self.history.append(candle)
        if len(self.history) > 3:
            self.history.pop(0)


def touched_vwap(candle: Candle) -> bool:
    if candle is None or candle.vwap_at_time is None:
        return False
    return abs(candle.close - candle.vwap_at_time) <= config.VWAP_TOUCH_BUFFER


def check_signal(state: VwapState):
    """
    Call this AFTER state.update_with_candle(newest_candle) for every
    newly closed candle. Needs at least 3 candles of history this session
    (i.e. won't fire on the first two candles of the day).

    Returns "LONG", "SHORT", or None.
    """
    if len(state.history) < 3:
        return None

    prev2, prev, current = state.history[-3], state.history[-2], state.history[-1]

    came_from_below = prev2.close < prev2.vwap_at_time
    came_from_above = prev2.close > prev2.vwap_at_time
    touched_last = touched_vwap(prev)

    if came_from_below and touched_last and current.close > current.vwap_at_time:
        return "LONG"
    if came_from_above and touched_last and current.close < current.vwap_at_time:
        return "SHORT"
    return None


def is_within_entry_window(ts: datetime) -> bool:
    start = ts.replace(hour=config.ENTRY_START_HOUR, minute=config.ENTRY_START_MIN, second=0, microsecond=0)
    end = ts.replace(hour=config.ENTRY_END_HOUR, minute=config.ENTRY_END_MIN, second=0, microsecond=0)
    return start <= ts <= end


def is_past_squareoff(ts: datetime) -> bool:
    cutoff = ts.replace(hour=config.SQUARE_OFF_HOUR, minute=config.SQUARE_OFF_MIN, second=0, microsecond=0)
    return ts >= cutoff
