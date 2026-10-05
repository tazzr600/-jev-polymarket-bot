from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from bot import bot
from config import settings

from db import (
    equity_history,
    get_positions,
    init_db,
    stats,
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="KRAKEN BOT",
    version="2.0.0",
)


# ============================================================
# KRAKEN AUTHENTICATION
# ============================================================

async def authenticate_kraken():

    # --------------------------------------------------------
    # PUBLIC CONNECTION TEST
    # --------------------------------------------------------

    connection = await asyncio.to_thread(
        bot.kraken.test_connection
    )

    if not connection.get(
        "connected"
    ):

        return connection

    # --------------------------------------------------------
    # PRIVATE API TEST
    # --------------------------------------------------------

    return await asyncio.to_thread(
        bot.kraken.test_authentication
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    print("")
    print("=" * 70)
    print("KRAKEN BOT STARTING")
    print("=" * 70)

    # --------------------------------------------------------
    # ENVIRONMENT
    # --------------------------------------------------------

    print(
        "PORT:",
        os.getenv(
            "PORT",
            "8000"
        )
    )

    print(
        "AUTONOMOUS:",
        settings.autonomous
    )

    print(
        "DRY RUN:",
        settings.dry_run
    )

    print(
        "LIVE TRADING:",
        settings.live_trading
    )

    print(
        "TIMEFRAME:",
        settings.timeframe
    )

    print(
        "MAX ML MARKETS:",
        settings.max_scan_symbols
    )

    print(
        "ALLOWED QUOTES:",
        settings.allowed_quote_list
    )

    # --------------------------------------------------------
    # KRAKEN AUTHENTICATION
    # --------------------------------------------------------

    result = await authenticate_kraken()

    if result.get(
        "authenticated"
    ):

        print(
            "KRAKEN CONNECTION: OK"
        )

        print(
            "KRAKEN AUTHENTICATION: OK"
        )

    else:

        print(
            "KRAKEN AUTHENTICATION FAILED:"
        )

        print(
            result.get(
                "error"
            )
        )

    # --------------------------------------------------------
    # LIVE ORDER STATUS
    # --------------------------------------------------------

    print(
        "KRAKEN LIVE ORDERS:",
        bot.kraken.live_orders_enabled
    )

    print(
        "KRAKEN CREDENTIALS CONFIGURED:",
        bot.kraken.credentials_configured()
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # START AUTONOMOUS ENGINE
    # --------------------------------------------------------

    asyncio.create_task(
        bot.run()
    )

    print(
        "BOT ENGINE STARTED"
    )

    print(
        "=" * 70
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    kraken = (
        bot.kraken.connection_status()
    )

    return {

        "ok":
            True,

        "service":
            "kraken-bot",

        "running":
            bot.running,

        "mode":
            (
                "LIVE"
                if (
                    settings.live_trading
                    and
                    not settings.dry_run
                )
                else
                "PAPER"
            ),

        "exchange":
            "kraken",

        "kraken_connected":
            kraken[
                "connected"
            ],

        "kraken_authenticated":
            kraken[
                "authenticated"
            ],

        "kraken_error":
            kraken[
                "error"
            ],

        "api_configured":
            kraken[
                "credentials_configured"
            ],

        "live_orders_enabled":
            kraken[
                "live_orders_enabled"
            ],

        "cycle":
            bot.cycle_count,

        "last_cycle":
            bot.last_cycle,

        "last_scan":
            bot.last_scan,

        "error":
            bot.error,
    }


# ============================================================
# BOT STATUS
# ============================================================

@app.get("/api/status")
async def status():

    kraken = (
        bot.kraken.connection_status()
    )

    current_stats = (
        stats()
    )

    return {

        # ----------------------------------------------------
        # BOT
        # ----------------------------------------------------

        "running":
            bot.running,

        "mode":
            (
                "LIVE"
                if (
                    settings.live_trading
                    and
                    not settings.dry_run
                )
                else
                "PAPER"
            ),

        "cycle":
            bot.cycle_count,

        "last_cycle":
            bot.last_cycle,

        "last_scan":
            bot.last_scan,

        # ----------------------------------------------------
        # KRAKEN
        # ----------------------------------------------------

        "kraken":
            kraken,

        # ----------------------------------------------------
        # AI SIGNALS
        # ----------------------------------------------------

        "signals":
            bot.signals,

        # ----------------------------------------------------
        # POSITIONS
        # ----------------------------------------------------

        "positions":
            get_positions(),

        # ----------------------------------------------------
        # PERFORMANCE
        # ----------------------------------------------------

        "stats":
            current_stats,

        # ----------------------------------------------------
        # EQUITY
        # ----------------------------------------------------

        "equity":
            equity_history(
                240
            ),

        # ----------------------------------------------------
        # SCANNER
        # ----------------------------------------------------

        "scanner":
            bot.scanner.status(),

        # ----------------------------------------------------
        # ERRORS
        # ----------------------------------------------------

        "error":
            bot.error,
    }


# ============================================================
# EQUITY ENDPOINT
# ============================================================

@app.get("/api/equity")
async def equity():

    return {

        "history":
            equity_history(
                480
            ),

        "stats":
            stats(),
    }


# ============================================================
# KRAKEN TEST
# ============================================================

@app.post("/api/kraken/test")
async def kraken_test():

    result = (
        await authenticate_kraken()
    )

    return {

        "success":
            result.get(
                "authenticated",
                False
            ),

        "kraken":
            bot.kraken.connection_status(),

        "result":
            result,
    }


# ============================================================
# START BOT
# ============================================================

@app.post("/api/start")
async def start():

    # --------------------------------------------------------
    # ALWAYS VERIFY KRAKEN FIRST
    # --------------------------------------------------------

    result = (
        await authenticate_kraken()
    )

    if not result.get(
        "authenticated"
    ):

        return {

            "running":
                False,

            "success":
                False,

            "error":
                (
                    "Kraken authentication failed: "
                    +
                    str(
                        result.get(
                            "error"
                        )
                    )
                ),

            "kraken":
                bot.kraken.connection_status(),
        }

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    bot.running = True

    bot.error = None

    return {

        "running":
            True,

        "success":
            True,

        "mode":
            (
                "LIVE"
                if (
                    settings.live_trading
                    and
                    not settings.dry_run
                )
                else
                "PAPER"
            ),

        "kraken":
            bot.kraken.connection_status(),
    }


# ============================================================
# STOP BOT
# ============================================================

@app.post("/api/stop")
async def stop():

    bot.running = False

    return {

        "running":
            False,

        "success":
            True,

        "message":
            "Bot stopped. Existing paper positions remain "
            "visible until managed by the engine.",
    }


# ============================================================
# MANUAL FULL-MARKET SCAN
# ============================================================

@app.post("/api/scan")
async def scan():

    try:

        signals = await bot.scan()

        return {

            "success":
                True,

            "signals":
                signals,

            "scanner":
                bot.scanner.status(),

            "last_scan":
                bot.last_scan,
        }

    except Exception as exc:

        bot.error = (
            f"MANUAL SCAN ERROR: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return {

            "success":
                False,

            "signals":
                [],

            "error":
                bot.error,

            "scanner":
                bot.scanner.status(),
        }


# ============================================================
# SCANNER STATUS
# ============================================================

@app.get("/api/scanner")
async def scanner():

    return {

        "status":
            bot.scanner.status(),

        "markets":
            [
                market.to_dict()
                for market
                in bot.scanner.universe
            ],
    }


# ============================================================
# TOP AI SIGNALS
# ============================================================

@app.get("/api/signals")
async def signals():

    return {

        "signals":
            bot.signals,

        "last_scan":
            bot.last_scan,
    }


# ============================================================
# OPEN POSITIONS
# ============================================================

@app.get("/api/positions")
async def positions():

    return {

        "positions":
            get_positions(),

        "stats":
            stats(),
    }


# ============================================================
# PERFORMANCE
# ============================================================

@app.get("/api/performance")
async def performance():

    current_stats = (
        stats()
    )

    return {

        "paper_start":
            current_stats[
                "paper_start_balance"
            ],

        "equity":
            current_stats[
                "paper_equity"
            ],

        "cash":
            current_stats[
                "paper_balance"
            ],

        "invested":
            current_stats[
                "paper_invested"
            ],

        "realized_pnl":
            current_stats[
                "realized_pnl"
            ],

        "unrealized_pnl":
            current_stats[
                "unrealized_pnl"
            ],

        "return_pct":
            current_stats[
                "return_pct"
            ],

        "drawdown_pct":
            current_stats[
                "drawdown_pct"
            ],

        "trades":
            current_stats[
                "trades"
            ],

        "wins":
            current_stats[
                "wins"
            ],

        "losses":
            current_stats[
                "losses"
            ],

        "win_rate":
            current_stats[
                "win_rate"
            ],

        "profit_factor":
            current_stats[
                "profit_factor"
            ],
    }


# ============================================================
# ROOT DASHBOARD
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home():

    try:

        with open(
            "index.html",
            "r",
            encoding="utf-8"
        ) as file:

            return file.read()

    except FileNotFoundError:

        return HTMLResponse(
            content="""
            <html>
            <head>
                <title>KRAKEN BOT</title>
            </head>

            <body
                style="
                    background:#08090b;
                    color:white;
                    font-family:Arial;
                    padding:40px;
                "
            >

                <h1>KRAKEN BOT</h1>

                <p>
                    Bot API is online.
                </p>

                <p>
                    index.html was not found.
                </p>

                <p>
                    Upload the dashboard file to the
                    root of the GitHub repository.
                </p>

            </body>
            </html>
            """,
            status_code=500
        )
