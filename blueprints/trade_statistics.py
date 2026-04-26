# blueprints/trade_statistics.py
"""
Trade Statistics Blueprint
==========================
Route prefix : /trade-statistics
API endpoint : GET /trade-statistics/api/data
               ?start_date=YYYY-MM-DD  (optional)
               &end_date=YYYY-MM-DD    (optional)
               &strategy=<name>        (optional, '' = all)

Data source  : SandboxTrades in db/sandbox.db (paper trade / Analyzer mode only)
Auth         : session-based (check_session_validity)

No existing code is modified.
"""

from datetime import datetime

import pytz
from flask import Blueprint, jsonify, request, session

from database.sandbox_db import SandboxTrades, db_session as sandbox_session
from utils.logging import get_logger
from utils.session import check_session_validity
from utils.trade_statistics import compute_round_trips, compute_statistics

logger = get_logger(__name__)

trade_stats_bp = Blueprint("trade_stats_bp", __name__, url_prefix="/trade-statistics")

IST = pytz.timezone("Asia/Kolkata")


def _get_user_id_from_session() -> str | None:
    """Return the logged-in user's ID from the Flask session."""
    return session.get("user")


@trade_stats_bp.route("/api/data")
@check_session_validity
def api_statistics():
    """
    Return trade statistics JSON for the frontend.

    Query params:
        start_date  : YYYY-MM-DD  (filter trade_timestamp >= this date)
        end_date    : YYYY-MM-DD  (filter trade_timestamp <= this date)
        strategy    : strategy name filter (empty string = all strategies)
    """
    start_date_str = request.args.get("start_date", "").strip()
    end_date_str   = request.args.get("end_date",   "").strip()
    strategy_filter = request.args.get("strategy",  "").strip()

    try:
        user_id = _get_user_id_from_session()
        if not user_id:
            return jsonify({"status": "error", "message": "Not authenticated"}), 401

        query = sandbox_session.query(SandboxTrades).filter(
            SandboxTrades.user_id == user_id
        )

        # Date filters
        if start_date_str:
            try:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
                query = query.filter(SandboxTrades.trade_timestamp >= start_dt)
            except ValueError:
                return jsonify({"status": "error", "message": "Invalid start_date format"}), 400

        if end_date_str:
            try:
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59
                )
                query = query.filter(SandboxTrades.trade_timestamp <= end_dt)
            except ValueError:
                return jsonify({"status": "error", "message": "Invalid end_date format"}), 400

        # Strategy filter
        if strategy_filter:
            query = query.filter(SandboxTrades.strategy == strategy_filter)

        trades_rows = query.order_by(SandboxTrades.trade_timestamp.asc()).all()

        # Convert ORM rows → plain dicts for the utility
        trades = [
            {
                "action":          row.action,
                "quantity":        row.quantity,
                "price":           float(row.price),
                "trade_timestamp": row.trade_timestamp,
                "strategy":        row.strategy,
                "symbol":          row.symbol,
                "exchange":        row.exchange,
            }
            for row in trades_rows
        ]

        round_trips = compute_round_trips(trades)
        stats       = compute_statistics(round_trips)

        # Also return distinct strategy names for the filter dropdown
        strategy_names: list[str] = []
        try:
            rows = (
                sandbox_session.query(SandboxTrades.strategy)
                .filter(SandboxTrades.user_id == user_id)
                .distinct()
                .all()
            )
            strategy_names = sorted(
                {r.strategy for r in rows if r.strategy},
                key=str.lower,
            )
        except Exception:
            pass

        return jsonify({
            "status":    "success",
            "stats":     stats,
            "trade_count": len(trades),
            "strategies": strategy_names,
            "filters": {
                "start_date": start_date_str or None,
                "end_date":   end_date_str   or None,
                "strategy":   strategy_filter or None,
            },
        })

    except Exception as exc:
        logger.exception(f"Error computing trade statistics: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500
    finally:
        sandbox_session.remove()
