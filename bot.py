from __future__ import annotations

import asyncio
import time

import pandas as pd

from config import settings

from db import (
    add_trade,
    delete_position,
    get_positions,
    get_risk,
    record_equity_snapshot,
    set_position,
    set_risk,
    stats,
)

from kraken_client import KrakenTrader
from market_scanner import KrakenMarketScanner

from ml_model import (
    predict,
    train_model,
)


class KrakenBot:

    def __init__(self):

        # =====================================================
        # KRAKEN
        # =====================================================

        self.kraken = KrakenTrader(
            settings
        )

        # =====================================================
        # FULL MARKET SCANNER
        # =====================================================

        self.scanner = KrakenMarketScanner(
            self.kraken,
            settings
        )

        # =====================================================
        # AI MODEL CACHE
        # =====================================================

        self.models = {}

        self.last_train = {}

        # =====================================================
        # BOT STATE
        # =====================================================

        self.running = (
            settings.autonomous
        )

        self.last_scan = None

        self.error = None

        self.signals = []

        self.cooldown_until = 0

        self.last_equity = None

        self.cycle_count = 0

        self.last_cycle = None

        # =====================================================
        # PAPER ACCOUNT INITIALIZATION
        # =====================================================

        if get_risk(
            "paper_balance"
        ) is None:

            set_risk(
                "paper_balance",
                settings.paper_start_balance
            )

        if get_risk(
            "paper_start_balance"
        ) is None:

            set_risk(
                "paper_start_balance",
                settings.paper_start_balance
            )

        if get_risk(
            "paper_realized_pnl"
        ) is None:

            set_risk(
                "paper_realized_pnl",
                0
            )

        if get_risk(
            "paper_invested"
        ) is None:

            set_risk(
                "paper_invested",
                0
            )

        if get_risk(
            "paper_peak_equity"
        ) is None:

            set_risk(
                "paper_peak_equity",
                settings.paper_start_balance
            )

    # =========================================================
    # DATAFRAME
    # =========================================================

    @staticmethod
    def _df(rows):

        return pd.DataFrame(
            rows,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

    # =========================================================
    # FIND POSITION
    # =========================================================

    @staticmethod
    def _position(
        symbol
    ):

        return next(
            (
                position
                for position
                in get_positions()
                if position["symbol"]
                == symbol
            ),
            None,
        )

    # =========================================================
    # RISK CHECK
    # =========================================================

    def _can_trade(self):

        current_stats = stats()

        # -----------------------------------------------------
        # DAILY LOSS LIMIT
        # -----------------------------------------------------

        if (
            current_stats[
                "last_24h_pnl"
            ]
            <=
            -settings.daily_loss_limit_usd
        ):

            return (
                False,
                "24h loss limit reached"
            )

        # -----------------------------------------------------
        # DAILY TRADE LIMIT
        # -----------------------------------------------------

        if (
            current_stats[
                "trades_24h"
            ]
            >=
            settings.max_trades_per_day
        ):

            return (
                False,
                "24h trade limit reached"
            )

        # -----------------------------------------------------
        # CONSECUTIVE LOSS PROTECTION
        # -----------------------------------------------------

        if (
            current_stats[
                "consecutive_losses"
            ]
            >=
            settings.max_consecutive_losses
        ):

            return (
                False,
                "loss circuit breaker active"
            )

        # -----------------------------------------------------
        # COOLDOWN
        # -----------------------------------------------------

        if (
            time.time()
            <
            self.cooldown_until
        ):

            remaining = (
                self.cooldown_until
                -
                time.time()
            )

            return (
                False,
                f"cooldown active "
                f"{remaining:.0f}s"
            )

        return (
            True,
            ""
        )

    # =========================================================
    # ANALYZE ONE MARKET
    # =========================================================

    async def scan_symbol(
        self,
        candidate
    ):

        symbol = candidate.symbol

        # -----------------------------------------------------
        # FETCH CANDLES
        # -----------------------------------------------------

        rows = await asyncio.to_thread(
            self.kraken.fetch_ohlcv,
            symbol,
            settings.timeframe,
            settings.candles,
        )

        if not rows:

            return None

        df = self._df(
            rows
        )

        # -----------------------------------------------------
        # MINIMUM DATA
        # -----------------------------------------------------

        if len(df) < 150:

            return None

        now = time.time()

        # -----------------------------------------------------
        # GET MODEL
        # -----------------------------------------------------

        state = self.models.get(
            symbol
        )

        # -----------------------------------------------------
        # TRAIN / RETRAIN
        # -----------------------------------------------------

        if (
            state is None
            or
            (
                now
                -
                self.last_train.get(
                    symbol,
                    0
                )
            )
            >=
            settings.train_every_seconds
        ):

            estimated_cost = (
                settings.round_trip_cost_pct
                /
                100
                +
                settings.slippage_buffer_pct
                /
                100
            )

            state = await asyncio.to_thread(
                train_model,
                df,
                settings.forecast_bars,
                estimated_cost,
            )

            self.models[
                symbol
            ] = state

            self.last_train[
                symbol
            ] = now

        # -----------------------------------------------------
        # MODEL PREDICTION
        # -----------------------------------------------------

        prediction = predict(
            state,
            df,
            settings.forecast_bars
        )

        if not prediction:

            return None

        # -----------------------------------------------------
        # MARKET PRICES
        # -----------------------------------------------------

        bid = float(
            candidate.bid
        )

        ask = float(
            candidate.ask
        )

        last = float(
            candidate.last
        )

        # -----------------------------------------------------
        # SPREAD
        # -----------------------------------------------------

        spread = max(
            0.0,
            ask / bid - 1.0
        )

        # -----------------------------------------------------
        # ESTIMATED COST
        # -----------------------------------------------------

        base_cost = (
            settings.round_trip_cost_pct
            /
            100
            +
            settings.slippage_buffer_pct
            /
            100
        )

        total_cost = (
            base_cost
            +
            spread
        )

        # -----------------------------------------------------
        # AI VALUES
        # -----------------------------------------------------

        probability = float(
            prediction[
                "probability_up"
            ]
        )

        expected_move = float(
            prediction[
                "expected_move"
            ]
        )

        strategy_score = float(
            prediction[
                "strategy_score"
            ]
        )

        agreement = float(
            prediction[
                "strategy_agreement"
            ]
        )

        confidence = float(
            prediction[
                "confidence"
            ]
        )

        direction = (
            prediction[
                "direction"
            ]
        )

        # -----------------------------------------------------
        # ML EDGE
        # -----------------------------------------------------

        ml_edge = (
            probability
            -
            0.50
        ) * 2.0

        # -----------------------------------------------------
        # COMBINED EDGE
        # -----------------------------------------------------

        combined_edge = (
            ml_edge
            *
            0.70
            +
            strategy_score
            *
            0.30
        )

        # -----------------------------------------------------
        # EXPECTED PROFIT AFTER COST
        # -----------------------------------------------------

        estimated_profit = (
            expected_move
            -
            total_cost
        )

        # -----------------------------------------------------
        # FINAL RANK SCORE
        # -----------------------------------------------------

        score = (
            combined_edge
            *
            max(
                agreement,
                0.25
            )
            *
            max(
                expected_move,
                0
            )
            -
            total_cost
        )

        # -----------------------------------------------------
        # TRADE FILTER REASONS
        # -----------------------------------------------------

        reasons = []

        # Spot bot is long-only.

        if direction != "LONG":

            reasons.append(
                f"direction={direction}"
            )

        if (
            probability
            <
            settings.min_probability
        ):

            reasons.append(
                f"probability="
                f"{probability:.3f}"
            )

        if (
            confidence
            <
            0.10
        ):

            reasons.append(
                f"confidence="
                f"{confidence:.3f}"
            )

        if (
            expected_move
            <
            settings.min_expected_move
        ):

            reasons.append(
                f"expected_move="
                f"{expected_move:.4f}"
            )

        if (
            expected_move
            <=
            total_cost
        ):

            reasons.append(
                "expected move below "
                "estimated trading cost"
            )

        if (
            strategy_score
            <
            -0.10
        ):

            reasons.append(
                f"strategy bearish="
                f"{strategy_score:.3f}"
            )

        if (
            agreement
            <
            0.35
        ):

            reasons.append(
                f"low strategy agreement="
                f"{agreement:.3f}"
            )

        # -----------------------------------------------------
        # RESULT
        # -----------------------------------------------------

        return {

            "symbol":
                symbol,

            "base":
                candidate.base,

            "quote":
                candidate.quote,

            "price":
                last,

            "bid":
                bid,

            "ask":
                ask,

            "spread":
                spread,

            "spread_pct":
                candidate.spread_pct,

            "volume_24h":
                candidate.quote_volume,

            "liquidity_score":
                candidate.liquidity_score,

            "market_rank":
                candidate.rank,

            "cost_estimate":
                total_cost,

            "probability_up":
                probability,

            "expected_move":
                expected_move,

            "direction":
                direction,

            "confidence":
                confidence,

            "strategy_score":
                strategy_score,

            "strategy_agreement":
                agreement,

            "combined_edge":
                combined_edge,

            "estimated_profit":
                estimated_profit,

            "score":
                score,

            "tradeable":
                (
                    len(reasons) == 0
                    and
                    score > 0
                ),

            "reasons":
                reasons,

            "accuracy":
                state.accuracy,

            "samples":
                state.samples,

            "regime":
                prediction[
                    "regime"
                ],

            "strategies":
                prediction[
                    "strategies"
                ],

            "trained_at":
                state.trained_at,
        }

    # =========================================================
    # FULL MARKET SCAN
    # =========================================================

    async def scan(self):

        candidates = (
            self.scanner.top_symbols()
        )

        results = []

        # -----------------------------------------------------
        # ANALYZE EACH TOP MARKET
        # -----------------------------------------------------

        for candidate in candidates:

            try:

                result = (
                    await self.scan_symbol(
                        candidate
                    )
                )

                if result:

                    results.append(
                        result
                    )

            except Exception as exc:

                print(
                    f"SCAN ERROR "
                    f"{candidate.symbol}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

        # -----------------------------------------------------
        # RANK AI SIGNALS
        # -----------------------------------------------------

        results.sort(
            key=lambda item:
                item["score"],
            reverse=True
        )

        self.signals = results

        self.last_scan = (
            time.time()
        )

        # -----------------------------------------------------
        # LOG RESULTS
        # -----------------------------------------------------

        print(
            "=" * 70
        )

        print(
            "AI MARKET SCAN COMPLETE"
        )

        print(
            f"Markets analyzed: "
            f"{len(results)}"
        )

        # -----------------------------------------------------
        # PRINT TOP SIGNALS
        # -----------------------------------------------------

        for signal in results[:10]:

            print(
                f"AI "
                f"{signal['symbol']} "
                f"rank="
                f"{signal['market_rank']} "
                f"prob="
                f"{signal['probability_up']:.3f} "
                f"confidence="
                f"{signal['confidence']:.3f} "
                f"strategy="
                f"{signal['strategy_score']:.3f} "
                f"agreement="
                f"{signal['strategy_agreement']:.3f} "
                f"move="
                f"{signal['expected_move']:.4f} "
                f"score="
                f"{signal['score']:.5f} "
                f"trade="
                f"{signal['tradeable']}"
            )

        print(
            "=" * 70
        )

        return results

    # =========================================================
    # MARK PAPER EQUITY
    # =========================================================

    async def _mark_equity(self):

        prices = {}

        positions = (
            get_positions()
        )

        for position in positions:

            try:

                ticker = (
                    await asyncio.to_thread(
                        self.kraken.fetch_ticker,
                        position["symbol"]
                    )
                )

                price = float(
                    ticker.get(
                        "bid"
                    )
                    or
                    ticker.get(
                        "last"
                    )
                    or
                    0
                )

                if price > 0:

                    prices[
                        position["symbol"]
                    ] = price

            except Exception as exc:

                print(
                    "EQUITY MARK ERROR:",
                    position["symbol"],
                    type(exc).__name__,
                    str(exc)
                )

        self.last_equity = (
            record_equity_snapshot(
                prices
            )
        )

        return self.last_equity

    # =========================================================
    # CLOSE POSITION
    # =========================================================

    async def _close_position(
        self,
        position,
        reason,
        market_price
    ):

        symbol = (
            position["symbol"]
        )

        try:

            result = (
                await asyncio.to_thread(
                    self.kraken.market_sell,
                    symbol,
                    position["amount"]
                )
            )

            exit_price = float(
                result.get(
                    "price"
                )
                or
                market_price
            )

            amount = float(
                position["amount"]
            )

            entry_price = float(
                position[
                    "entry_price"
                ]
            )

            notional = float(
                position[
                    "notional"
                ]
            )

            # -------------------------------------------------
            # GROSS PNL
            # -------------------------------------------------

            pnl = (
                exit_price
                -
                entry_price
            ) * amount

            # -------------------------------------------------
            # PAPER COST
            # -------------------------------------------------

            estimated_cost = (
                notional
                *
                (
                    settings.round_trip_cost_pct
                    /
                    100
                )
            )

            net_pnl = (
                pnl
                -
                estimated_cost
            )

            mode = (
                "DRY_RUN"
                if settings.dry_run
                else
                "LIVE"
            )

            # -------------------------------------------------
            # TRADE RECORD
            # -------------------------------------------------

            add_trade({

                "symbol":
                    symbol,

                "side":
                    "SELL",

                "price":
                    exit_price,

                "amount":
                    amount,

                "notional":
                    exit_price
                    *
                    amount,

                "pnl":
                    net_pnl,

                "status":
                    "CLOSED",

                "mode":
                    mode,

                "reason":
                    reason,
            })

            # -------------------------------------------------
            # PAPER ACCOUNT
            # -------------------------------------------------

            if settings.dry_run:

                balance = float(
                    get_risk(
                        "paper_balance",
                        settings.paper_start_balance
                    )
                )

                invested = float(
                    get_risk(
                        "paper_invested",
                        0
                    )
                )

                # Capital originally invested + net result.

                returned_capital = (
                    notional
                    +
                    net_pnl
                )

                set_risk(
                    "paper_balance",
                    balance
                    +
                    returned_capital
                )

                set_risk(
                    "paper_invested",
                    max(
                        0,
                        invested
                        -
                        notional
                    )
                )

                realized = float(
                    get_risk(
                        "paper_realized_pnl",
                        0
                    )
                )

                set_risk(
                    "paper_realized_pnl",
                    realized
                    +
                    net_pnl
                )

            # -------------------------------------------------
            # REMOVE POSITION
            # -------------------------------------------------

            delete_position(
                symbol
            )

            # -------------------------------------------------
            # COOLDOWN
            # -------------------------------------------------

            self.cooldown_until = (
                time.time()
                +
                settings.cooldown_minutes
                *
                60
            )

            print(
                f"EXIT "
                f"{symbol} "
                f"reason={reason} "
                f"entry={entry_price:.8f} "
                f"exit={exit_price:.8f} "
                f"net_pnl={net_pnl:.2f}"
            )

            return True

        except Exception as exc:

            self.error = (
                "POSITION EXIT ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            print(
                self.error
            )

            return False

    # =========================================================
    # MANAGE OPEN POSITIONS
    # =========================================================

    async def manage_positions(self):

        positions = (
            get_positions()
        )

        if not positions:

            return

        for position in positions:

            try:

                ticker = (
                    await asyncio.to_thread(
                        self.kraken.fetch_ticker,
                        position["symbol"]
                    )
                )

                price = float(
                    ticker.get(
                        "bid"
                    )
                    or
                    ticker.get(
                        "last"
                    )
                    or
                    0
                )

                if price <= 0:

                    continue

                # -------------------------------------------------
                # HOLD TIME
                # -------------------------------------------------

                age_minutes = (
                    time.time()
                    -
                    position[
                        "opened_ts"
                    ]
                ) / 60.0

                reason = None

                # -------------------------------------------------
                # STOP LOSS
                # -------------------------------------------------

                if (
                    price
                    <=
                    position[
                        "stop_price"
                    ]
                ):

                    reason = (
                        "stop_loss"
                    )

                # -------------------------------------------------
                # TAKE PROFIT
                # -------------------------------------------------

                elif (
                    price
                    >=
                    position[
                        "target_price"
                    ]
                ):

                    reason = (
                        "take_profit"
                    )

                # -------------------------------------------------
                # MAX HOLD
                # -------------------------------------------------

                elif (
                    age_minutes
                    >=
                    settings.max_hold_minutes
                ):

                    reason = (
                        "time_exit"
                    )

                if not reason:

                    continue

                await self._close_position(
                    position,
                    reason,
                    price
                )

            except Exception as exc:

                self.error = (
                    "POSITION ERROR: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print(
                    self.error
                )

    # =========================================================
    # SELECT BEST TRADE
    # =========================================================

    def _best_trade(
        self
    ):

        candidates = [

            signal

            for signal
            in self.signals

            if signal.get(
                "tradeable"
            )

        ]

        if not candidates:

            return None

        # -----------------------------------------------------
        # SORT AGAIN FOR SAFETY
        # -----------------------------------------------------

        candidates.sort(
            key=lambda item:
                item["score"],
            reverse=True
        )

        # -----------------------------------------------------
        # MODEL ACCURACY FILTER
        # -----------------------------------------------------

        qualified = [

            signal

            for signal
            in candidates

            if signal.get(
                "accuracy",
                0
            )
            >=
            settings.min_training_accuracy

        ]

        if not qualified:

            return None

        return qualified[0]

    # =========================================================
    # ENTER BEST TRADE
    # =========================================================

    async def maybe_enter_best(self):

        if not self.signals:

            return

        # -----------------------------------------------------
        # RISK CHECK
        # -----------------------------------------------------

        allowed, reason = (
            self._can_trade()
        )

        if not allowed:

            print(
                f"TRADE BLOCKED: "
                f"{reason}"
            )

            return

        # -----------------------------------------------------
        # SELECT BEST AI TRADE
        # -----------------------------------------------------

        best = (
            self._best_trade()
        )

        if not best:

            print(
                "AI: No trade meets "
                "all requirements."
            )

            return

        symbol = (
            best["symbol"]
        )

        # -----------------------------------------------------
        # DON'T DOUBLE ENTER
        # -----------------------------------------------------

        if self._position(
            symbol
        ):

            return

        # -----------------------------------------------------
        # MAX ONE OPEN POSITION
        # -----------------------------------------------------

        #
        # With a $1,000 paper account we want to validate
        # the signal engine before allowing multiple positions.
        #

        if len(
            get_positions()
        ) >= 1:

            return

        # -----------------------------------------------------
        # MODEL ACCURACY
        # -----------------------------------------------------

        if (
            best["accuracy"]
            <
            settings.min_training_accuracy
        ):

            print(
                f"AI BLOCKED "
                f"{symbol}: "
                f"accuracy="
                f"{best['accuracy']:.3f}"
            )

            return

        # -----------------------------------------------------
        # POSITION SIZE
        # -----------------------------------------------------

        quote_amount = (
            settings.max_trade_usd
        )

        # -----------------------------------------------------
        # PAPER
        # -----------------------------------------------------

        if settings.dry_run:

            balance = float(
                get_risk(
                    "paper_balance",
                    settings.paper_start_balance
                )
            )

            max_allowed = (
                balance
                *
                settings.max_position_pct
            )

            quote_amount = min(
                quote_amount,
                max_allowed
            )

        # -----------------------------------------------------
        # LIVE
        # -----------------------------------------------------

        else:

            try:

                free = (
                    await asyncio.to_thread(
                        self.kraken.free_quote,
                        "USD"
                    )
                )

                max_allowed = (
                    free
                    *
                    settings.max_position_pct
                )

                quote_amount = min(
                    quote_amount,
                    max_allowed
                )

            except Exception as exc:

                print(
                    f"BALANCE ERROR: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                return

        # -----------------------------------------------------
        # MINIMUM TRADE SIZE
        # -----------------------------------------------------

        if quote_amount < 5:

            print(
                f"AI BLOCKED "
                f"{symbol}: "
                f"trade size below minimum"
            )

            return

        # -----------------------------------------------------
        # EXECUTE BUY
        # -----------------------------------------------------

        result = (
            await asyncio.to_thread(
                self.kraken.market_buy,
                symbol,
                quote_amount
            )
        )

        entry = float(
            result.get(
                "price"
            )
            or
            best["ask"]
        )

        amount = float(
            result.get(
                "amount"
            )
            or
            (
                quote_amount
                /
                entry
            )
        )

        notional = (
            entry
            *
            amount
        )

        # -----------------------------------------------------
        # STOP / TARGET
        # -----------------------------------------------------

        stop_price = (
            entry
            *
            (
                1
                -
                settings.stop_loss_pct
            )
        )

        target_price = (
            entry
            *
            (
                1
                +
                settings.take_profit_pct
            )
        )

        # -----------------------------------------------------
        # SAVE POSITION
        # -----------------------------------------------------

        set_position({

            "symbol":
                symbol,

            "entry_price":
                entry,

            "amount":
                amount,

            "notional":
                notional,

            "opened_ts":
                time.time(),

            "stop_price":
                stop_price,

            "target_price":
                target_price,
        })

        # -----------------------------------------------------
        # PAPER BALANCE
        # -----------------------------------------------------

        if settings.dry_run:

            balance = float(
                get_risk(
                    "paper_balance",
                    settings.paper_start_balance
                )
            )

            invested = float(
                get_risk(
                    "paper_invested",
                    0
                )
            )

            set_risk(
                "paper_balance",
                max(
                    0,
                    balance
                    -
                    notional
                )
            )

            set_risk(
                "paper_invested",
                invested
                +
                notional
            )

        # -----------------------------------------------------
        # RECORD ENTRY
        # -----------------------------------------------------

        add_trade({

            "symbol":
                symbol,

            "side":
                "BUY",

            "price":
                entry,

            "amount":
                amount,

            "notional":
                notional,

            "status":
                "OPEN",

            "mode":
                (
                    "DRY_RUN"
                    if settings.dry_run
                    else
                    "LIVE"
                ),

            "reason":
                (
                    f"AI "
                    f"prob="
                    f"{best['probability_up']:.3f} "
                    f"confidence="
                    f"{best['confidence']:.3f} "
                    f"strategy="
                    f"{best['strategy_score']:.3f} "
                    f"agreement="
                    f"{best['strategy_agreement']:.3f} "
                    f"regime="
                    f"{best['regime']} "
                    f"rank="
                    f"{best['market_rank']}"
                ),
        })

        # -----------------------------------------------------
        # LOG ENTRY
        # -----------------------------------------------------

        print(
            "=" * 70
        )

        print(
            "AI ENTRY"
        )

        print(
            f"Market: "
            f"{symbol}"
        )

        print(
            f"Entry: "
            f"{entry:.8f}"
        )

        print(
            f"Amount: "
            f"{amount:.8f}"
        )

        print(
            f"Notional: "
            f"${notional:.2f}"
        )

        print(
            f"AI probability: "
            f"{best['probability_up']:.3f}"
        )

        print(
            f"Confidence: "
            f"{best['confidence']:.3f}"
        )

        print(
            f"Strategy score: "
            f"{best['strategy_score']:.3f}"
        )

        print(
            f"Expected move: "
            f"{best['expected_move']:.4f}"
        )

        print(
            f"Stop: "
            f"{stop_price:.8f}"
        )

        print(
            f"Target: "
            f"{target_price:.8f}"
        )

        print(
            "=" * 70
        )

    # =========================================================
    # ONE COMPLETE BOT CYCLE
    # =========================================================

    async def cycle(self):

        self.cycle_count += 1

        self.last_cycle = (
            time.time()
        )

        print(
            ""
        )

        print(
            "#" * 70
        )

        print(
            f"KRAKEN BOT CYCLE "
            f"#{self.cycle_count}"
        )

        print(
            "#" * 70
        )

        # -----------------------------------------------------
        # 1. MANAGE EXISTING POSITION
        # -----------------------------------------------------

        await self.manage_positions()

        # -----------------------------------------------------
        # 2. SCAN KRAKEN MARKETS
        # -----------------------------------------------------

        await self.scan()

        # -----------------------------------------------------
        # 3. FIND BEST TRADE
        # -----------------------------------------------------

        await self.maybe_enter_best()

        # -----------------------------------------------------
        # 4. MARK EQUITY
        # -----------------------------------------------------

        await self._mark_equity()

        # -----------------------------------------------------
        # 5. SHOW ACCOUNT
        # -----------------------------------------------------

        current_stats = (
            stats()
        )

        print(
            ""
        )

        print(
            "ACCOUNT"
        )

        print(
            f"Equity: "
            f"${current_stats['paper_equity']:.2f}"
        )

        print(
            f"Cash: "
            f"${current_stats['paper_balance']:.2f}"
        )

        print(
            f"Realized P&L: "
            f"${current_stats['realized_pnl']:.2f}"
        )

        print(
            f"Return: "
            f"{current_stats['return_pct'] * 100:.2f}%"
        )

        print(
            f"Trades: "
            f"{current_stats['trades']}"
        )

        print(
            f"Win rate: "
            f"{current_stats['win_rate'] * 100:.1f}%"
        )

        print(
            ""
        )

    # =========================================================
    # MAIN AUTONOMOUS LOOP
    # =========================================================

    async def run(self):

        print(
            "KRAKEN BOT ENGINE STARTED"
        )

        print(
            f"Autonomous: "
            f"{settings.autonomous}"
        )

        print(
            f"Paper mode: "
            f"{settings.dry_run}"
        )

        print(
            f"Live trading: "
            f"{settings.live_trading}"
        )

        while True:

            try:

                if self.running:

                    await self.cycle()

                    self.error = None

                else:

                    # Still mark equity while stopped.

                    await self._mark_equity()

            except Exception as exc:

                self.error = (
                    "BOT ERROR: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print(
                    self.error
                )

            await asyncio.sleep(
                max(
                    5,
                    settings.scan_seconds
                )
            )


# =============================================================
# GLOBAL BOT INSTANCE
# =============================================================

bot = KrakenBot()
