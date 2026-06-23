"""
Central configuration for the NIFTY VWAP Touch-and-Reverse options bot.

EVERYTHING that controls risk or behavior lives here so you never have to
go hunting through logic files to find a number that matters.
"""

import os

# ============================================================
#  SAFETY SWITCH - read this every single time before running
# ============================================================
# True  -> bot computes signals, "would place" orders, but NEVER calls
#          the real Dhan order API. Safe to leave running unattended.
# False -> bot places REAL orders with REAL money. No confirmation prompt.
#
# Flip this to False only after you have watched DRY_RUN logs for at
# least several full trading days and they match what you expect.
DRY_RUN = False

# ============================================================
#  DHAN CREDENTIALS
# ============================================================
# Dhan access tokens you generate manually from the developer portal
# expire (commonly within ~24 hours), so re-exporting an environment
# variable every morning is annoying and easy to forget mid-session.
#
# Instead, credentials are read from a local file: credentials.txt
# (same folder as this config.py). Each morning, just open that file
# and paste in the new access token Dhan gives you - nothing else
# needs to change, and you never re-run any export/$env command.
#
# credentials.txt format (plain text, one per line):
#   CLIENT_ID=your_client_id_here
#   ACCESS_TOKEN=your_new_token_here
#
# This file is read fresh every time the bot starts. It is NOT
# committed to git (see .gitignore) and should never be shared.
#
# Environment variables still work too, and take priority over the
# file, in case you ever do want to script an automated refresh later.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.txt")


def _load_credentials_file():
    creds = {}
    if not os.path.exists(CREDENTIALS_FILE):
        return creds
    with open(CREDENTIALS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()
    return creds


_file_creds = _load_credentials_file()

DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID") or _file_creds.get("CLIENT_ID")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN") or _file_creds.get("ACCESS_TOKEN")

if not DHAN_CLIENT_ID or not DHAN_ACCESS_TOKEN:
    raise RuntimeError(
        f"DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not found.\n"
        f"Either set them as environment variables, OR create a file at:\n"
        f"  {CREDENTIALS_FILE}\n"
        f"containing:\n"
        f"  CLIENT_ID=your_client_id\n"
        f"  ACCESS_TOKEN=your_access_token\n"
        "Set them before running the bot. Do not hardcode credentials in this file."
    )

# ------------------------------------------------------------
# Credential staleness check (matters most for unattended /
# Task Scheduler runs, where nobody is watching the console)
# ------------------------------------------------------------
# Dhan access tokens generated manually expire roughly every 24h.
# If you forgot to update credentials.txt this morning, the bot
# would otherwise just fail confusingly partway through the day
# with no one noticing. Instead: if the file's last-modified date
# is not TODAY, refuse to start at all and say exactly why.
#
# This only checks the FILE was edited today - it cannot verify the
# token itself is still valid (only Dhan's API can tell you that).
# If you're using environment variables instead of the file, this
# check is skipped (you're presumably setting them fresh each run).
if not os.environ.get("DHAN_ACCESS_TOKEN") and os.path.exists(CREDENTIALS_FILE):
    import datetime as _dt
    file_mtime = _dt.date.fromtimestamp(os.path.getmtime(CREDENTIALS_FILE))
    today = _dt.date.today()
    if file_mtime != today:
        raise RuntimeError(
            f"credentials.txt was last modified on {file_mtime}, not today ({today}).\n"
            f"Dhan access tokens expire daily - this file almost certainly has a "
            f"stale token.\n"
            f"Open {CREDENTIALS_FILE}, paste in today's fresh ACCESS_TOKEN from the "
            f"Dhan developer portal, save, and run the bot again.\n"
            f"(This check exists so an unattended/scheduled run doesn't silently "
            f"fail mid-session on an expired token with nobody watching.)"
        )

# ============================================================
#  INSTRUMENT IDENTIFIERS
# ============================================================
# NIFTY 50 index security_id on Dhan (IDX_I segment). Verify this is
# still correct against Dhan's instrument master CSV before first run:
# https://images.dhan.co/api-data/api-scrip-master.csv
NIFTY_INDEX_SECURITY_ID = "13"
NIFTY_INDEX_EXCHANGE_SEGMENT = "IDX_I"

# Underlying scrip code used by the Option Chain API for NIFTY.
NIFTY_UNDERLYING_SCRIP = 13
NIFTY_UNDERLYING_SEGMENT = "IDX_I"

# Option contracts trade on NSE F&O segment
OPTION_EXCHANGE_SEGMENT = "NSE_FNO"

# ============================================================
#  STRATEGY PARAMETERS (carried over from the backtest)
# ============================================================
LOT_SIZE = 65                          # VERIFY current NIFTY lot size before trusting this
PER_TRADE_SL_RUPEES = 750
PER_TRADE_SL_POINTS = PER_TRADE_SL_RUPEES / LOT_SIZE     # applied to the OPTION premium, not the index
TRAIL_TRIGGER_POINTS = 5               # option premium points
TRAIL_DISTANCE_POINTS = 5              # option premium points
DAILY_LOSS_CAP_RUPEES = 1500
MAX_TRADES_PER_DAY = 10
VWAP_TOUCH_BUFFER = 1.0                # NIFTY index points

# Trading window
ENTRY_START_HOUR = 9
ENTRY_START_MIN = 20          # avoid the first few minutes of chaotic price discovery
ENTRY_END_HOUR = 15
ENTRY_END_MIN = 0
SQUARE_OFF_HOUR = 15
SQUARE_OFF_MIN = 15

# Polling interval for the live loop, in seconds.
# 5-min candles -> poll every 15-20s is plenty; no need to hammer the API.
POLL_INTERVAL_SECONDS = 15

# ============================================================
#  STRIKE SELECTION
# ============================================================
STRIKE_STEP = 50    # NIFTY strikes are in steps of 50

# ============================================================
#  ORDER PLACEMENT
# ============================================================
ORDER_PRODUCT_TYPE = "INTRADAY"
ORDER_TYPE = "MARKET"
ORDER_VALIDITY = "DAY"

# ============================================================
#  PATHS
# ============================================================
LOG_DIR = os.path.join(BASE_DIR, "logs")
STATE_FILE = os.path.join(BASE_DIR, "bot_state.json")
KILL_FILE = os.path.join(BASE_DIR, "KILL_SWITCH")   # create this file to force-stop the bot

os.makedirs(LOG_DIR, exist_ok=True)
