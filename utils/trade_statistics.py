# utils/trade_statistics.py
"""
Trade Statistics Computation
============================
Accepts a list of sandbox trades (dicts with keys: action, quantity, price,
trade_timestamp, strategy, symbol, exchange) and computes fully-paired round-trip
statistics using FIFO matching.

Round-trip pairing rules
------------------------
* Trades are grouped by (symbol, exchange).
* Within each group, sorted by trade_timestamp ASC.
* A LONG trade opens when a BUY arrives with no short inventory.
* A SHORT trade opens when a SELL arrives with no long inventory.
* Each fill is decomposed into 1-share lots and matched FIFO.
* "manual" = strategy is None / empty string / the literal 'Manual'.

Statistics produced
-------------------
Overall Performance
  total_trades, win_rate, wins, losses, profit_factor
Profit & Loss
  total_profit, total_loss, best_win, worst_loss, avg_win, avg_loss
Streaks
  best_streak, worst_losing_run, current_streak
BUY vs SELL (direction of entry)
  buy_trades, buy_wins, buy_win_rate
  sell_trades, sell_wins, sell_win_rate   (short-side)
Manual Closes
  manual_total, manual_profit, manual_loss, manual_win_rate, manual_pnl
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal
from typing import Any


def _is_manual(strategy: str | None) -> bool:
    if not strategy:
        return True
    return strategy.strip().lower() in {"manual", ""}


def _to_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def compute_round_trips(trades: list[dict]) -> list[dict]:
    """
    FIFO-pair a flat list of trade dicts into closed round-trips.

    Each input trade dict must have:
        action          : 'BUY' or 'SELL'
        quantity        : int
        price           : float / Decimal
        trade_timestamp : datetime (or ISO string)
        strategy        : str | None
        symbol          : str
        exchange        : str

    Returns a list of round-trip dicts:
        symbol, exchange, direction ('LONG'|'SHORT'),
        entry_price, exit_price, quantity, pnl,
        entry_ts, exit_ts, strategy, manual (bool)
    """
    # Group by (symbol, exchange)
    groups: dict[tuple, list] = {}
    for t in trades:
        key = (t["symbol"], t["exchange"])
        groups.setdefault(key, []).append(t)

    round_trips: list[dict] = []

    for (symbol, exchange), group in groups.items():
        # Sort by timestamp ascending
        def _ts(t):
            ts = t.get("trade_timestamp")
            if isinstance(ts, datetime):
                return ts
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    pass
            return datetime.min

        group.sort(key=_ts)

        # FIFO queues: each element is (price, strategy, timestamp) per lot
        long_q:  deque = deque()   # lots waiting to be closed by a SELL
        short_q: deque = deque()   # lots waiting to be closed by a BUY

        for trade in group:
            action   = trade["action"].upper()
            qty      = int(trade["quantity"])
            price    = _to_float(trade["price"])
            strategy = trade.get("strategy") or None
            ts       = _ts(trade)

            if action == "BUY":
                remaining = qty
                # First close any open shorts
                while remaining > 0 and short_q:
                    entry_price, entry_strategy, entry_ts = short_q.popleft()
                    pnl = entry_price - price   # short: profit when buy < sell
                    round_trips.append({
                        "symbol":       symbol,
                        "exchange":     exchange,
                        "direction":    "SHORT",
                        "entry_price":  entry_price,
                        "exit_price":   price,
                        "quantity":     1,
                        "pnl":          round(pnl, 2),
                        "entry_ts":     entry_ts,
                        "exit_ts":      ts,
                        "strategy":     entry_strategy,
                        "manual":       _is_manual(entry_strategy),
                    })
                    remaining -= 1
                # Remaining = new long lots
                for _ in range(remaining):
                    long_q.append((price, strategy, ts))

            elif action == "SELL":
                remaining = qty
                # First close any open longs
                while remaining > 0 and long_q:
                    entry_price, entry_strategy, entry_ts = long_q.popleft()
                    pnl = price - entry_price   # long: profit when sell > buy
                    round_trips.append({
                        "symbol":       symbol,
                        "exchange":     exchange,
                        "direction":    "LONG",
                        "entry_price":  entry_price,
                        "exit_price":   price,
                        "quantity":     1,
                        "pnl":          round(pnl, 2),
                        "entry_ts":     entry_ts,
                        "exit_ts":      ts,
                        "strategy":     entry_strategy,
                        "manual":       _is_manual(entry_strategy),
                    })
                    remaining -= 1
                # Remaining = new short lots
                for _ in range(remaining):
                    short_q.append((price, strategy, ts))

    # Sort all round trips by exit_ts for streak computation
    round_trips.sort(key=lambda r: r["exit_ts"] or datetime.min)
    return round_trips


def compute_statistics(round_trips: list[dict]) -> dict:
    """
    Compute all statistics from a list of round-trip dicts.
    Returns a serialisable dict (all values are Python int/float/None).
    """
    if not round_trips:
        return _empty_stats()

    total  = len(round_trips)
    wins   = [r for r in round_trips if r["pnl"] > 0]
    losses = [r for r in round_trips if r["pnl"] <= 0]

    win_count  = len(wins)
    loss_count = len(losses)
    win_rate   = round(win_count / total * 100, 1) if total else 0.0

    total_profit = round(sum(r["pnl"] for r in wins),   2)
    total_loss   = round(sum(r["pnl"] for r in losses), 2)
    profit_factor = (
        round(total_profit / abs(total_loss), 2)
        if total_loss != 0 else (float("inf") if total_profit > 0 else 0.0)
    )

    best_win   = round(max((r["pnl"] for r in wins),   default=0.0), 2)
    worst_loss = round(min((r["pnl"] for r in losses), default=0.0), 2)
    avg_win    = round(total_profit / win_count,  2) if win_count  else 0.0
    avg_loss   = round(total_loss   / loss_count, 2) if loss_count else 0.0

    # ── Streaks ──────────────────────────────────────────────────────────────
    outcomes      = [1 if r["pnl"] > 0 else -1 for r in round_trips]
    best_streak   = 0
    worst_run     = 0
    cur_streak_pos = 0
    cur_streak_neg = 0
    cur_max_pos    = 0
    cur_max_neg    = 0

    for o in outcomes:
        if o > 0:
            cur_streak_pos += 1
            cur_streak_neg = 0
            cur_max_pos = max(cur_max_pos, cur_streak_pos)
        else:
            cur_streak_neg += 1
            cur_streak_pos = 0
            cur_max_neg = max(cur_max_neg, cur_streak_neg)

    best_streak = cur_max_pos
    worst_run   = cur_max_neg

    # Current streak: scan backwards
    current_streak = 0
    if outcomes:
        sign = outcomes[-1]
        for o in reversed(outcomes):
            if o == sign:
                current_streak += 1
            else:
                break
        if sign < 0:
            current_streak = -current_streak

    # ── BUY vs SELL (entry direction) ────────────────────────────────────────
    long_rt  = [r for r in round_trips if r["direction"] == "LONG"]
    short_rt = [r for r in round_trips if r["direction"] == "SHORT"]

    long_wins  = [r for r in long_rt  if r["pnl"] > 0]
    short_wins = [r for r in short_rt if r["pnl"] > 0]

    buy_win_rate  = round(len(long_wins)  / len(long_rt)  * 100, 1) if long_rt  else 0.0
    sell_win_rate = round(len(short_wins) / len(short_rt) * 100, 1) if short_rt else 0.0

    # ── Manual Closes ─────────────────────────────────────────────────────────
    manual_rt      = [r for r in round_trips if r["manual"]]
    manual_wins    = [r for r in manual_rt   if r["pnl"] > 0]
    manual_losses  = [r for r in manual_rt   if r["pnl"] <= 0]
    manual_pnl     = round(sum(r["pnl"] for r in manual_rt), 2)
    manual_win_rate = round(len(manual_wins) / len(manual_rt) * 100, 1) if manual_rt else 0.0

    return {
        "overall": {
            "total_trades":   total,
            "win_rate":       win_rate,
            "wins":           win_count,
            "losses":         loss_count,
            "profit_factor":  profit_factor,
        },
        "pnl": {
            "total_profit":  total_profit,
            "total_loss":    total_loss,
            "best_win":      best_win,
            "worst_loss":    worst_loss,
            "avg_win":       avg_win,
            "avg_loss":      avg_loss,
            "net_pnl":       round(total_profit + total_loss, 2),
        },
        "streaks": {
            "best_streak":      best_streak,
            "worst_losing_run": worst_run,
            "current_streak":   current_streak,
        },
        "direction": {
            "buy_trades":    len(long_rt),
            "buy_wins":      len(long_wins),
            "buy_win_rate":  buy_win_rate,
            "sell_trades":   len(short_rt),
            "sell_wins":     len(short_wins),
            "sell_win_rate": sell_win_rate,
        },
        "manual_closes": {
            "total":        len(manual_rt),
            "profit_count": len(manual_wins),
            "loss_count":   len(manual_losses),
            "win_rate":     manual_win_rate,
            "pnl":          manual_pnl,
        },
    }


def _empty_stats() -> dict:
    return {
        "overall":  {"total_trades": 0, "win_rate": 0.0, "wins": 0, "losses": 0, "profit_factor": 0.0},
        "pnl":      {"total_profit": 0.0, "total_loss": 0.0, "best_win": 0.0, "worst_loss": 0.0,
                     "avg_win": 0.0, "avg_loss": 0.0, "net_pnl": 0.0},
        "streaks":  {"best_streak": 0, "worst_losing_run": 0, "current_streak": 0},
        "direction": {"buy_trades": 0, "buy_wins": 0, "buy_win_rate": 0.0,
                      "sell_trades": 0, "sell_wins": 0, "sell_win_rate": 0.0},
        "manual_closes": {"total": 0, "profit_count": 0, "loss_count": 0, "win_rate": 0.0, "pnl": 0.0},
    }
