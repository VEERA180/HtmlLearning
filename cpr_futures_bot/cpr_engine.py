"""
cpr_engine.py

Central Pivot Range (CPR) math, plus narrow/wide classification and
breakout detection on 15-min candles.

CPR formula (standard, computed from the PREVIOUS day's daily H, L, C),
using the Frank Ochoa / PivotBoss definition that is the dominant one in
Indian retail trading content and on platforms like Zerodha/TradingView:
    Pivot (P)        = (High + Low + Close) / 3
    raw_bc           = (High + Low) / 2
    raw_tc           = (Pivot - raw_bc) + Pivot   [equivalently: 2*Pivot - raw_bc]

IMPORTANT - confirmed via direct testing AND multiple independent sources:
the raw formula above does NOT guarantee raw_tc >= raw_bc. When Close sits
far enough below the High-Low midpoint, raw_tc can come out LOWER than
raw_bc (this happened with real data: H=1327.1, L=1325.0, C=1325.0 produced
raw_bc=1326.05, raw_tc=1325.35 - i.e. backwards).

Every real charting platform (TradingView, Zerodha Kite, PivotBoss's own
indicator) handles this the same way: whichever of the two raw values is
numerically HIGHER is always labeled/plotted as TC, and the lower one as
BC, regardless of which formula produced which number. This module follows
that same convention - it assigns tc/bc by magnitude AFTER computing the
raw formula values, so tc >= bc is always true here, matching what you
would see on a real chart.
"""

from dataclasses import dataclass
import config


@dataclass
class CPRLevels:
    pivot: float
    bc: float
    tc: float

    @property
    def width(self) -> float:
        return self.tc - self.bc

    def width_pct_of(self, close_price: float) -> float:
        """CPR width as a percentage of a reference close price."""
        if close_price == 0:
            return float("inf")
        return (self.width / close_price) * 100.0

    def is_narrow(self, close_price: float, threshold_pct: float = None) -> bool:
        threshold_pct = threshold_pct or config.CPR_NARROW_THRESHOLD_PCT
        return self.width_pct_of(close_price) < threshold_pct


def calculate_cpr(prev_high: float, prev_low: float, prev_close: float) -> CPRLevels:
    """
    Calculate CPR levels from the PREVIOUS trading day's daily High/Low/Close.

    tc and bc are assigned by MAGNITUDE (higher value = TC, lower value = BC),
    matching real charting platform convention - see module docstring for why
    this matters and is not optional.
    """
    pivot = (prev_high + prev_low + prev_close) / 3.0
    raw_bc = (prev_high + prev_low) / 2.0
    raw_tc = (2 * pivot) - raw_bc

    tc = max(raw_tc, raw_bc)
    bc = min(raw_tc, raw_bc)

    return CPRLevels(pivot=pivot, bc=bc, tc=tc)


def is_breakout_candle(candle_close: float, cpr: CPRLevels) -> bool:
    """
    Your rule: entry triggers when a 15-min candle CLOSES above CPR resistance (TC).
    This function checks a single already-closed candle's close price.
    """
    return candle_close > cpr.tc


def evaluate_stock(prev_day_high: float, prev_day_low: float, prev_day_close: float,
                    today_15m_close: float, reference_close: float):
    """
    Convenience wrapper: given yesterday's daily HLC and today's latest CLOSED
    15-min candle close, returns a dict describing whether this stock currently
    qualifies for entry per your strategy.

    reference_close is used to compute CPR width as a percentage - use the most
    recent daily close (i.e. prev_day_close) per the 0.5% rule discussed.
    """
    cpr = calculate_cpr(prev_day_high, prev_day_low, prev_day_close)
    narrow = cpr.is_narrow(reference_close)
    breakout = is_breakout_candle(today_15m_close, cpr)

    return {
        "pivot": round(cpr.pivot, 2),
        "bc": round(cpr.bc, 2),
        "tc": round(cpr.tc, 2),
        "width": round(cpr.width, 2),
        "width_pct": round(cpr.width_pct_of(reference_close), 3),
        "is_narrow": narrow,
        "is_breakout": breakout,
        "entry_signal": narrow and breakout,
    }


if __name__ == "__main__":
    # Quick sanity check with made-up numbers
    sample = evaluate_stock(
        prev_day_high=1050.0,
        prev_day_low=1030.0,
        prev_day_close=1040.0,
        today_15m_close=1052.5,
        reference_close=1040.0,
    )
    print("Sample CPR evaluation:", sample)
