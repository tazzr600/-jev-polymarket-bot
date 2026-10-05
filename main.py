import asyncio

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from bot import bot
from config import settings
from db import init_db, get_positions, stats


app = FastAPI(title="KRAKEN BOT")


@app.on_event("startup")
async def startup():
    init_db()

    # Verify Kraken before starting the autonomous engine.
    result = await asyncio.to_thread(
        bot.kraken.test_connection
    )

    if result["connected"]:
        auth = await asyncio.to_thread(
            bot.kraken.test_authentication
        )

        if auth["authenticated"]:
            print("KRAKEN CONNECTION: OK")
            print("KRAKEN AUTHENTICATION: OK")
        else:
            print(
                "KRAKEN AUTHENTICATION FAILED:",
                auth["error"]
            )
    else:
        print(
            "KRAKEN CONNECTION FAILED:",
            result["error"]
        )

    asyncio.create_task(bot.run())


@app.get("/health")
async def health():
    kraken = bot.kraken.connection_status()

    return {
        "ok": True,
        "running": bot.running,

        "live": settings.live_trading,
        "dry_run": settings.dry_run,

        "exchange": "kraken",

        "kraken_connected": kraken["connected"],
        "kraken_authenticated": kraken["authenticated"],
        "kraken_error": kraken["error"],

        "symbols": settings.symbols,
        "ml_models": len(bot.models),

        "last_scan": bot.last_scan,
        "error": bot.error,

        "api_configured": bool(
            settings.kraken_api_key
            and settings.kraken_api_secret
        ),
    }


@app.get("/api/status")
async def status():
    kraken = bot.kraken.connection_status()

    return {
        "running": bot.running,

        "mode": (
            "LIVE"
            if settings.live_trading and not settings.dry_run
            else "PAPER"
        ),

        "kraken": kraken,

        "signals": bot.signals,
        "positions": get_positions(),
        "stats": stats(),

        "error": bot.error,
        "last_scan": bot.last_scan,
    }


@app.post("/api/start")
async def start():
    result = await asyncio.to_thread(
        bot.kraken.test_authentication
    )

    if not result["authenticated"]:
        return {
            "running": False,
            "error": "Kraken authentication failed",
            "kraken": result,
        }

    bot.running = True

    return {
        "running": True,
        "kraken": result,
    }


@app.post("/api/stop")
async def stop():
    bot.running = False

    return {
        "running": False
    }


@app.post("/api/scan")
async def scan():
    return {
        "signals": await bot.scan()
    }


@app.get("/", response_class=HTMLResponse)
async def home():
    with open(
        "index.html",
        "r",
        encoding="utf-8"
    ) as f:
        return f.read()
