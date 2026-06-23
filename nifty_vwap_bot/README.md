# NIFTY VWAP Touch-and-Reverse — Options Bot

Converts the index-points backtest into a live bot that buys ATM NIFTY
CE/PE based on a VWAP touch-and-reverse signal computed on the NIFTY
index itself.

## IMPORTANT — read before running

This code was written against Dhan's documented v2 REST API as of
June 2026. **It has not been run or tested against the live API** (no
network access in the environment that wrote it) — you need to validate
every piece below before trusting it with money.

### Known gaps / things YOU must verify or fix

1. **`NIFTY_INDEX_SECURITY_ID = "13"` in `config.py` is unverified.**
   Confirm it against Dhan's instrument master CSV
   (https://images.dhan.co/api-data/api-scrip-master.csv) before running.
   If wrong, `get_nifty_ltp()` will fail loudly (good) — but check anyway.

2. **`resolve_atm_option()` parses the option chain response assuming
   keys `oc`, `ce`/`pe`, `security_id`, `last_price`.** I built this from
   Dhan's documented schema, but field names can drift. **Print and
   inspect one raw `get_option_chain()` response before your first dry
   run** — add a quick `print(dhan_client.get_option_chain(expiry))` in
   a scratch script and check the actual keys match.

3. **Static IP whitelisting is required for order placement** (place,
   modify, cancel) per Dhan's docs. If you haven't set this up in your
   Dhan API settings, every real order call will fail — `DRY_RUN=True`
   mode is unaffected since it never calls the order endpoint.

4. **The VWAP-on-NIFTY-index live calculation is an approximation of the
   backtest, not an exact match.** The backtest compares each candle's
   close to "the VWAP value at that point in time" using vectorized pandas
   ops. The live version (`strategy.py`) approximates "VWAP two candles
   ago" using the *current* rolling VWAP, since this proxy VWAP moves
   slowly once enough candles have accumulated in the session — but it is
   not identical, especially in the first 10-15 minutes after market open
   when the cumulative average is still swinging a lot. This is flagged
   in `strategy.py`'s `check_signal()` docstring. If you want an exact
   match, store the VWAP value alongside each historical candle instead
   of only the latest rolling value.

5. **LTP-based execution, not tick-by-tick.** This polls LTP every
   `POLL_INTERVAL_SECONDS` (default 15s) rather than subscribing to
   Dhan's WebSocket live feed. This is simpler and adequate for a
   ~0.26-trades/day strategy, but means SL/trailing checks have up to
   ~15s of lag versus true tick data. For higher-frequency strategies
   you'd want the WebSocket feed instead.

6. **No slippage/brokerage modeling.** Real fills on MARKET orders for
   options, especially less-liquid OTM/ATM strikes, can differ from the
   LTP you last polled. The backtest's "82.6% win rate, 6.39 profit
   factor" numbers do not account for this at all, and it matters more
   for options than it did for the raw index-points backtest.

7. **Sample size problem, still unresolved.** 23 trades over 60 days
   was discussed and acknowledged before this code was written — going
   live doesn't fix the small-sample-size concern, it just means you're
   now finding out with real money instead of more backtest data.

## Setup

```bash
pip install -r requirements.txt
```

### Credentials (and your daily token refresh)

Dhan access tokens generated manually from the developer portal expire
(commonly within ~24 hours). Rather than re-running an export command
in your terminal every morning, this bot reads credentials from a
plain text file:

1. Copy `credentials.txt.example` to `credentials.txt` (same folder).
2. Fill in your real `CLIENT_ID` and `ACCESS_TOKEN`.
3. **Each morning**, when Dhan gives you a new access token: open
   `credentials.txt`, replace the `ACCESS_TOKEN=` line, save. Nothing
   else needs to change — just restart the bot (`python main.py`)
   after updating it.

`credentials.txt` is listed in `.gitignore` so it's never accidentally
committed or shared if you put this folder under version control.

Environment variables (`DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN`) still
work too and take priority over the file, if you ever want to script
something fancier later — but for now, the file is the simplest daily
routine.

## Recommended validation sequence

1. **Inspect raw API responses first.** Before running `main.py` at all,
   write a 5-line scratch script that calls `dhan_client.get_nifty_ltp()`,
   `dhan_client.get_option_expiry_list()`, and
   `dhan_client.get_option_chain(expiry)` and prints the raw output.
   Confirm the shapes match what `resolve_atm_option()` expects.

2. **Run with `DRY_RUN = True` (the default) for at least 3-5 full
   trading days.** Watch `logs/bot_YYYYMMDD.log`. Confirm:
   - Candles are closing every 5 minutes as expected
   - VWAP values look sane (close to NIFTY spot, not wildly off)
   - Signals fire at moments that make sense when you cross-check the
     NIFTY chart yourself
   - "Would place order" log lines show the strike/option type you'd
     expect given the signal direction

3. **Only after that, set `DRY_RUN = False`** — and consider starting
   with the bot running during market hours while you watch it live for
   the first day or two, not unattended overnight-to-open.

## Auto-starting at 9:15 AM with Windows Task Scheduler

The bot has no built-in scheduler — `python main.py` only runs when
something launches it. To have Windows launch it automatically at
9:15 AM on weekdays:

### 1. Find your Python path

In PowerShell:
```powershell
(Get-Command python).Source
```
Copy that path — you'll need it in step 3.

### 2. Edit `run_bot.bat`

Open `run_bot.bat` in this folder and, if `python` isn't reliably on
PATH for scheduled tasks (it often isn't), replace this line:
```bat
set PYTHON_EXE=python
```
with the full path from step 1, e.g.:
```bat
set PYTHON_EXE="C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"
```

### 3. Create the scheduled task

Open **Task Scheduler** (search for it in the Start menu) →
**Create Task** (not "Basic Task" — the full dialog gives more control):

- **General tab**: Name it `NIFTY VWAP Bot`. Select "Run whether user
  is logged on or not" if you want it to fire even if you're not
  logged in (requires saving your Windows password when prompted).
- **Triggers tab** → New: Daily, start time `9:15:00 AM`, recur every
  1 day. Click **OK**, then edit the trigger again and check
  "Weekdays" instead of every day, if that option appears — or under
  Advanced Settings, set it to repeat only Mon–Fri.
- **Actions tab** → New → Action: "Start a program" → Browse to
  `run_bot.bat` in this folder.
- **Conditions tab**: uncheck "Start the task only if the computer is
  on AC power" if you're on a laptop, so a battery doesn't block it.

### 4. CRITICAL — the daily credential routine doesn't disappear

Task Scheduler does not solve the expiring-token problem. **You still
need to open `credentials.txt` and paste in today's fresh
`ACCESS_TOKEN` before 9:15 AM every trading day.** If you forget,
`config.py` will now refuse to start at all — it checks whether
`credentials.txt` was modified today, and raises a clear error if not,
specifically so a stale-token run doesn't fail silently and
unattended partway through the day. You'll see this in
`logs\scheduler_runs.log`.

### 5. Verify it actually fires

The easiest way to test without waiting for 9:15 AM tomorrow: in Task
Scheduler, right-click the task → **Run**. Check
`logs\scheduler_runs.log` for the timestamp marker, and
`logs\bot_YYYYMMDD.log` for the bot's own output.

## Emergency stop

- Create a file named `KILL_SWITCH` in this folder (no extension, any
  content) — the bot checks for this every loop iteration and will stop
  cleanly. **It does not auto-close open positions** — you'd need to
  square off manually via the Dhan app, or call
  `dhan_client.exit_all_positions()` / `dhan_client.activate_kill_switch()`
  yourself.
- Ctrl+C also stops the bot cleanly without touching open positions.

## Files

- `config.py` — all parameters, including the `DRY_RUN` safety switch
- `dhan_client.py` — only file that talks to Dhan's API
- `strategy.py` — VWAP touch-and-reverse signal detection on NIFTY index
- `risk_manager.py` — per-trade SL, trailing stop, daily loss cap
- `state.py` — JSON persistence so a restart doesn't lose track of positions
- `main.py` — the live loop
- `run_bot.bat` — Windows Task Scheduler entry point (see above)
- `credentials.txt.example` — template; copy to `credentials.txt` and fill in
