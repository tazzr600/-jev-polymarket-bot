# db.py

import os
import sqlite3
import time


# Railway can provide a persistent DB path through an environment variable.
# If DB_PATH is not set, use the local database file.
DB = os.getenv("DB_PATH", "jev_crypto.sqlite3")


def db():
    """
    Open a SQLite database connection.
    """
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """
    Create all required database tables.

    This MUST run before bot.py attempts to read risk_state,
    positions, or trades.
    """

    c = db()

    try:
        # Trades
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                symbol TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                notional REAL,
                pnl REAL DEFAULT 0,
                status TEXT,
                mode TEXT,
                reason TEXT
            )
        """)

        # Open positions
        c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL,
                amount REAL,
                notional REAL,
                opened_ts REAL,
                stop_price REAL,
                target_price REAL
            )
        """)

        # Bot risk/state values
        c.execute("""
            CREATE TABLE IF NOT EXISTS risk_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Default paper-trading balance
        c.execute("""
            INSERT OR IGNORE INTO risk_state (
                key,
                value
            )
            VALUES (
                'paper_balance',
                '1000'
            )
        """)

        c.commit()

    finally:
        c.close()


def add_trade(x):
    """
    Add a trade to the trade history.
    """

    c = db()

    try:
        c.execute("""
            INSERT INTO trades (
                ts,
                symbol,
                side,
                price,
                amount,
                notional,
                pnl,
                status,
                mode,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            time.time(),
            x.get("symbol"),
            x.get("side"),
            x.get("price"),
            x.get("amount"),
            x.get("notional"),
            x.get("pnl", 0),
            x.get("status"),
            x.get("mode"),
            x.get("reason", "")
        ))

        c.commit()

    finally:
        c.close()


def set_position(x):
    """
    Create or replace an open position.
    """

    c = db()

    try:
        c.execute("""
            INSERT OR REPLACE INTO positions (
                symbol,
                entry_price,
                amount,
                notional,
                opened_ts,
                stop_price,
                target_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            x["symbol"],
            x["entry_price"],
            x["amount"],
            x["notional"],
            x["opened_ts"],
            x["stop_price"],
            x["target_price"]
        ))

        c.commit()

    finally:
        c.close()


def get_positions():
    """
    Return all currently open positions.
    """

    c = db()

    try:
        rows = c.execute("""
            SELECT *
            FROM positions
        """).fetchall()

        return [dict(r) for r in rows]

    finally:
        c.close()


def delete_position(symbol):
    """
    Remove an open position.
    """

    c = db()

    try:
        c.execute("""
            DELETE FROM positions
            WHERE symbol = ?
        """, (symbol,))

        c.commit()

    finally:
        c.close()


def set_risk(key, value):
    """
    Store a risk/state value.
    """

    c = db()

    try:
        c.execute("""
            INSERT OR REPLACE INTO risk_state (
                key,
                value
            )
            VALUES (?, ?)
        """, (
            key,
            str(value)
        ))

        c.commit()

    finally:
        c.close()


def get_risk(key, default=None):
    """
    Retrieve a risk/state value.
    """

    c = db()

    try:
        r = c.execute("""
            SELECT value
            FROM risk_state
            WHERE key = ?
        """, (key,)).fetchone()

        if not r:
            return default

        return r["value"]

    finally:
        c.close()


def stats():
    """
    Calculate bot trading statistics.
    """

    c = db()

    try:
        # Overall closed-trade statistics
        row = c.execute("""
            SELECT
                COALESCE(SUM(pnl), 0) AS pnl,
                COUNT(*) AS trades,
                COALESCE(
                    SUM(
                        CASE
                            WHEN pnl > 0 THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS wins
            FROM trades
            WHERE status = 'CLOSED'
        """).fetchone()

        # Last 24 hours
        today = c.execute("""
            SELECT
                COALESCE(SUM(pnl), 0) AS pnl,
                COUNT(*) AS n
            FROM trades
            WHERE status = 'CLOSED'
              AND ts >= ?
        """, (
            time.time() - 86400,
        )).fetchone()

        # Recent trades for consecutive-loss protection
        losses = c.execute("""
            SELECT pnl
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY ts DESC
            LIMIT 10
        """).fetchall()

    finally:
        c.close()

    n = int(row["trades"] or 0)

    consecutive = 0

    for r in losses:
        if float(r["pnl"] or 0) < 0:
            consecutive += 1
        else:
            break

    paper_balance = float(
        get_risk(
            "paper_balance",
            1000
        )
    )

    return {
        "pnl": float(row["pnl"] or 0),

        "trades": n,

        "wins": int(row["wins"] or 0),

        "win_rate": (
            float(row["wins"]) / n
            if n
            else 0
        ),

        "last_24h_pnl": float(
            today["pnl"] or 0
        ),

        "trades_24h": int(
            today["n"] or 0
        ),

        "consecutive_losses": consecutive,

        "paper_balance": paper_balance
    }


# ============================================================
# DATABASE INITIALIZATION
# ============================================================
#
# This is the critical fix.
#
# bot.py imports get_risk(), then creates KrakenBot().
# KrakenBot() immediately calls get_risk("paper_balance").
#
# Without this call, risk_state doesn't exist yet and Railway
# crashes with:
#
# sqlite3.OperationalError:
# no such table: risk_state
#
# Running init_db() here guarantees the tables exist first.
# ============================================================

init_db()
