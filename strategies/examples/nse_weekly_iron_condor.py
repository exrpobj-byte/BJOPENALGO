#!/usr/bin/env python
"""
NSE Weekly Options - Iron Condor Strategy
==========================================
Underlying  : NIFTY (Tuesday weekly expiry)
Setup       : Short Iron Condor
              SELL ATM+100 CE (OTM2), SELL ATM-100 PE (OTM2)
              BUY  ATM+200 CE (OTM5), BUY  ATM-200 PE (OTM5)
Entry Day   : Tuesday (fresh weekly expiry) 9:35 – 10:00 AM IST
Profit Exit : 40% of max premium collected
Loss Exit   : 100% of max premium collected (2x the credit)
Time Exit   : Tuesday 3:20 PM IST
Mode        : Analyzer (paper trade) via OpenAlgo
Exchange    : NFO  |  Product : NRML  |  Lot Size : NIFTY = 75

Upload to   : http://127.0.0.1:5000/python  →  Add Strategy
Schedule    : 09:15–15:25, Monday–Friday, Exchange = NFO
"""

import os
import time
from datetime import datetime, timedelta

import pytz
from openalgo import api

# ─── Credentials (auto-injected by OpenAlgo's /python runner) ────────────────
api_key = os.getenv("OPENALGO_API_KEY")
host    = os.getenv("HOST_SERVER",   "http://127.0.0.1:5000")
ws_url  = os.getenv("WEBSOCKET_URL", "ws://127.0.0.1:8765")

if not api_key:
    print("[ERROR] OPENALGO_API_KEY not set. Exiting.")
    raise SystemExit(1)

client = api(api_key=api_key, host=host, ws_url=ws_url)

# ─── Strategy Configuration ───────────────────────────────────────────────────
STRATEGY_NAME  = "NIFTY Weekly Iron Condor"
UNDERLYING     = "NIFTY"
EXCH_UNDERLYING = "NSE_INDEX"   # for fetching LTP / expiry
EXCH_FNO       = "NFO"          # for placing orders
PRODUCT        = "NRML"
LOT_SIZE       = 75             # NIFTY lot size (verify on master contract)
NUM_LOTS       = 1              # number of lots per leg

# Iron Condor legs (offset from ATM)
SHORT_STRIKE_OFFSET = "OTM2"   # sell strikes closer to money
LONG_STRIKE_OFFSET  = "OTM5"   # buy wings for protection

# Risk parameters
PROFIT_TARGET_PCT  = 40         # exit when 40% of premium is captured
MAX_LOSS_PCT       = 100        # exit when loss = 100% of premium collected
CHECK_INTERVAL_SEC = 60         # poll P&L every 60 seconds

# IST timezone
IST = pytz.timezone("Asia/Kolkata")

# ─── State ─────────────────────────────────────────────────────────────────────
position_entered   = False
entry_credit       = 0.0        # total premium collected at entry
entry_timestamp    = None
expiry_date_str    = ""         # e.g. "24APR25"
ce_short_symbol    = ""
pe_short_symbol    = ""


def ist_now() -> datetime:
    return datetime.now(IST)


def is_entry_window() -> bool:
    """Allow entry only 9:35-10:00 AM IST on Tuesday (expiry day)."""
    now = ist_now()
    if now.weekday() != 1:          # 1 = Tuesday only
        return False
    entry_open  = now.replace(hour=9,  minute=35, second=0, microsecond=0)
    entry_close = now.replace(hour=10, minute=0,  second=0, microsecond=0)
    return entry_open <= now <= entry_close


def is_exit_time() -> bool:
    """Force-exit at 3:20 PM IST."""
    now = ist_now()
    return now >= now.replace(hour=15, minute=20, second=0, microsecond=0)


def is_market_hours() -> bool:
    """Return True between 9:15 AM and 3:30 PM IST on weekdays."""
    now = ist_now()
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_ = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_ <= now <= close_


def get_weekly_expiry() -> str:
    """
    Return the nearest Tuesday weekly expiry as 'DDMMMYY'.
    NIFTY50 weekly options expire every Tuesday.
    If today is Tuesday after 3:30 PM, return NEXT Tuesday.
    """
    now = ist_now().date()
    days_ahead = 1 - now.weekday()          # Tuesday = weekday 1
    if days_ahead < 0:
        days_ahead += 7
    # If it's Tuesday afternoon (market closed) roll to next week
    if days_ahead == 0 and ist_now().hour >= 15 and ist_now().minute >= 30:
        days_ahead = 7
    expiry_dt = now + timedelta(days=days_ahead)
    return expiry_dt.strftime("%d%b%y").upper()   # e.g. "28APR26"


def get_premium_for_leg(symbol: str) -> float:
    """Fetch LTP for an option symbol from NFO."""
    try:
        q = client.quotes(symbol=symbol, exchange=EXCH_FNO)
        if q.get("status") == "success":
            return float(q.get("ltp", 0) or 0)
    except Exception as exc:
        print(f"[WARN] Could not fetch quote for {symbol}: {exc}")
    return 0.0


def calculate_current_pnl() -> float:
    """
    Estimated P&L = (entry_credit - current_cost) * LOT_SIZE * NUM_LOTS * 2 legs
    Positive = profit, Negative = loss.
    """
    if not ce_short_symbol or not pe_short_symbol:
        return 0.0

    ce_ltp = get_premium_for_leg(ce_short_symbol)
    pe_ltp = get_premium_for_leg(pe_short_symbol)
    current_debit = (ce_ltp + pe_ltp) * LOT_SIZE * NUM_LOTS
    return entry_credit - current_debit


def enter_iron_condor(expiry: str) -> bool:
    """
    Place all 4 legs of the iron condor as a multi-leg order.
    Returns True on success.
    """
    global entry_credit, ce_short_symbol, pe_short_symbol

    print(f"\n[{ist_now():%H:%M:%S}] Entering Iron Condor | Expiry: {expiry}")

    legs = [
        # ── Protective BUY wings first (debit legs, placed first for margin) ──
        {"offset": LONG_STRIKE_OFFSET,  "option_type": "CE",
         "action": "BUY",  "quantity": LOT_SIZE * NUM_LOTS, "product": PRODUCT},
        {"offset": LONG_STRIKE_OFFSET,  "option_type": "PE",
         "action": "BUY",  "quantity": LOT_SIZE * NUM_LOTS, "product": PRODUCT},
        # ── Short strikes (credit legs) ──
        {"offset": SHORT_STRIKE_OFFSET, "option_type": "CE",
         "action": "SELL", "quantity": LOT_SIZE * NUM_LOTS, "product": PRODUCT},
        {"offset": SHORT_STRIKE_OFFSET, "option_type": "PE",
         "action": "SELL", "quantity": LOT_SIZE * NUM_LOTS, "product": PRODUCT},
    ]

    try:
        resp = client.optionsmultiorder(
            strategy=STRATEGY_NAME,
            underlying=UNDERLYING,
            exchange=EXCH_UNDERLYING,
            expiry_date=expiry,
            legs=legs,
        )
        print(f"[ENTRY RESPONSE] {resp}")

        if resp.get("status") != "success":
            print(f"[ERROR] Entry failed: {resp.get('message')}")
            return False

        # ── Record entry premium for P&L tracking ──
        total_credit = 0.0
        for result in resp.get("results", []):
            if result.get("action") == "SELL":
                sym = result.get("symbol", "")
                if "CE" in sym:
                    ce_short_symbol = sym
                elif "PE" in sym:
                    pe_short_symbol = sym
                ltp = get_premium_for_leg(sym)
                total_credit += ltp * LOT_SIZE * NUM_LOTS

        entry_credit = total_credit
        print(f"[ENTRY] Total credit collected: ₹{entry_credit:.2f}")
        print(f"[ENTRY] CE short: {ce_short_symbol} | PE short: {pe_short_symbol}")
        return True

    except Exception as exc:
        print(f"[ERROR] Iron condor entry exception: {exc}")
        return False


def exit_iron_condor(reason: str) -> None:
    """Close all 4 legs by closing all positions for this strategy."""
    print(f"\n[{ist_now():%H:%M:%S}] EXITING Iron Condor | Reason: {reason}")
    try:
        resp = client.closeposition(strategy=STRATEGY_NAME)
        print(f"[EXIT RESPONSE] {resp}")
    except Exception as exc:
        print(f"[ERROR] Exit exception: {exc}")


# ─── Main Loop ────────────────────────────────────────────────────────────────

def run_strategy():
    global position_entered, entry_credit, entry_timestamp, expiry_date_str

    print("=" * 60)
    print(f"  {STRATEGY_NAME}  (NIFTY Tuesday weekly expiry)")
    print(f"  Underlying : {UNDERLYING}  |  Offset : {SHORT_STRIKE_OFFSET}/{LONG_STRIKE_OFFSET}")
    print(f"  Profit Target : {PROFIT_TARGET_PCT}%  |  Max Loss : {MAX_LOSS_PCT}%")
    print(f"  Mode : ANALYZER (paper trade)")
    print("=" * 60)

    while True:
        try:
            now = ist_now()

            if not is_market_hours():
                print(f"[{now:%H:%M:%S}] Market closed. Sleeping 60 s …")
                time.sleep(60)
                continue

            # ── Force exit at 3:20 PM ─────────────────────────────────────────
            if position_entered and is_exit_time():
                exit_iron_condor("Time exit 3:20 PM")
                position_entered = False
                print("[INFO] Strategy done for today. Sleeping till next session.")
                time.sleep(3600)
                continue

            # ── Entry logic ───────────────────────────────────────────────────
            if not position_entered and is_entry_window():
                expiry_date_str = get_weekly_expiry()
                print(f"[{now:%H:%M:%S}] Entry window open. Expiry = {expiry_date_str}")
                success = enter_iron_condor(expiry_date_str)
                if success:
                    position_entered = True
                    entry_timestamp  = now
                else:
                    print("[WARN] Entry failed. Will retry in next interval.")

            # ── P&L monitoring ────────────────────────────────────────────────
            elif position_entered:
                pnl    = calculate_current_pnl()
                pnl_pct = (pnl / entry_credit * 100) if entry_credit > 0 else 0
                print(
                    f"[{now:%H:%M:%S}] P&L = ₹{pnl:.2f}  ({pnl_pct:+.1f}%)  "
                    f"| Credit = ₹{entry_credit:.2f}"
                )

                if pnl_pct >= PROFIT_TARGET_PCT:
                    exit_iron_condor(f"Profit target {PROFIT_TARGET_PCT}% hit")
                    position_entered = False

                elif pnl_pct <= -MAX_LOSS_PCT:
                    exit_iron_condor(f"Max loss {MAX_LOSS_PCT}% hit")
                    position_entered = False

            time.sleep(CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            print("\n[INFO] Strategy interrupted by user.")
            if position_entered:
                exit_iron_condor("Manual interrupt")
            break
        except Exception as exc:
            print(f"[ERROR] Unexpected exception: {exc}")
            time.sleep(30)


if __name__ == "__main__":
    run_strategy()
