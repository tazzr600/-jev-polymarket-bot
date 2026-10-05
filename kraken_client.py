from __future__ import annotations

import ccxt


class KrakenTrader:
    def __init__(self, settings):
        self.settings = settings

        self.exchange = ccxt.kraken({
            "apiKey": settings.kraken_api_key,
            "secret": settings.kraken_api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
        })

        self.connected = False
        self.authenticated = False
        self.last_error = None
        self.last_balance = None

    def _require_credentials(self):
        if not self.settings.kraken_api_key:
            raise RuntimeError("KRAKEN_API_KEY is missing")

        if not self.settings.kraken_api_secret:
            raise RuntimeError("KRAKEN_API_SECRET is missing")

    def test_connection(self):
        """
        Verify public Kraken connectivity.
        """
        try:
            self.exchange.load_markets()
            self.connected = True
            self.last_error = None
            return {
                "connected": True,
                "authenticated": self.authenticated,
                "error": None,
            }

        except Exception as e:
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"

            return {
                "connected": False,
                "authenticated": False,
                "error": self.last_error,
            }

    def test_authentication(self):
        """
        Verify that the Kraken API credentials actually work
        by requesting the authenticated account balance.
        """
        try:
            self._require_credentials()

            balance = self.exchange.fetch_balance()

            self.authenticated = True
            self.connected = True
            self.last_error = None
            self.last_balance = balance

            return {
                "connected": True,
                "authenticated": True,
                "error": None,
            }

        except Exception as e:
            self.authenticated = False
            self.last_error = f"{type(e).__name__}: {e}"

            return {
                "connected": self.connected,
                "authenticated": False,
                "error": self.last_error,
            }

    def connection_status(self):
        return {
            "connected": self.connected,
            "authenticated": self.authenticated,
            "error": self.last_error,
        }

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self.exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit,
        )

    def fetch_ticker(self, symbol):
        return self.exchange.fetch_ticker(symbol)

    def free_quote(self, currency="USD"):
        balance = self.exchange.fetch_balance()

        free = balance.get("free", {}).get(currency)

        if free is None:
            free = 0

        return float(free)

    def market_buy(self, symbol, quote_amount):
        """
        Buy using a USD quote amount.
        """
        ticker = self.exchange.fetch_ticker(symbol)

        ask = float(
            ticker.get("ask")
            or ticker.get("last")
            or 0
        )

        if ask <= 0:
            raise RuntimeError(
                f"Unable to determine {symbol} market price"
            )

        amount = quote_amount / ask

        order = self.exchange.create_market_buy_order(
            symbol,
            amount,
        )

        price = float(
            order.get("average")
            or order.get("price")
            or ask
        )

        filled = float(
            order.get("filled")
            or amount
        )

        return {
            "order_id": order.get("id"),
            "price": price,
            "amount": filled,
            "raw": order,
        }

    def market_sell(self, symbol, amount):
        order = self.exchange.create_market_sell_order(
            symbol,
            amount,
        )

        ticker = self.exchange.fetch_ticker(symbol)

        fallback_price = float(
            ticker.get("bid")
            or ticker.get("last")
            or 0
        )

        price = float(
            order.get("average")
            or order.get("price")
            or fallback_price
        )

        filled = float(
            order.get("filled")
            or amount
        )

        return {
            "order_id": order.get("id"),
            "price": price,
            "amount": filled,
            "raw": order,
        }
