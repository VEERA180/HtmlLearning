"""
Risk management, kept deliberately separate from strategy.py.

The reasoning: a bug in signal detection should NEVER be able to bypass
position sizing, stop-losses, or the daily loss cap. This module is the
single source of truth for "are we allowed to enter" and "should we exit
right now", and it operates on OPTION PREMIUM points/rupees, not index
points - that conversion already happened by the time anything reaches
here.
"""

from dataclasses import dataclass
from datetime import date
import logging

import config

logger = logging.getLogger("risk_manager")


@dataclass
class OpenPosition:
    side: str                 # "LONG" (we are long the option, i.e. bought CE or PE)
    security_id: str
    trading_symbol: str
    entry_price: float        # option premium at entry
    entry_time: object
    quantity: int              # total units = lots * lot_size
    sl_price: float            # current stop price (premium terms), only ever tightens favorably
    best_price: float          # best premium seen since entry
    trailing_active: bool = False


@dataclass
class DayState:
    trade_date: date = None
    trades_today: int = 0
    daily_pnl_rupees: float = 0.0

    def reset_for_new_day(self, trade_date):
        self.trade_date = trade_date
        self.trades_today = 0
        self.daily_pnl_rupees = 0.0

    def ensure_current_day(self, current_date):
        if self.trade_date != current_date:
            logger.info("New trading day detected (%s) - resetting daily counters", current_date)
            self.reset_for_new_day(current_date)


def can_enter_new_trade(day_state: DayState) -> bool:
    if day_state.trades_today >= config.MAX_TRADES_PER_DAY:
        logger.info("Blocked entry: MAX_TRADES_PER_DAY (%d) reached", config.MAX_TRADES_PER_DAY)
        return False
    if day_state.daily_pnl_rupees <= -config.DAILY_LOSS_CAP_RUPEES:
        logger.warning(
            "Blocked entry: DAILY_LOSS_CAP_RUPEES (%.0f) hit, current daily P&L = %.0f",
            config.DAILY_LOSS_CAP_RUPEES, day_state.daily_pnl_rupees,
        )
        return False
    return True


def initial_sl_price(entry_premium: float) -> float:
    """
    We are always BUYING an option (CE for a long-NIFTY signal, PE for a
    short-NIFTY signal) - so our position is always 'long premium', and a
    loss happens when the premium DROPS. SL is therefore always below
    entry, regardless of whether the underlying signal was LONG or SHORT.
    """
    return max(entry_premium - config.PER_TRADE_SL_POINTS, 0.01)


def update_trailing_stop(position: OpenPosition, current_premium: float) -> OpenPosition:
    """
    Update best_price / sl_price based on the latest premium tick.
    Stop only ever moves up (more favorable), matching the backtest logic.
    """
    if current_premium > position.best_price:
        position.best_price = current_premium

    favorable_move = position.best_price - position.entry_price
    if favorable_move >= config.TRAIL_TRIGGER_POINTS:
        position.trailing_active = True
        new_sl = position.best_price - config.TRAIL_DISTANCE_POINTS
        if new_sl > position.sl_price:
            position.sl_price = new_sl

    return position


def should_exit_on_stop(position: OpenPosition, current_premium: float) -> bool:
    return current_premium <= position.sl_price


def record_trade_result(day_state: DayState, pnl_rupees: float):
    day_state.daily_pnl_rupees += pnl_rupees
    day_state.trades_today += 1
    logger.info(
        "Trade closed. P&L=%.2f | Daily P&L now=%.2f | Trades today=%d",
        pnl_rupees, day_state.daily_pnl_rupees, day_state.trades_today,
    )
    if day_state.daily_pnl_rupees <= -config.DAILY_LOSS_CAP_RUPEES:
        logger.warning(
            "DAILY LOSS CAP HIT (%.0f). No further entries today.",
            config.DAILY_LOSS_CAP_RUPEES,
        )
