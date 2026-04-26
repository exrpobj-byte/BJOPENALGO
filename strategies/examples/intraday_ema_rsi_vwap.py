#!/usr/bin/env python
"""
Intraday Strategy – EMA + RSI + VWAP on NIFTY
==============================================
Timeframe   : 5-minute candles
Underlying  : NIFTY index (NSE_INDEX) → trades via ATM options on NFO
Signal      : EMA(9) cross EMA(21) + RSI(14) filter + Price vs VWAP
Entry Window: 9:35 AM – 2:30 PM IST
Exit        : 3:15 PM IST (time-based) OR target/stop hit
Target      : 1.5% on premium  |  Stop Loss : 0.8% on premium
Max Trades  : 2 per day (1 long, 1 short)
Product     : MIS (auto-square-off)
Mode        : Analyzer (paper trade) via OpenAlgo

Upload to   : http://127.0.0.1:5000/python  →  Add Strategy
Schedule    : 09:15–15:20, Monday–Friday, Exchange = NFO
"""

import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytz
from openalgo import api

# ─── Credentials ─────────────────────────────────────────────────────────────
api_key = os.getenv("OPENALGO_API_KEY")
host    = os.getenv("HOST_SERVER",   "http://127.0.0.1:5000")
ws_url  = os.getenv("WEBSOCKET_URL", "ws://127.0.0.1:8765")

if not api_key:
    print("[ERROR] OPENALGO_API_KEY not set. Exiting.")
    raise SystemExit(1)

client = api(api_key=api_key, host=host, ws_url=ws_url)

# ─── Strategy Configuration ───────────────────────────────────────────────────
STRATEGY_NAME  = "NIFTY Intraday EMA RSI"
UNDERLYING     = "NIFTY"
EXCH_INDEX     = "NSE_INDEX"    # for historical data + underlying LTP
EXCH_FNO       = "NFO"          # for option orders
PRODUCT        = "MIS"
LOT_SIZE       = 75             # NIFTY lot size (verify on master contract)
NUM_LOTS       = 1

# EMA periods
EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14

# Risk params
TARGET_PCT     = 1.5            # 1.5% profit on premium
STOP_PCT       = 0.8            # 0.8% stop-loss on premium
MAX_TRADES_DAY = 2              # max directional trades per calendar day
CHECK_INTERVAL = 60             # seconds between signal checks

IST = pytz.timezone("Asia/Kolkata")

# ─── State ─────────────────────────────────────────────────────────────────────
trades_today   = 0
trade_date     = None
position_side  = None           # "CE" | "PE" | None
option_symbol  = ""
entry_premium  = 0.0


def ist_now() -> datetime:
    return datetime.now(IST)


def is_market_open() -> bool:
    now = ist_now()
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_ = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_ <= now <= close_


def is_entry_window() -> bool:
    now = ist_now()
    start_ = now.replace(hour=9,  minute=35, second=0, microsecond=0)
    end_   = now.replace(hour=14, minute=30, second=0, microsecond=0)
    return start_ <= now <= end_


def is_force_exit_time() -> bool:
    now = ist_now()
    return now >= now.replace(hour=15, minute=15, second=0, microsecond=0)


def get_nearest_expiry() -> str:
    """Return nearest NFO weekly expiry for NIFTY as DDMMMYY string.
    NIFTY options expire every Thursday (weekly). Monthly on last Thursday.
    """
    try:
        result = client.expiry(
            symbol=UNDERLYING,
            exchange=EXCH_FNO,
            instrumenttype="options",
        )
        if result.get("status") == "success":
            expiries = result.get("data", [])
            if expiries:
                # expiries is a list of strings like ["24APR25", "01MAY25", ...]
                return expiries[0]
    except Exception as exc:
        print(f"[WARN] Could not fetch expiry dates: {exc}")

    # Fallback: compute nearest Thursday
    today = ist_now().date()
    days_to_thu = (3 - today.weekday()) % 7
    if days_to_thu == 0 and ist_now().hour >= 15:
        days_to_thu = 7
    expiry_dt = today + timedelta(days=days_to_thu)
    return expiry_dt.strftime("%d%b%y").upper()


# ─── Technical Indicators ─────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP calculated from first available candle each day."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).cumsum()
    cum_vol    = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def fetch_signals() -> dict:
    """
    Fetch 5-minute NIFTY data (last 5 days) and compute indicators.
    Returns dict with current signal, ema_fast, ema_slow, rsi, vwap, ltp.
    """
    end_dt   = ist_now().strftime("%Y-%m-%d")
    start_dt = (ist_now() - timedelta(days=5)).strftime("%Y-%m-%d")

    df = client.history(
        symbol=UNDERLYING,
        exchange=EXCH_INDEX,
        interval="5m",
        start_date=start_dt,
        end_date=end_dt,
    )

    if isinstance(df, dict) or df.empty or len(df) < EMA_SLOW + 2:
        return {}

    df["ema_fast"] = ema(df["close"], EMA_FAST)
    df["ema_slow"] = ema(df["close"], EMA_SLOW)
    df["rsi"]      = rsi(df["close"], RSI_PERIOD)
    df["vwap"]     = vwap(df)

    # Use the closed candle (index -2) to avoid partial signals
    row_prev = df.iloc[-2]
    row_curr = df.iloc[-1]

    # EMA crossover using last two closed candles
    cross_up   = (df["ema_fast"].iloc[-3] < df["ema_slow"].iloc[-3]) and \
                 (row_prev["ema_fast"] > row_prev["ema_slow"])
    cross_down = (df["ema_fast"].iloc[-3] > df["ema_slow"].iloc[-3]) and \
                 (row_prev["ema_fast"] < row_prev["ema_slow"])

    signal = None
    rsi_val = row_prev["rsi"]
    vwap_val = row_prev["vwap"]
    ltp = row_curr["close"]

    # Bullish: EMA crossup + RSI>55 + price above VWAP
    if cross_up and rsi_val > 55 and ltp > vwap_val:
        signal = "BUY_CE"
    # Bearish: EMA crossdown + RSI<45 + price below VWAP
    elif cross_down and rsi_val < 45 and ltp < vwap_val:
        signal = "BUY_PE"

    return {
        "signal":   signal,
        "ema_fast": round(row_prev["ema_fast"], 2),
        "ema_slow": round(row_prev["ema_slow"], 2),
        "rsi":      round(rsi_val, 2),
        "vwap":     round(vwap_val, 2),
        "ltp":      round(ltp, 2),
    }


def get_option_ltp(symbol: str) -> float:
    try:
        q = client.quotes(symbol=symbol, exchange=EXCH_FNO)
        if q.get("status") == "success":
            return float(q.get("ltp", 0) or 0)
    except Exception:
        pass
    return 0.0


def enter_trade(option_type: str, expiry: str) -> bool:
    """Place ATM option order (CE or PE). Returns True on success."""
    global position_side, option_symbol, entry_premium

    print(f"\n[{ist_now():%H:%M:%S}] ENTERING {option_type} | Expiry {expiry}")
    try:
        resp = client.optionsorder(
            strategy=STRATEGY_NAME,
            underlying=UNDERLYING,
            exchange=EXCH_INDEX,
            offset="ATM",
            option_type=option_type,
            action="BUY",
            quantity=LOT_SIZE * NUM_LOTS,
            expiry_date=expiry,
            price_type="MARKET",
            product=PRODUCT,
        )
        print(f"[ENTRY] {resp}")
        if resp.get("status") != "success":
            print(f"[ERROR] Entry failed: {resp.get('message')}")
            return False

        option_symbol  = resp.get("symbol", "")
        position_side  = option_type
        entry_premium  = get_option_ltp(option_symbol)
        print(f"[ENTRY] Symbol={option_symbol} | Entry premium=₹{entry_premium:.2f}")
        return True

    except Exception as exc:
        print(f"[ERROR] Entry exception: {exc}")
        return False


def exit_trade(reason: str) -> None:
    """Sell the option held to close the position."""
    global position_side, option_symbol, entry_premium

    print(f"\n[{ist_now():%H:%M:%S}] EXITING | Reason: {reason}")
    if not option_symbol:
        print("[WARN] No option symbol recorded. Using closeposition fallback.")
        client.closeposition(strategy=STRATEGY_NAME)
    else:
        try:
            resp = client.placeorder(
                strategy=STRATEGY_NAME,
                symbol=option_symbol,
                action="SELL",
                exchange=EXCH_FNO,
                price_type="MARKET",
                product=PRODUCT,
                quantity=LOT_SIZE * NUM_LOTS,
            )
            print(f"[EXIT] {resp}")
        except Exception as exc:
            print(f"[ERROR] Exit exception: {exc}")
            client.closeposition(strategy=STRATEGY_NAME)

    position_side  = None
    option_symbol  = ""
    entry_premium  = 0.0


# ─── Main Loop ────────────────────────────────────────────────────────────────

def run_strategy():
    global trades_today, trade_date, position_side

    print("=" * 60)
    print(f"  {STRATEGY_NAME}")
    print(f"  Underlying : {UNDERLYING}  |  Timeframe : 5-minute")
    print(f"  Indicators : EMA({EMA_FAST}/{EMA_SLOW}) + RSI({RSI_PERIOD}) + VWAP")
    print(f"  Target : {TARGET_PCT}%  |  SL : {STOP_PCT}%")
    print(f"  Mode : ANALYZER (paper trade)")
    print("=" * 60)

    expiry = ""

    while True:
        try:
            now = ist_now()

            if not is_market_open():
                print(f"[{now:%H:%M:%S}] Market closed. Sleeping …")
                time.sleep(60)
                continue

            # Reset daily counters
            if trade_date != now.date():
                trade_date   = now.date()
                trades_today = 0
                expiry       = get_nearest_expiry()
                print(f"[{now:%H:%M:%S}] New session. Expiry={expiry} | Trades=0")

            # ── Force exit at 3:15 PM ─────────────────────────────────────────
            if position_side and is_force_exit_time():
                exit_trade("Force exit 3:15 PM")
                time.sleep(60)
                continue

            # ── Monitor open position ─────────────────────────────────────────
            if position_side:
                ltp = get_option_ltp(option_symbol)
                if ltp > 0 and entry_premium > 0:
                    chg_pct = (ltp - entry_premium) / entry_premium * 100
                    print(f"[{now:%H:%M:%S}] {option_symbol} LTP=₹{ltp:.2f} "
                          f"Entry=₹{entry_premium:.2f} Δ={chg_pct:+.2f}%")

                    if chg_pct >= TARGET_PCT:
                        exit_trade(f"Target +{TARGET_PCT}% hit")
                        trades_today += 1
                    elif chg_pct <= -STOP_PCT:
                        exit_trade(f"Stop -{STOP_PCT}% hit")
                        trades_today += 1

                time.sleep(CHECK_INTERVAL)
                continue

            # ── Look for new entry ────────────────────────────────────────────
            if trades_today >= MAX_TRADES_DAY:
                print(f"[{now:%H:%M:%S}] Max trades ({MAX_TRADES_DAY}) reached today.")
                time.sleep(300)
                continue

            if not is_entry_window():
                print(f"[{now:%H:%M:%S}] Outside entry window.")
                time.sleep(60)
                continue

            sigs = fetch_signals()
            if not sigs:
                print(f"[{now:%H:%M:%S}] Could not compute signals. Retrying.")
                time.sleep(30)
                continue

            print(f"[{now:%H:%M:%S}] Signal={sigs['signal']} "
                  f"EMA={sigs['ema_fast']}/{sigs['ema_slow']} "
                  f"RSI={sigs['rsi']} VWAP={sigs['vwap']} LTP={sigs['ltp']}")

            sig = sigs.get("signal")
            if sig == "BUY_CE":
                success = enter_trade("CE", expiry)
                if success:
                    trades_today += 1
            elif sig == "BUY_PE":
                success = enter_trade("PE", expiry)
                if success:
                    trades_today += 1
            else:
                print(f"[{now:%H:%M:%S}] No signal. Waiting …")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n[INFO] Strategy interrupted.")
            if position_side:
                exit_trade("Manual interrupt")
            break
        except Exception as exc:
            print(f"[ERROR] Unexpected: {exc}")
            time.sleep(30)


if __name__ == "__main__":
    run_strategy()
