"""
trade_manager.py

Manages ONE open position's lifecycle for the CPR breakout strategy:

  1. Entry at price E. Initial SL = E - STOP_LOSS_RUPEES.
  2. While price < E + BREAKEVEN_TRIGGER_RUPEES: SL stays at entry-1.
  3. Once price >= E + BREAKEVEN_TRIGGER_RUPEES: SL moves to E (breakeven).
  4. Beyond that point, SL trails: SL = highest_price_seen - TRAIL_STEP_RUPEES,
     but SL never moves down, only up.
  5. Target = E + TARGET_RUPEES. If price >= target, exit at target (or you can
     choose to let the trail run past target - see ALLOW_RUN_PAST_TARGET below).

This module contains NO network calls. It is pure logic so it can be unit
tested and reused identically in both the backtester and the live bot -
this is important: the live and backtest behaviour must be the same code path,
otherwise a backtest result will not predict live behaviour.
"""

from dataclasses import dataclass, field
from enum import Enum
import config


class PositionState(Enum):
    OPEN_INITIAL_RISK = "OPEN_INITIAL_RISK"   # SL still at entry - 1
    OPEN_BREAKEVEN = "OPEN_BREAKEVEN"          # SL moved to entry
    OPEN_TRAILING = "OPEN_TRAILING"            # SL trailing behind highest price
    CLOSED_SL = "CLOSED_SL"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_TRAIL = "CLOSED_TRAIL"              # exited because trailing SL was hit


# If True, once target is reached the trade keeps running and only exits via
# trailing SL (lets winners run further). If False, it exits hard at target.
ALLOW_RUN_PAST_TARGET = False


@dataclass
class Position:
    symbol: str
    entry_price: float
    quantity: int
    entry_time: str

    stop_loss: float = field(init=False)
    target: float = field(init=False)
    highest_price_seen: float = field(init=False)
    state: PositionState = field(init=False)
    exit_price: float = None
    exit_time: str = None
    exit_reason: str = None

    def __post_init__(self):
        self.stop_loss = self.entry_price - config.STOP_LOSS_RUPEES
        self.target = self.entry_price + config.TARGET_RUPEES
        self.highest_price_seen = self.entry_price
        self.state = PositionState.OPEN_INITIAL_RISK

    def is_open(self) -> bool:
        return self.state in (
            PositionState.OPEN_INITIAL_RISK,
            PositionState.OPEN_BREAKEVEN,
            PositionState.OPEN_TRAILING,
        )

    def update(self, current_price: float, timestamp: str) -> bool:
        """
        Feed the latest price tick/candle close into the position.
        Returns True if the position closed as a result of this update.
        Call this on every new price update while the position is open.
        """
        if not self.is_open():
            return False

        if current_price > self.highest_price_seen:
            self.highest_price_seen = current_price

        gain = current_price - self.entry_price

        # --- state transitions for SL management ---
        if self.state == PositionState.OPEN_INITIAL_RISK:
            if gain >= config.BREAKEVEN_TRIGGER_RUPEES:
                self.stop_loss = self.entry_price
                self.state = PositionState.OPEN_BREAKEVEN

        if self.state == PositionState.OPEN_BREAKEVEN:
            trail_candidate = self.highest_price_seen - config.TRAIL_STEP_RUPEES
            if trail_candidate > self.stop_loss:
                self.stop_loss = trail_candidate
                self.state = PositionState.OPEN_TRAILING

        if self.state == PositionState.OPEN_TRAILING:
            trail_candidate = self.highest_price_seen - config.TRAIL_STEP_RUPEES
            if trail_candidate > self.stop_loss:
                self.stop_loss = trail_candidate

        # --- check target (if hard target exit enabled) ---
        if not ALLOW_RUN_PAST_TARGET and current_price >= self.target:
            self._close(self.target, timestamp, PositionState.CLOSED_TARGET, "target hit")
            return True

        # --- check stop loss (this also covers the trailing-SL exit case) ---
        if current_price <= self.stop_loss:
            reason = "trailing stop hit" if self.state == PositionState.OPEN_TRAILING else "stop loss hit"
            final_state = (PositionState.CLOSED_TRAIL
                            if self.state == PositionState.OPEN_TRAILING
                            else PositionState.CLOSED_SL)
            self._close(self.stop_loss, timestamp, final_state, reason)
            return True

        return False

    def _close(self, price: float, timestamp: str, state: PositionState, reason: str):
        self.exit_price = price
        self.exit_time = timestamp
        self.state = state
        self.exit_reason = reason

    def pnl(self) -> float:
        if self.exit_price is None:
            return (self.highest_price_seen - self.entry_price) * self.quantity  # unrealized, informational
        return (self.exit_price - self.entry_price) * self.quantity

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "quantity": self.quantity,
            "pnl": round(self.pnl(), 2) if self.exit_price else None,
            "state": self.state.value,
        }


if __name__ == "__main__":
    # Walk through a sample price path to sanity-check the state machine.
    pos = Position(symbol="TESTFUT", entry_price=1000.0, quantity=1, entry_time="09:30")
    path = [1000.5, 1001.0, 1002.5, 1003.5, 1004.0, 1003.2, 1005.5, 1004.6, 1006.0]
    for i, price in enumerate(path):
        closed = pos.update(price, timestamp=f"tick_{i}")
        print(f"price={price:>8} state={pos.state.value:<20} SL={pos.stop_loss:.2f} closed={closed}")
        if closed:
            break
    print("\nFinal summary:", pos.summary())
