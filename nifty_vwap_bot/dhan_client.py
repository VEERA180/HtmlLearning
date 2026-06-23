"""
Thin wrapper around the Dhan v2 REST API.

Deliberately uses raw `requests` calls (rather than the dhanhq SDK) for
option-chain / instrument lookups, because the option chain response
shape is simple JSON and this avoids any SDK-version drift. Order
placement also goes through raw REST here so the DRY_RUN gate is
unmissable and sits in exactly one place.

Every method that would place/modify/cancel a REAL order checks
config.DRY_RUN first and logs-only if True.

RATE LIMIT HANDLING (added after a real 429/805 "Too many requests" event):
Dhan's rate limit is enforced PER ACCOUNT, not per-process. If you run
more than one bot/script against the same client_id/token (e.g. this bot
on one machine and another bot on another machine, both polling
/marketfeed/ltp), their request rates stack and can trip the limit even
if neither one alone would. This module cannot prevent that collision by
itself, but it DOES now retry 429s with exponential backoff + jitter
instead of retrying at a fixed interval - a fixed-interval retry across
two independently-running bots can stay synchronized and keep colliding;
backoff with jitter breaks that synchronization over a few attempts.
"""

import time
import random
import logging
import requests

import config

logger = logging.getLogger("dhan_client")

BASE_URL = "https://api.dhan.co/v2"

HEADERS = {
    "Content-Type": "application/json",
    "access-token": config.DHAN_ACCESS_TOKEN,
    "client-id": config.DHAN_CLIENT_ID,
}

# ----------------------------------------------------------------------
# 429 retry tuning - applies ONLY to HTTP 429 (rate limit) responses.
# Any other error (4xx/5xx/network) still raises immediately, unchanged -
# we don't want to silently retry into a real auth/data error.
# ----------------------------------------------------------------------
RATE_LIMIT_MAX_RETRIES = 4
RATE_LIMIT_BASE_DELAY_SECONDS = 2.0   # first retry waits ~2s, then ~4s, ~8s, ~16s
RATE_LIMIT_JITTER_SECONDS = 1.0       # +/- random jitter so two bots don't stay in lockstep


class DhanClientError(Exception):
    pass


def _request_with_rate_limit_retry(method, url, **kwargs):
    """
    Wraps requests.<method>(url, **kwargs) with retry-on-429 behavior.
    Every other status code / exception is returned/raised to the caller
    exactly as before - this function only changes what happens on 429.
    """
    attempt = 0
    while True:
        try:
            resp = method(url, **kwargs)
        except requests.RequestException as e:
            raise DhanClientError(f"Network error calling {url}: {e}") from e

        if resp.status_code != 429:
            return resp

        attempt += 1
        if attempt > RATE_LIMIT_MAX_RETRIES:
            raise DhanClientError(
                f"Dhan API rate limit (429) on {url} - gave up after "
                f"{RATE_LIMIT_MAX_RETRIES} retries. Last response: {resp.text}"
            )

        delay = RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        delay += random.uniform(-RATE_LIMIT_JITTER_SECONDS, RATE_LIMIT_JITTER_SECONDS)
        delay = max(delay, 0.5)

        logger.warning(
            "Rate limited (429) on %s - retry %d/%d in %.1fs. Response: %s",
            url, attempt, RATE_LIMIT_MAX_RETRIES, delay, resp.text,
        )
        time.sleep(delay)


def _post(path, payload, timeout=10):
    url = f"{BASE_URL}{path}"
    resp = _request_with_rate_limit_retry(
        requests.post, url, json=payload, headers=HEADERS, timeout=timeout
    )

    if resp.status_code not in (200, 202):
        raise DhanClientError(
            f"Dhan API error on {path}: HTTP {resp.status_code} - {resp.text}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise DhanClientError(f"Non-JSON response from {path}: {resp.text}") from e


def _get(path, timeout=10):
    url = f"{BASE_URL}{path}"
    resp = _request_with_rate_limit_retry(
        requests.get, url, headers=HEADERS, timeout=timeout
    )

    if resp.status_code != 200:
        raise DhanClientError(
            f"Dhan API error on {path}: HTTP {resp.status_code} - {resp.text}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise DhanClientError(f"Non-JSON response from {path}: {resp.text}") from e


def _delete(path, timeout=10):
    url = f"{BASE_URL}{path}"
    resp = _request_with_rate_limit_retry(
        requests.delete, url, headers=HEADERS, timeout=timeout
    )
    if resp.status_code not in (200, 202):
        raise DhanClientError(
            f"Dhan API error on {path}: HTTP {resp.status_code} - {resp.text}"
        )
    return resp.text


# ----------------------------------------------------------------------
# Market data
# ----------------------------------------------------------------------

def get_nifty_ltp():
    """Returns the current NIFTY 50 index LTP (float)."""
    payload = {"IDX_I": [int(config.NIFTY_INDEX_SECURITY_ID)]}
    data = _post("/marketfeed/ltp", payload)
    try:
        idx_block = data["data"]["IDX_I"][config.NIFTY_INDEX_SECURITY_ID]
        return float(idx_block["last_price"])
    except (KeyError, TypeError) as e:
        raise DhanClientError(f"Unexpected LTP response shape: {data}") from e


def get_option_expiry_list():
    """Returns list of available expiry date strings (YYYY-MM-DD) for NIFTY."""
    payload = {
        "UnderlyingScrip": config.NIFTY_UNDERLYING_SCRIP,
        "UnderlyingSeg": config.NIFTY_UNDERLYING_SEGMENT,
    }
    data = _post("/optionchain/expirylist", payload)
    return data.get("data", [])


def get_option_chain(expiry_date):
    """
    Returns the full option chain dict for the given expiry (YYYY-MM-DD).
    Rate-limited to 1 unique request per 3 seconds by Dhan - caller should
    not poll this in the main tight loop.
    """
    payload = {
        "UnderlyingScrip": config.NIFTY_UNDERLYING_SCRIP,
        "UnderlyingSeg": config.NIFTY_UNDERLYING_SEGMENT,
        "Expiry": expiry_date,
    }
    return _post("/optionchain", payload)


def resolve_atm_option(nifty_spot, option_type, expiry_date):
    """
    Given current NIFTY spot, find the ATM strike (rounded to nearest 50)
    and return (security_id, trading_symbol, strike, ltp) for that
    option's CE or PE leg from the live option chain.

    option_type: "CE" or "PE"
    """
    atm_strike = round(nifty_spot / config.STRIKE_STEP) * config.STRIKE_STEP

    chain = get_option_chain(expiry_date)
    try:
        strikes_data = chain["data"]["oc"]
    except (KeyError, TypeError) as e:
        raise DhanClientError(f"Unexpected option chain response shape: {chain}") from e

    # Dhan keys strike entries as string price, e.g. "24500.000000"
    matched_key = None
    for key in strikes_data.keys():
        try:
            if abs(float(key) - atm_strike) < 0.01:
                matched_key = key
                break
        except ValueError:
            continue

    if matched_key is None:
        raise DhanClientError(f"ATM strike {atm_strike} not found in option chain for {expiry_date}")

    leg_key = "ce" if option_type == "CE" else "pe"
    leg = strikes_data[matched_key].get(leg_key)
    if leg is None:
        raise DhanClientError(f"{option_type} leg missing for strike {atm_strike}")

    security_id = leg.get("security_id") or leg.get("securityId")
    ltp = leg.get("last_price", leg.get("ltp"))

    if security_id is None:
        raise DhanClientError(
            f"No security_id in option chain leg for {option_type} {atm_strike} "
            f"- raw leg: {leg}"
        )

    trading_symbol = f"NIFTY {expiry_date} {int(atm_strike)} {option_type}"
    return {
        "security_id": str(security_id),
        "trading_symbol": trading_symbol,
        "strike": atm_strike,
        "ltp": float(ltp) if ltp is not None else None,
    }


def get_option_ltp(security_id):
    """Returns current LTP for a single option contract by security_id."""
    payload = {"NSE_FNO": [int(security_id)]}
    data = _post("/marketfeed/ltp", payload)
    try:
        block = data["data"]["NSE_FNO"][str(security_id)]
        return float(block["last_price"])
    except (KeyError, TypeError) as e:
        raise DhanClientError(f"Unexpected option LTP response shape: {data}") from e


# ----------------------------------------------------------------------
# Positions / account state
# ----------------------------------------------------------------------

def get_open_positions():
    """Returns the raw list of open positions for today from Dhan."""
    data = _get("/positions")
    if isinstance(data, list):
        return [p for p in data if p.get("netQty", 0) != 0]
    return []


def get_fund_limits():
    return _get("/fundlimit")


def get_kill_switch_status():
    data = _get("/killswitch")
    return data.get("killSwitchStatus")


# ----------------------------------------------------------------------
# Order placement - DRY_RUN gate lives here, and only here
# ----------------------------------------------------------------------

def place_order(security_id, transaction_type, quantity, trading_symbol="UNKNOWN"):
    """
    transaction_type: "BUY" or "SELL"
    quantity: number of units (lots * lot_size already multiplied by caller)

    Returns a dict: {"order_id": ..., "status": ..., "dry_run": bool}
    """
    payload = {
        "dhanClientId": config.DHAN_CLIENT_ID,
        "transactionType": transaction_type,
        "exchangeSegment": config.OPTION_EXCHANGE_SEGMENT,
        "productType": config.ORDER_PRODUCT_TYPE,
        "orderType": config.ORDER_TYPE,
        "validity": config.ORDER_VALIDITY,
        "securityId": str(security_id),
        "quantity": int(quantity),
        "price": "0",
    }

    if config.DRY_RUN:
        logger.info(
            "[DRY_RUN] Would place order: %s %s qty=%s symbol=%s payload=%s",
            transaction_type, security_id, quantity, trading_symbol, payload,
        )
        return {"order_id": "DRY_RUN", "status": "DRY_RUN", "dry_run": True, "payload": payload}

    logger.warning(
        "[LIVE] Placing REAL order: %s %s qty=%s symbol=%s",
        transaction_type, security_id, quantity, trading_symbol,
    )
    result = _post("/orders", payload)
    logger.warning("[LIVE] Order response: %s", result)
    result["dry_run"] = False
    return result


def exit_all_positions():
    """Square off everything immediately. Used by the kill-switch path."""
    if config.DRY_RUN:
        logger.info("[DRY_RUN] Would call exit_all_positions()")
        return {"dry_run": True}
    logger.warning("[LIVE] Calling exit_all_positions()")
    return _delete("/positions")


def activate_kill_switch():
    """Activates Dhan's account-level kill switch - disables trading for the day."""
    if config.DRY_RUN:
        logger.info("[DRY_RUN] Would activate Dhan kill switch")
        return {"dry_run": True}
    url = f"{BASE_URL}/killswitch?killSwitchStatus=ACTIVATE"
    resp = _request_with_rate_limit_retry(
        requests.post, url, headers=HEADERS,
        json={"dhanClientId": config.DHAN_CLIENT_ID}, timeout=10,
    )
    return resp.json()