"""
config.py
All strategy constants live here. Change values here, not inside the logic files.
"""

# ============================================================
# SAFETY SWITCH - leave True until you have verified everything
# in DRY_RUN mode for several days. Only flip to False when you
# are ready to risk real capital.
# ============================================================
DRY_RUN = False

# ============================================================
# Dhan API
# ============================================================
CREDENTIALS_FILE = "credentials.txt"
DHAN_BASE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
SCRIP_MASTER_LOCAL_CACHE = "scrip_master_cache.csv"   # refreshed once per day

EXCHANGE_SEGMENT_FUTURES = "BSE_FNO"   # see note below - this is NOT a typo

# IMPORTANT - CONFIRMED BY EXHAUSTIVE DIRECT TESTING on this specific Dhan
# account, across daily candles, intraday candles, AND live OHLC quotes:
#   - exchange_segment="NSE_FNO" returns EMPTY data for FUTSTK security IDs
#     on every endpoint tested (status:success with zero rows, or DH-905),
#     for multiple different contracts (RELIANCE June/July, TATASTEEL June).
#   - exchange_segment="BSE_FNO" returns real, internally consistent OHLC
#     data for the SAME security_id on every endpoint tested.
# This is the account's actual current data access, not a parameter mistake -
# every reasonable combination of instrument_type/expiry_code/date-range/
# security_id was tried and ruled out before reaching this conclusion.
#
# Practical meaning: this account's market-data FEED for single-stock futures
# is only populated under the BSE_FNO tag, even though the underlying contract
# genuinely trades on NSE. Why Dhan's feed is structured this way (data plan
# limitation, account configuration, etc.) is a question for Dhan support, not
# something fixable from this code. If your Dhan plan/data access changes in
# the future, re-run the diagnostic scripts from this project's debugging
# history to re-verify before assuming this constant should change back.
#
# ORDER PLACEMENT CAVEAT: this constant is also used for order routing in
# main.py. Whether BSE_FNO is correct for ACTUALLY PLACING a real order (as
# opposed to just reading data) has NOT been independently confirmed - only
# DRY_RUN has been tested. Before disabling DRY_RUN, place one tiny manual
# test order through Dhan's own web/app interface on this exact contract to
# confirm BSE_FNO routes correctly, since a wrong segment on a REAL order
# could fail outright or - worse - execute on an unintended venue.
INSTRUMENT_TYPE_FUTSTK = "FUTSTK"

# ============================================================
# Strategy parameters (from your spec)
# ============================================================
CPR_TIMEFRAME_MINUTES = 15          # CPR is read off the 15-min chart
CPR_NARROW_THRESHOLD_PCT = 0.5      # CPR width < 0.5% of Close => "narrow"

STOP_LOSS_RUPEES = 1.0              # initial SL distance in Rs
TARGET_RUPEES = 5.0                 # target distance in Rs

BREAKEVEN_TRIGGER_RUPEES = 2.5      # move SL to entry once price is +2.5 from entry
                                     # (midpoint of your stated 2-3 Rs range)
TRAIL_STEP_RUPEES = 1.0             # after breakeven, trail SL by Rs1 for every Rs1 gained

# Quantity per trade - set this based on your capital/risk per trade.
# This is a placeholder; do not leave as 1 lot blindly, size it deliberately.
DEFAULT_QUANTITY_LOTS = 1

# ============================================================
# Chartink
# ============================================================
# Paste the exact "scan_clause" string from your Chartink screener's URL/network
# request here once you build it on chartink.com - see chartink_scanner.py for
# instructions on how to obtain this from your browser.
CHARTINK_SCAN_CLAUSE = ""   # leave empty and chartink_scanner.py will warn you

CHARTINK_SCAN_URL = "https://chartink.com/screener/process"

# ============================================================
# Backtesting (Yahoo Finance)
# ============================================================
YF_INTRADAY_INTERVAL = "15m"
YF_MAX_INTRADAY_DAYS = 59   # Yahoo only allows ~60 days of 15m history per request

# ============================================================
# Logging
# ============================================================
LOG_DIR = "logs"
TRADE_LOG_FILE = "logs/trades.csv"
ERROR_LOG_FILE = "logs/errors.log"
