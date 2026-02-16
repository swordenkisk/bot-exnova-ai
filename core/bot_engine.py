"""
core/bot_engine.py — Trading Bot Engine
========================================

Manages the full lifecycle of the trading bot:
  - Startup / graceful shutdown
  - Main analysis loop with configurable intervals
  - Automatic reconnection with exponential back-off (circuit breaker)
  - Real-time Telegram notifications for key events
  - Per-cycle callbacks for external monitoring

Architecture
------------
BotEngine orchestrates:
  ConfigService → validated configuration (including .env secrets)
  BotState      → shared runtime state
  ExnovaBot     → legacy trading logic (migration in progress)
  TelegramNotifier → async notifications
  AutoRegulationSystem → attached to bot via integrate_auto_regulation()
"""

from __future__ import annotations

import sys
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config_service import get_config_service, AppConfig
from core.bot_state import BotState, BotMode, get_bot_state

# Optional dashboard
try:
    from utils.iq_style_dashboard import IQStyleDashboard, get_iq_dashboard
    IQ_DASHBOARD_AVAILABLE = True
except ImportError:
    IQ_DASHBOARD_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── Callback types ────────────────────────────────────────────────────────────

OnCycleCallback = Callable[[int, dict], None]   # (cycle_number, stats) -> None
OnTradeCallback = Callable[[str, str, float], None]  # (asset, direction, amount) -> None


# ═════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    Prevents repeated rapid reconnection attempts.

    States:
      CLOSED   → Normal operation. Calls go through.
      OPEN     → Too many failures. Calls blocked until cool-down expires.
      HALF_OPEN → Testing recovery: one attempt allowed.
    """

    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        failure_threshold:  int   = 5,
        success_threshold:  int   = 2,
        timeout_seconds:    float = 60.0,
    ) -> None:
        self.failure_threshold  = failure_threshold
        self.success_threshold  = success_threshold
        self.timeout            = timeout_seconds

        self._state           = self.CLOSED
        self._failure_count   = 0
        self._success_count   = 0
        self._last_failure_at = 0.0

    @property
    def state(self) -> str:
        if self._state == self.OPEN:
            if time.monotonic() - self._last_failure_at >= self.timeout:
                self._state = self.HALF_OPEN
                logger.info("🔄 Circuit breaker → HALF_OPEN (testing reconnection)")
        return self._state

    @property
    def is_closed(self) -> bool:
        return self.state in (self.CLOSED, self.HALF_OPEN)

    def record_success(self) -> None:
        self._failure_count = 0
        if self._state == self.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state         = self.CLOSED
                self._success_count = 0
                logger.info("✅ Circuit breaker → CLOSED")

    def record_failure(self) -> None:
        self._failure_count  += 1
        self._success_count   = 0
        self._last_failure_at = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            if self._state != self.OPEN:
                logger.warning(
                    "⛔ Circuit breaker → OPEN (%.0fs cool-down)", self.timeout
                )
            self._state = self.OPEN


# ═════════════════════════════════════════════════════════════════════════════
# BOT ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class BotEngine:
    """
    Orchestrates the full trading bot lifecycle.

    Example
    -------
        engine = BotEngine()
        engine.on_cycle(lambda n, stats: print(f"Cycle {n}"))
        engine.start()   # blocks until shutdown
    """

    # Reconnection back-off: [5s, 10s, 30s, 60s, 120s]
    _BACKOFF: List[float] = [5, 10, 30, 60, 120]
    _ANALYSIS_INTERVAL    = 5       # seconds between main loop cycles
    _HEARTBEAT_CYCLES     = 60      # send Telegram heartbeat every N cycles
    _BALANCE_SYNC_CYCLES  = 12      # sync balance every N cycles

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        state:  Optional[BotState]  = None,
    ) -> None:
        self.config = config or get_config_service().config
        self.state  = state  or get_bot_state()

        self._on_cycle_callbacks: List[OnCycleCallback] = []
        self._on_trade_callbacks: List[OnTradeCallback] = []

        self._shutdown_requested = False
        self._cycle_count        = 0
        self._backoff_index      = 0
        self._last_error:   Optional[Exception] = None
        self._legacy_bot:   Optional[object]    = None

        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            timeout_seconds=120.0,
        )

        # Telegram notifier
        self._notifier = self._init_notifier()

        # IQ Dashboard (optional)
        self.iq_dashboard = self._init_iq_dashboard()

        logger.info("BotEngine initialised")

    # ── Init helpers ──────────────────────────────────────────────────────────

    def _init_notifier(self):
        try:
            from core.telegram_notifier import get_notifier
            return get_notifier(
                token   = self.config.telegram.bot_token   or None,
                chat_id = self.config.telegram.chat_id     or None,
            )
        except Exception as exc:
            logger.warning("Telegram notifier unavailable: %s", exc)
            return None

    def _init_iq_dashboard(self):
        if not IQ_DASHBOARD_AVAILABLE:
            return None
        try:
            demo = getattr(self.config.bot, "modo_conta", "PRACTICE") == "PRACTICE"
            return get_iq_dashboard(mode="demo" if demo else "real")
        except Exception as exc:
            logger.warning("IQ Dashboard init failed: %s", exc)
            return None

    def _notify(self, method: str, *args, **kwargs) -> None:
        if self._notifier:
            try:
                getattr(self._notifier, method)(*args, **kwargs)
            except Exception as exc:
                logger.debug("Telegram notification error: %s", exc)

    # ── Callback registration ─────────────────────────────────────────────────

    def on_cycle(self, callback: OnCycleCallback) -> None:
        self._on_cycle_callbacks.append(callback)

    def on_trade(self, callback: OnTradeCallback) -> None:
        self._on_trade_callbacks.append(callback)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, blocking: bool = True) -> None:
        """
        Start the bot engine.

        Parameters
        ----------
        blocking : If True (default), blocks the calling thread until
                   shutdown is requested. If False, runs in a daemon thread.
        """
        logger.info("=" * 60)
        logger.info("🚀 BOT ENGINE — STARTING")
        logger.info("=" * 60)

        self.state.bot_mode = BotMode.CONNECTING

        try:
            if not self._initialize_legacy_bot():
                logger.error("❌ Failed to initialise legacy bot")
                return

            if not self._connect():
                logger.error("❌ Failed to connect to broker API")
                return

            if self._legacy_bot:
                self._legacy_bot.bot_running = True

            self.state.bot_mode = BotMode.ANALYZING
            self._notify(
                "bot_event",
                event="started",
                detail=f"Mode: {self.config.bot.strategy_mode} | "
                       f"Account: {self.config.bot.modo_conta}",
                balance=self.state.balance,
            )

            if blocking:
                self._main_loop()
            else:
                t = threading.Thread(
                    target=self._main_loop, daemon=True, name="BotMainLoop"
                )
                t.start()

        except Exception as exc:
            logger.exception("❌ Fatal error in BotEngine: %s", exc)
            self._last_error = exc
            self._notify("bot_event", event="error", detail=str(exc))
        finally:
            self._cleanup()

    def stop(self) -> None:
        """Request graceful shutdown."""
        logger.info("⏹️  Shutdown requested")
        self._shutdown_requested = True

    # ── Main Loop ─────────────────────────────────────────────────────────────

    def _main_loop(self) -> None:
        logger.info("🔧 Main loop started")
        interval = self._ANALYSIS_INTERVAL

        while not self._shutdown_requested:
            cycle_start = time.monotonic()
            self._cycle_count += 1

            stats = {
                "cycle":    self._cycle_count,
                "analyzed": 0,
                "signals":  0,
                "trades":   0,
                "skipped":  0,
            }

            try:
                # ── Check connection ───────────────────────────────────────────
                if not self.state.is_connected:
                    self._handle_reconnection()
                    time.sleep(interval)
                    continue

                # ── Reset backoff on successful cycle ──────────────────────────
                self._backoff_index = 0
                self._circuit_breaker.record_success()

                # ── Market scan ────────────────────────────────────────────────
                if self._legacy_bot and hasattr(self._legacy_bot, "active_assets"):
                    self._scan_market(stats)
                    stats["analyzed"] = len(self._legacy_bot.active_assets)

                # ── Callbacks ──────────────────────────────────────────────────
                for cb in self._on_cycle_callbacks:
                    try:
                        cb(self._cycle_count, stats)
                    except Exception as exc:
                        logger.warning("Cycle callback error: %s", exc)

                # ── Periodic: balance sync ─────────────────────────────────────
                if self._cycle_count % self._BALANCE_SYNC_CYCLES == 0:
                    self._sync_balance()

                # ── Periodic: heartbeat log ────────────────────────────────────
                if self._cycle_count % 5 == 0:
                    logger.info(
                        "❤️  Cycle %d | Mode: %s | Balance: $%.2f",
                        self._cycle_count, self.state.bot_mode, self.state.balance,
                    )

                # ── Periodic: Telegram heartbeat ───────────────────────────────
                if (
                    self._cycle_count % self._HEARTBEAT_CYCLES == 0
                    and self._notifier
                ):
                    self._notify(
                        "heartbeat",
                        cycle=self._cycle_count,
                        balance=self.state.balance,
                        mode=str(self.state.bot_mode),
                    )

            except KeyboardInterrupt:
                logger.info("🛑 Interrupted by user")
                break

            except Exception as exc:
                self._circuit_breaker.record_failure()
                logger.error("❌ Error in cycle %d: %s", self._cycle_count, exc)
                self._last_error = exc
                # Exponential back-off on errors
                backoff = self._BACKOFF[
                    min(self._backoff_index, len(self._BACKOFF) - 1)
                ]
                self._backoff_index += 1
                logger.info("⏳ Backing off for %.0fs…", backoff)
                time.sleep(backoff)
                continue

            # ── Sleep until next cycle ─────────────────────────────────────────
            elapsed    = time.monotonic() - cycle_start
            sleep_time = max(0.1, interval - elapsed)
            time.sleep(sleep_time)

        logger.info("🔚 Main loop exited after %d cycles", self._cycle_count)

    # ── Market Scanning ───────────────────────────────────────────────────────

    def _scan_market(self, stats: dict) -> None:
        """Delegate asset analysis to the legacy bot."""
        if not self._legacy_bot:
            return

        # Resolve pending trades first
        if getattr(self._legacy_bot, "open_trades", None):
            self._legacy_bot.check_results()
            return

        assets = self._legacy_bot.active_assets
        logger.info("🔍 Scanning %d assets | Mode: %s", len(assets), self.state.bot_mode)

        for idx, asset in enumerate(assets):
            try:
                df = self._legacy_bot.get_market_data(asset, 60)
                if df is None or df.empty:
                    continue

                signal = self._legacy_bot.analyze_asset(asset, df)
                if signal and signal.get("action") not in (None, "HOLD"):
                    stats["signals"] += 1
                    executed = self._legacy_bot.execute_trade(
                        asset     = asset,
                        direction = signal["action"],
                        price     = signal.get("price", 0.0),
                        confidence= signal.get("confidence", 0.0),
                        strategies= signal.get("strategies", []),
                    )
                    if executed:
                        stats["trades"] += 1
                        self._notify(
                            "trade_opened",
                            asset      = asset,
                            direction  = signal["action"],
                            amount     = float(self.config.bot.fixed_trade_amount),
                            confidence = signal.get("confidence", 0.0),
                            mode       = self.config.bot.strategy_mode,
                        )
                        # Notify on_trade callbacks
                        for cb in self._on_trade_callbacks:
                            try:
                                cb(asset, signal["action"],
                                   self.config.bot.fixed_trade_amount)
                            except Exception as exc:
                                logger.warning("Trade callback error: %s", exc)

            except Exception as exc:
                logger.error("Error scanning %s: %s", asset, exc)

    # ── Connection Management ─────────────────────────────────────────────────

    def _handle_reconnection(self) -> None:
        """Attempt reconnection with exponential back-off and circuit breaker."""
        if not self._circuit_breaker.is_closed:
            remaining = max(
                0.0,
                self._circuit_breaker.timeout
                - (time.monotonic() - self._circuit_breaker._last_failure_at),
            )
            logger.warning("⛔ Circuit OPEN — waiting %.0fs before retry", remaining)
            time.sleep(min(remaining + 1, 30))
            return

        backoff = self._BACKOFF[min(self._backoff_index, len(self._BACKOFF) - 1)]
        logger.info("🔄 Reconnecting in %.0fs (attempt %d)…",
                    backoff, self._backoff_index + 1)
        time.sleep(backoff)

        if self._connect():
            self._circuit_breaker.record_success()
            self._backoff_index = 0
            self._notify(
                "bot_event",
                event="reconnected",
                detail="Connection restored",
                balance=self.state.balance,
            )
        else:
            self._circuit_breaker.record_failure()
            self._backoff_index += 1
            self._notify(
                "bot_event",
                event="disconnected",
                detail=f"Reconnect attempt {self._backoff_index} failed",
            )

    def _connect(self) -> bool:
        if not self._legacy_bot:
            return False
        try:
            account_type = self.config.bot.modo_conta
            logger.info("🔐 Connecting to %s account…", account_type)
            ok = self._legacy_bot.connect(account_type)
            if ok:
                self.state.is_connected = True
                self._sync_balance()
                logger.info("✅ Connected. Balance: $%.2f", self.state.balance)
                return True
            else:
                logger.error("❌ Connection failed")
                self.state.is_connected = False
                return False
        except Exception as exc:
            logger.error("❌ Connection error: %s", exc)
            self.state.is_connected = False
            return False

    def _sync_balance(self) -> None:
        if self._legacy_bot and hasattr(self._legacy_bot, "get_balance"):
            try:
                bal = self._legacy_bot.get_balance()
                if bal and bal > 0:
                    self.state.balance = bal
            except Exception as exc:
                logger.debug("Balance sync error: %s", exc)

    # ── Legacy Bot Init ───────────────────────────────────────────────────────

    def _initialize_legacy_bot(self) -> bool:
        try:
            from core.exnova_bot import ExnovaBot
            logger.info("📦 Initialising ExnovaBot (legacy)…")
            self._legacy_bot = ExnovaBot()
            self.state.balance = getattr(self._legacy_bot, "balance", 0.0)
            logger.info("✅ ExnovaBot initialised")
            return True
        except ImportError as exc:
            logger.error("❌ Import error: %s", exc)
            return False
        except Exception as exc:
            logger.error("❌ ExnovaBot init error: %s", exc)
            return False

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        logger.info("🧹 Cleaning up…")
        self.state.bot_mode     = BotMode.STOPPED
        self.state.is_connected = False

        self._notify(
            "bot_event",
            event="stopped",
            detail=f"Total cycles: {self._cycle_count}",
            balance=self.state.balance,
        )

        if self._notifier:
            try:
                self._notifier.stop()
            except Exception:
                pass

        logger.info("👋 BotEngine shut down cleanly")
