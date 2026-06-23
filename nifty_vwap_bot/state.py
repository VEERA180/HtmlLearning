"""
Minimal JSON-based persistence for bot state across restarts.

Why this exists: if the bot crashes or you restart it mid-session while
a position is open, it MUST NOT just forget about that position and
potentially fire a second entry on top of it. On startup, main.py should
always reconcile this saved state against config.dhan_client.get_open_positions()
before doing anything else.
"""

import json
import os
import logging
from datetime import datetime

import config

logger = logging.getLogger("state")


def _default_state():
    return {
        "trade_date": None,
        "trades_today": 0,
        "daily_pnl_rupees": 0.0,
        "open_position": None,   # dict or None
    }


def load_state():
    if not os.path.exists(config.STATE_FILE):
        return _default_state()
    try:
        with open(config.STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Could not read state file (%s) - starting fresh. Error: %s", config.STATE_FILE, e)
        return _default_state()


def save_state(day_state, open_position):
    payload = {
        "trade_date": str(day_state.trade_date) if day_state.trade_date else None,
        "trades_today": day_state.trades_today,
        "daily_pnl_rupees": day_state.daily_pnl_rupees,
        "open_position": None,
        "saved_at": datetime.now().isoformat(),
    }
    if open_position is not None:
        payload["open_position"] = {
            "side": open_position.side,
            "security_id": open_position.security_id,
            "trading_symbol": open_position.trading_symbol,
            "entry_price": open_position.entry_price,
            "entry_time": str(open_position.entry_time),
            "quantity": open_position.quantity,
            "sl_price": open_position.sl_price,
            "best_price": open_position.best_price,
            "trailing_active": open_position.trailing_active,
        }

    tmp_path = config.STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, config.STATE_FILE)


def kill_switch_file_present():
    return os.path.exists(config.KILL_FILE)
