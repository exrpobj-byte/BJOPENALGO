#!/usr/bin/env python
"""
Swing Trade Strategy – EMA + MACD Crossover on Nifty50 Stocks
==============================================================
Timeframe   : Daily candles
Universe    : Top 10 liquid Nifty50 stocks
Signal      : EMA(20) cross EMA(50) + MACD signal confirmation
              + price above 200-day EMA (trend filter)
Entry       : BUY signal → CNC (delivery) order next morning at open
Exit        : EMA(20) cross BELOW EMA(50)  OR  3% trailing stop
              OR +6% target (2:1 reward-to-risk)
Product     : CNC (delivery, carry overnight)
Max Positions: 5 concurrent (equal allocation)
Capital     : ₹5,00,000 notional for position sizing
Run Time    : 9:20 AM IST (signal scan) + 3:20 PM IST (stop-loss check)
Mode        : Analyzer (paper trade) via OpenAlgo

Upload to   : http://127.0.0.1:5000/python  →  Add Strategy
Schedule    : 09:15–15:30, Monday–Friday, Exchange = NSE
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

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
STRATEGY_NAME  = "Swing EMA MACD"
EXCHANGE       = "NSE"
PRODUCT        = "CNC"          # delivery – hold for days/weeks
TOTAL_CAPITAL  = 500_000        # ₹5 lakh notional for sizing
MAX_POSITIONS  = 5              # max concurrent open positions
RISK_PER_TRADE = 0.03           # 3% stop loss
TARGET_PCT     = 0.06           # 6% profit target
TRAILING_SL    = 0.03           # 3% trailing stop (updated daily)

# Indicator periods
EMA_FAST   = 20
EMA_SLOW   = 50
EMA_TREND  = 200                # trend filter
MACD_FAST  = 12
MACD_SLOW  = 26
MACD_SIG   = 9

# Stock universe (top liquid Nifty50)
UNIVERSE = [
    "RELIANCE", "HDFCBANK", "INFY", "ICICIBANK", "TCS",
    "KOTAKBANK", "SBIN", "HINDUNILVR", "BAJFINANCE", "AXISBANK",
    "ITC", "LT", "MARUTI", "HCLTECH", "WIPRO",
]

IST = pytz.timezone("Asia/Kolkata")

# ─── Persistent State (JSON file in same directory as strategy) ───────────────
STATE_FILE = Path(__file__).parent / "swing_ema_macd_state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"positions": {}, "last_scan_date": ""}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as exc:
        print(f"[WARN] Could not save state: {exc}")


# ─── Technical Indicators ─────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast  = ema(series, fast)
    ema_slow  = ema(series, slow)
    macd_line = ema_fast - ema_slow
    sig_line  = ema(macd_line, signal)
    hist      = macd_line - sig_line
    return macd_line, sig_line, hist


def ist_now() -> datetime:
    return datetime.now(IST)


def is_scan_time() -> bool:
    """Signal scan window: 9:20–9:40 AM IST."""
    now = ist_now()
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=20) <= now <= now.replace(hour=9, minute=40)


def is_eod_check_time() -> bool:
    """End-of-day stop-loss review: 3:20–3:30 PM IST."""
    now = ist_now()
    if now.weekday() >= 5:
        return False
    return now.replace(hour=15, minute=20) <= now <= now.replace(hour=15, minute=30)


def is_market_hours() -> bool:
    now = ist_now()
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=15) <= now <= now.replace(hour=15, minute=30)


def get_current_ltp(symbol: str) -> float:
    try:
        q = client.quotes(symbol=symbol, exchange=EXCHANGE)
        if q.get("status") == "success":
            return float(q.get("ltp", 0) or 0)
    except Exception:
        pass
    return 0.0


def fetch_daily_data(symbol: str, lookback_days: int = 300) -> pd.DataFrame:
    """Fetch daily OHLCV for a symbol."""
    end_dt   = ist_now().strftime("%Y-%m-%d")
    start_dt = (ist_now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        df = client.history(
            symbol=symbol,
            exchange=EXCHANGE,
            interval="D",
            start_date=start_dt,
            end_date=end_dt,
        )
        if isinstance(df, dict) or df.empty:
            return pd.DataFrame()
        return df
    except Exception as exc:
        print(f"[WARN] History fetch failed for {symbol}: {exc}")
        return pd.DataFrame()


def compute_signals(symbol: str) -> dict:
    """
    Returns a dict with signal ('BUY'|'SELL'|None), indicators, and LTP.
    """
    df = fetch_daily_data(symbol)
    min_bars = EMA_TREND + MACD_SLOW + MACD_SIG + 2
    if df.empty or len(df) < min_bars:
        return {}

    df["ema_fast"]  = ema(df["close"], EMA_FAST)
    df["ema_slow"]  = ema(df["close"], EMA_SLOW)
    df["ema_trend"] = ema(df["close"], EMA_TREND)
    df["macd"], df["macd_sig"], df["macd_hist"] = macd(
        df["close"], MACD_FAST, MACD_SLOW, MACD_SIG
    )

    # Use second-to-last bar (confirmed close, avoid live partial candle)
    cur  = df.iloc[-2]
    prev = df.iloc[-3]

    bullish_cross = (prev["ema_fast"] < prev["ema_slow"]) and \
                    (cur["ema_fast"]  > cur["ema_slow"])
    bearish_cross = (prev["ema_fast"] > prev["ema_slow"]) and \
                    (cur["ema_fast"]  < cur["ema_slow"])

    macd_bullish = cur["macd"] > cur["macd_sig"] and cur["macd_hist"] > 0
    macd_bearish = cur["macd"] < cur["macd_sig"] and cur["macd_hist"] < 0
    above_trend  = cur["close"] > cur["ema_trend"]

    signal = None
    if bullish_cross and macd_bullish and above_trend:
        signal = "BUY"
    elif bearish_cross and macd_bearish:
        signal = "SELL"

    return {
        "signal":    signal,
        "close":     round(cur["close"],     2),
        "ema_fast":  round(cur["ema_fast"],  2),
        "ema_slow":  round(cur["ema_slow"],  2),
        "ema_trend": round(cur["ema_trend"], 2),
        "macd":      round(cur["macd"],      4),
        "macd_sig":  round(cur["macd_sig"],  4),
    }


def position_size(ltp: float) -> int:
    """Equal-weight allocation from total capital; round down to whole shares."""
    if ltp <= 0:
        return 0
    alloc_per_stock = TOTAL_CAPITAL / MAX_POSITIONS
    qty = int(alloc_per_stock / ltp)
    return max(qty, 1)


def place_buy(symbol: str, qty: int) -> bool:
    try:
        resp = client.placeorder(
            strategy=STRATEGY_NAME,
            symbol=symbol,
            action="BUY",
            exchange=EXCHANGE,
            price_type="MARKET",
            product=PRODUCT,
            quantity=qty,
        )
        print(f"[BUY] {symbol} x{qty} → {resp}")
        return resp.get("status") == "success"
    except Exception as exc:
        print(f"[ERROR] Buy {symbol}: {exc}")
        return False


def place_sell(symbol: str, qty: int) -> bool:
    try:
        resp = client.placeorder(
            strategy=STRATEGY_NAME,
            symbol=symbol,
            action="SELL",
            exchange=EXCHANGE,
            price_type="MARKET",
            product=PRODUCT,
            quantity=qty,
        )
        print(f"[SELL] {symbol} x{qty} → {resp}")
        return resp.get("status") == "success"
    except Exception as exc:
        print(f"[ERROR] Sell {symbol}: {exc}")
        return False


# ─── Morning Scan ─────────────────────────────────────────────────────────────

def morning_scan(state: dict) -> dict:
    """
    Scan universe for BUY signals. Open positions up to MAX_POSITIONS.
    Returns updated state.
    """
    open_pos = state.get("positions", {})
    num_open = len(open_pos)
    print(f"\n[SCAN] Morning scan | Open positions: {num_open}/{MAX_POSITIONS}")

    for symbol in UNIVERSE:
        if num_open >= MAX_POSITIONS:
            print(f"[SCAN] Max positions reached ({MAX_POSITIONS}). Skipping rest.")
            break
        if symbol in open_pos:
            print(f"[SCAN] {symbol}: already in position.")
            continue

        sigs = compute_signals(symbol)
        if not sigs:
            print(f"[SCAN] {symbol}: insufficient data.")
            continue

        print(f"[SCAN] {symbol}: signal={sigs['signal']} "
              f"EMA={sigs['ema_fast']}/{sigs['ema_slow']} "
              f"MACD={sigs['macd']:.4f}/{sigs['macd_sig']:.4f}")

        if sigs["signal"] == "BUY":
            ltp = get_current_ltp(symbol)
            if ltp <= 0:
                print(f"[SCAN] {symbol}: could not fetch LTP.")
                continue
            qty = position_size(ltp)
            stop_price   = round(ltp * (1 - RISK_PER_TRADE), 2)
            target_price = round(ltp * (1 + TARGET_PCT), 2)
            print(f"[SCAN] {symbol}: BUY signal! LTP={ltp} qty={qty} "
                  f"SL={stop_price} Target={target_price}")
            success = place_buy(symbol, qty)
            if success:
                open_pos[symbol] = {
                    "qty":         qty,
                    "entry_price": ltp,
                    "stop_price":  stop_price,
                    "target":      target_price,
                    "high_since_entry": ltp,
                    "entry_date":  ist_now().date().isoformat(),
                }
                num_open += 1
                state["positions"] = open_pos
                save_state(state)
                time.sleep(1)   # avoid rate limits

    return state


# ─── EOD Stop-Loss & Target Check ─────────────────────────────────────────────

def eod_check(state: dict) -> dict:
    """
    Review all open positions at EOD.
    - Update trailing stop.
    - Exit if stop or target breached.
    - Check EMA crossover sell signal.
    """
    open_pos = state.get("positions", {})
    to_exit  = []

    print(f"\n[EOD] Checking {len(open_pos)} open positions …")

    for symbol, pos in open_pos.items():
        ltp = get_current_ltp(symbol)
        if ltp <= 0:
            print(f"[EOD] {symbol}: could not fetch LTP, skipping.")
            continue

        entry    = pos["entry_price"]
        stop     = pos["stop_price"]
        target   = pos["target"]
        high_wm  = max(pos.get("high_since_entry", entry), ltp)
        trail_sl = round(high_wm * (1 - TRAILING_SL), 2)
        new_stop = max(stop, trail_sl)            # only ratchet upward
        pnl_pct  = (ltp - entry) / entry * 100

        pos["high_since_entry"] = high_wm
        pos["stop_price"]       = new_stop

        print(f"[EOD] {symbol}: LTP={ltp} Entry={entry} "
              f"SL={new_stop:.2f} Target={target} P&L={pnl_pct:+.2f}%")

        # ── Exit conditions ──────────────────────────────────────────────────
        reason = None
        if ltp <= new_stop:
            reason = f"Stop-loss hit (SL={new_stop})"
        elif ltp >= target:
            reason = f"Target hit ({TARGET_PCT*100:.0f}%)"
        else:
            sigs = compute_signals(symbol)
            if sigs.get("signal") == "SELL":
                reason = "EMA bearish crossover (SELL signal)"

        if reason:
            to_exit.append((symbol, pos["qty"], reason))

    for symbol, qty, reason in to_exit:
        print(f"[EOD] EXITING {symbol}: {reason}")
        success = place_sell(symbol, qty)
        if success:
            del open_pos[symbol]
            state["positions"] = open_pos
            save_state(state)

    return state


# ─── Main Loop ────────────────────────────────────────────────────────────────

def run_strategy():
    state = load_state()

    print("=" * 60)
    print(f"  {STRATEGY_NAME}")
    print(f"  Universe : {len(UNIVERSE)} stocks  |  Max positions : {MAX_POSITIONS}")
    print(f"  EMA({EMA_FAST}/{EMA_SLOW}/{EMA_TREND}) + MACD({MACD_FAST}/{MACD_SLOW}/{MACD_SIG})")
    print(f"  SL : {RISK_PER_TRADE*100:.0f}%  |  Target : {TARGET_PCT*100:.0f}%  "
          f"|  Trailing : {TRAILING_SL*100:.0f}%")
    print(f"  Capital : ₹{TOTAL_CAPITAL:,.0f}  |  Mode : ANALYZER (paper trade)")
    print(f"  Saved state : {len(state.get('positions', {}))} open positions")
    print("=" * 60)

    scan_done_today = False
    eod_done_today  = False

    while True:
        try:
            now = ist_now()

            if not is_market_hours():
                scan_done_today = False
                eod_done_today  = False
                print(f"[{now:%H:%M:%S}] Market closed. Sleeping 5 min …")
                time.sleep(300)
                continue

            # ── Morning scan (once per day) ───────────────────────────────────
            if is_scan_time() and not scan_done_today:
                state = morning_scan(state)
                scan_done_today = True

            # ── EOD check (once per day) ──────────────────────────────────────
            elif is_eod_check_time() and not eod_done_today:
                state = eod_check(state)
                eod_done_today = True

            else:
                # Intra-session: just log open positions periodically
                open_pos = state.get("positions", {})
                if open_pos:
                    symbols = list(open_pos.keys())
                    print(f"[{now:%H:%M:%S}] Holding: {', '.join(symbols)}")
                else:
                    print(f"[{now:%H:%M:%S}] No open positions. Waiting for scan …")

            time.sleep(300)   # check every 5 minutes

        except KeyboardInterrupt:
            print("\n[INFO] Strategy stopped by user.")
            save_state(state)
            break
        except Exception as exc:
            print(f"[ERROR] {exc}")
            save_state(state)
            time.sleep(60)


if __name__ == "__main__":
    run_strategy()
