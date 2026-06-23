"""
Main live loop for the NIFTY VWAP touch-and-reverse options bot.

FLOW EACH 5-MINUTE CANDLE BOUNDARY:
  1. Check kill-switch file / EOD square-off time first, always.
  2. Pull NIFTY LTP, build the "current candle" incrementally.
  3. On candle close (every 5 min), feed it into VwapState.
  4. If a position is open: update trailing stop, check SL, exit if hit.
  5. If flat AND risk_manager says we're allowed to enter AND a signal
     fires: resolve ATM option, place BUY order, record OpenPosition.
  6. Persist state to disk after every meaningful change.

STARTUP RECONCILIATION:
  Before the loop starts, this script:
    - Loads saved state from disk (state.py)
    - Calls dhan_client.get_open_positions() to see what Dhan ACTUALLY
      shows as open right now
    - If saved state says "position open" but Dhan shows flat -> the
      position was closed while the bot was down (e.g. SL hit via some
      other channel, or manual square-off). We log this loudly and start
      flat, trusting Dhan's broker-side truth over our local file.
    - If Dhan shows an open option position but our state file is empty
      -> we DO NOT silently adopt it (we don't know its original SL/
      trailing state). We log a loud warning and require you to either
      close it manually or restart with a documented entry. This bot
      will not guess parameters for an existing position.

Run with:  python main.py
Stop any time with Ctrl+C (current candle's position is left exactly
as-is; nothing is force-closed on Ctrl+C - use the KILL_SWITCH file or
Dhan's own kill switch / exit_all_positions if you need an emergency
flatten).
"""

import logging
import os
import sys
import time
from datetime import datetime

import config
import dhan_client
import strategy
import risk_manager
import state

# ----------------------------------------------------------------------
# Logging setup - logs to both console and a daily file
# ----------------------------------------------------------------------
log_filename = os.path.join(config.LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")


def reconcile_startup_state():
    """
    Returns (day_state, open_position_or_None).
    Exits the process if reconciliation finds an ambiguous situation
    that the bot should not guess its way through.
    """
    saved = state.load_state()
    day_state = risk_manager.DayState()

    today = datetime.now().date()
    saved_date = saved.get("trade_date")
    if saved_date == str(today):
        day_state.trade_date = today
        day_state.trades_today = saved.get("trades_today", 0)
        day_state.daily_pnl_rupees = saved.get("daily_pnl_rupees", 0.0)
    else:
        day_state.reset_for_new_day(today)

    try:
        live_positions = dhan_client.get_open_positions()
    except dhan_client.DhanClientError as e:
        logger.error("Could not fetch live positions from Dhan on startup: %s", e)
        logger.error("Refusing to start blind. Fix connectivity/auth and restart.")
        sys.exit(1)

    saved_pos = saved.get("open_position")

    if saved_pos is None and not live_positions:
        logger.info("Startup reconciliation: flat on both sides. Clean start.")
        return day_state, None

    if saved_pos is not None and not live_positions:
        logger.warning(
            "Startup reconciliation: saved state had an OPEN position (%s) "
            "but Dhan shows NO open positions. Assuming it was closed while "
            "the bot was offline (SL/manual exit). Starting flat. "
            "Check your Dhan tradebook to confirm the exit price/reason.",
            saved_pos.get("trading_symbol"),
        )
        return day_state, None

    if saved_pos is None and live_positions:
        logger.error(
            "Startup reconciliation: Dhan shows %d open position(s) but the "
            "bot has NO saved record of opening them: %s",
            len(live_positions), live_positions,
        )
        logger.error(
            "Refusing to adopt unknown positions automatically (no known "
            "entry price / SL / trailing state to manage them with). "
            "Please manually close these in the Dhan app/web UI, or delete "
            "the position from your account, before restarting the bot."
        )
        sys.exit(1)

    # Both sides have something - rebuild OpenPosition from saved state,
    # but warn loudly so you double check it matches.
    logger.warning(
        "Startup reconciliation: both saved state and Dhan show an open "
        "position. Resuming management of: %s. VERIFY this matches your "
        "Dhan positions tab before trusting the bot unattended.",
        saved_pos.get("trading_symbol"),
    )
    pos = risk_manager.OpenPosition(
        side=saved_pos["side"],
        security_id=saved_pos["security_id"],
        trading_symbol=saved_pos["trading_symbol"],
        entry_price=saved_pos["entry_price"],
        entry_time=saved_pos["entry_time"],
        quantity=saved_pos["quantity"],
        sl_price=saved_pos["sl_price"],
        best_price=saved_pos["best_price"],
        trailing_active=saved_pos.get("trailing_active", False),
    )
    return day_state, pos


def enter_position(signal_side, nifty_spot, vwap_state):
    """
    signal_side: "LONG" or "SHORT" (direction of the NIFTY signal)
    LONG signal -> buy ATM CE.  SHORT signal -> buy ATM PE.
    """
    option_type = "CE" if signal_side == "LONG" else "PE"

    try:
        expiries = dhan_client.get_option_expiry_list()
        if not expiries:
            logger.error("No expiries returned from Dhan - skipping entry this candle.")
            return None
        nearest_expiry = expiries[0]

        option = dhan_client.resolve_atm_option(nifty_spot, option_type, nearest_expiry)
    except dhan_client.DhanClientError as e:
        logger.error("Could not resolve ATM %s option - skipping entry: %s", option_type, e)
        return None

    if option["ltp"] is None:
        logger.error("ATM option resolved but LTP missing - skipping entry: %s", option)
        return None

    quantity = config.LOT_SIZE   # 1 lot; do not scale up without deliberately changing this

    order_result = dhan_client.place_order(
        security_id=option["security_id"],
        transaction_type="BUY",
        quantity=quantity,
        trading_symbol=option["trading_symbol"],
    )

    entry_price = option["ltp"]
    position = risk_manager.OpenPosition(
        side=signal_side,
        security_id=option["security_id"],
        trading_symbol=option["trading_symbol"],
        entry_price=entry_price,
        entry_time=datetime.now().isoformat(),
        quantity=quantity,
        sl_price=risk_manager.initial_sl_price(entry_price),
        best_price=entry_price,
        trailing_active=False,
    )

    logger.info(
        "ENTERED %s %s @ premium=%.2f | initial SL=%.2f | qty=%d | order=%s",
        signal_side, option["trading_symbol"], entry_price,
        position.sl_price, quantity, order_result.get("order_id"),
    )
    return position


def exit_position(position, reason, day_state):
    try:
        current_premium = dhan_client.get_option_ltp(position.security_id)
    except dhan_client.DhanClientError as e:
        logger.error(
            "Could not fetch exit LTP for %s, using last known sl_price as exit estimate: %s",
            position.trading_symbol, e,
        )
        current_premium = position.sl_price

    order_result = dhan_client.place_order(
        security_id=position.security_id,
        transaction_type="SELL",
        quantity=position.quantity,
        trading_symbol=position.trading_symbol,
    )

    pnl_points = current_premium - position.entry_price
    pnl_rupees = pnl_points * position.quantity

    logger.info(
        "EXITED %s %s | reason=%s | exit_premium=%.2f | pnl=%.2f pts -> Rs.%.0f | order=%s",
        position.side, position.trading_symbol, reason, current_premium,
        pnl_points, pnl_rupees, order_result.get("order_id"),
    )

    risk_manager.record_trade_result(day_state, pnl_rupees)


def main():
    logger.info("=" * 60)
    logger.info("NIFTY VWAP Touch-and-Reverse bot starting. DRY_RUN=%s", config.DRY_RUN)
    if config.DRY_RUN:
        logger.info("Running in DRY_RUN mode - no real orders will be placed.")
    else:
        logger.warning("Running in LIVE mode - REAL ORDERS WILL BE PLACED WITH REAL MONEY.")
    logger.info("=" * 60)

    day_state, open_position = reconcile_startup_state()
    vwap_state = strategy.VwapState()

    current_candle_data = {"high": None, "low": None, "close": None, "open_minute": None}

    try:
        while True:
            if state.kill_switch_file_present():
                logger.warning("KILL_SWITCH file detected. Stopping the bot. (Open positions are NOT auto-closed - check Dhan manually if needed.)")
                break

            now = datetime.now()
            day_state.ensure_current_day(now.date())

            # ---- EOD square-off ----
            if strategy.is_past_squareoff(now):
                if open_position is not None:
                    exit_position(open_position, "EOD_SQUAREOFF", day_state)
                    open_position = None
                    state.save_state(day_state, open_position)
                logger.info("Past square-off time (%02d:%02d). Idling until next day.",
                            config.SQUARE_OFF_HOUR, config.SQUARE_OFF_MIN)
                time.sleep(60)
                continue

            # ---- pull current NIFTY price ----
            try:
                nifty_ltp = dhan_client.get_nifty_ltp()
            except dhan_client.DhanClientError as e:
                logger.error("Failed to fetch NIFTY LTP, will retry: %s", e)
                time.sleep(config.POLL_INTERVAL_SECONDS)
                continue

            minute_bucket = now.minute - (now.minute % 5)

            if current_candle_data["open_minute"] is None:
                current_candle_data.update(high=nifty_ltp, low=nifty_ltp, close=nifty_ltp, open_minute=minute_bucket)
            elif minute_bucket != current_candle_data["open_minute"]:
                # candle just closed - finalize it
                closed_candle = strategy.Candle(
                    timestamp=now,
                    close=current_candle_data["close"],
                    high=current_candle_data["high"],
                    low=current_candle_data["low"],
                )
                vwap_state.update_with_candle(closed_candle)
                logger.info(
                    "Candle closed: O-ish=%.2f H=%.2f L=%.2f C=%.2f | VWAP=%.2f",
                    current_candle_data["close"], current_candle_data["high"],
                    current_candle_data["low"], current_candle_data["close"],
                    vwap_state.current_vwap,
                )

                # ---- manage open position on this new candle ----
                if open_position is not None:
                    try:
                        current_premium = dhan_client.get_option_ltp(open_position.security_id)
                        open_position = risk_manager.update_trailing_stop(open_position, current_premium)
                        if risk_manager.should_exit_on_stop(open_position, current_premium):
                            reason = "TRAIL_STOP_HIT" if open_position.trailing_active else "INITIAL_SL_HIT"
                            exit_position(open_position, reason, day_state)
                            open_position = None
                    except dhan_client.DhanClientError as e:
                        logger.error("Could not update/check position SL this candle: %s", e)

                # ---- check for new entry signal ----
                if (
                    open_position is None
                    and strategy.is_within_entry_window(now)
                    and risk_manager.can_enter_new_trade(day_state)
                ):
                    signal = strategy.check_signal(vwap_state)
                    if signal is not None:
                        open_position = enter_position(signal, nifty_ltp, vwap_state)

                state.save_state(day_state, open_position)

                # start fresh candle accumulation
                current_candle_data.update(high=nifty_ltp, low=nifty_ltp, close=nifty_ltp, open_minute=minute_bucket)
            else:
                # still inside the current candle - just update high/low/close
                current_candle_data["high"] = max(current_candle_data["high"], nifty_ltp)
                current_candle_data["low"] = min(current_candle_data["low"], nifty_ltp)
                current_candle_data["close"] = nifty_ltp

                # also check SL intra-candle, not just on candle close, so a
                # sharp move doesn't blow through SL before the candle closes
                if open_position is not None:
                    try:
                        current_premium = dhan_client.get_option_ltp(open_position.security_id)
                        open_position = risk_manager.update_trailing_stop(open_position, current_premium)
                        if risk_manager.should_exit_on_stop(open_position, current_premium):
                            reason = "TRAIL_STOP_HIT" if open_position.trailing_active else "INITIAL_SL_HIT"
                            exit_position(open_position, reason, day_state)
                            open_position = None
                            state.save_state(day_state, open_position)
                    except dhan_client.DhanClientError as e:
                        logger.error("Could not check intra-candle SL: %s", e)

            time.sleep(config.POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Ctrl+C received. Shutting down. Open position (if any) left as-is on Dhan.")
        state.save_state(day_state, open_position)


if __name__ == "__main__":
    main()
