"""
core/telegram_notifier.py — Telegram Notification System
=========================================================

Sends real-time notifications to a Telegram chat for all
significant bot events: trades, wins/losses, emergency alerts,
daily summaries, and connection status.

Setup
-----
1. Create a bot via @BotFather → get TELEGRAM_BOT_TOKEN
2. Get your chat ID via @userinfobot → TELEGRAM_CHAT_ID
3. Set both in .env or environment variables.

Usage
-----
    from core.telegram_notifier import TelegramNotifier, get_notifier

    notifier = get_notifier()
    notifier.trade_opened("EURUSD", "CALL", 2.0, confidence=0.73)
    notifier.trade_closed("EURUSD", "WIN", profit=1.68)
    notifier.emergency("5 consecutive losses — switching to CONSERVATIVE")

Notification Levels (TELEGRAM_LEVEL env var)
--------------------------------------------
    ALL        → every event including debug heartbeats
    TRADES     → all trade events (open, close, win, loss)
    ALERTS     → wins, losses, emergency, risk events only
    CRITICAL   → emergency mode and critical errors only
"""

from __future__ import annotations

import os
import logging
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from queue import Queue, Empty
from typing import Optional

import urllib.request
import urllib.error
import json as _json

logger = logging.getLogger(__name__)


# ── Notification Levels ───────────────────────────────────────────────────────

class NotifLevel(Enum):
    ALL      = 0
    TRADES   = 1
    ALERTS   = 2
    CRITICAL = 3


_LEVEL_MAP = {
    "ALL":      NotifLevel.ALL,
    "TRADES":   NotifLevel.TRADES,
    "ALERTS":   NotifLevel.ALERTS,
    "CRITICAL": NotifLevel.CRITICAL,
}


# ── Message Templates ─────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _fmt_trade_open(asset: str, direction: str, amount: float,
                    confidence: float, mode: str) -> str:
    arrow = "📈" if direction.upper() == "CALL" else "📉"
    return (
        f"{arrow} *TRADE OPENED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Asset:       `{asset}`\n"
        f"Direction:   `{direction.upper()}`\n"
        f"Amount:      `${amount:.2f}`\n"
        f"Confidence:  `{confidence:.0%}`\n"
        f"Mode:        `{mode}`\n"
        f"Time:        `{_ts()}`"
    )


def _fmt_trade_close(asset: str, result: str, profit: float,
                     win_rate: float, balance: float) -> str:
    if result.upper() == "WIN":
        icon = "✅"
        profit_str = f"+${profit:.2f}"
    else:
        icon = "❌"
        profit_str = f"-${abs(profit):.2f}"

    return (
        f"{icon} *TRADE CLOSED — {result.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Asset:       `{asset}`\n"
        f"P&L:         `{profit_str}`\n"
        f"Session WR:  `{win_rate:.1f}%`\n"
        f"Balance:     `${balance:.2f}`\n"
        f"Time:        `{_ts()}`"
    )


def _fmt_emergency(reason: str, consecutive_losses: int,
                   current_mode: str) -> str:
    return (
        f"🚨 *EMERGENCY MODE ACTIVATED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Reason:      `{reason}`\n"
        f"Con. Losses: `{consecutive_losses}`\n"
        f"New Mode:    `{current_mode}`\n"
        f"Time:        `{_ts()}`\n\n"
        f"⚠️ Bot has paused trading. Review required."
    )


def _fmt_emergency_cleared(wins: int) -> str:
    return (
        f"✅ *EMERGENCY MODE CLEARED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Recovery:    `{wins} consecutive wins`\n"
        f"Time:        `{_ts()}`\n\n"
        f"▶️ Bot resuming normal operation."
    )


def _fmt_daily_summary(wins: int, losses: int, profit: float,
                       balance: float, top_asset: str) -> str:
    total = wins + losses
    wr    = (wins / total * 100) if total else 0.0
    icon  = "📊"
    return (
        f"{icon} *DAILY SUMMARY*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Trades:      `{total}` (W:{wins} / L:{losses})\n"
        f"Win Rate:    `{wr:.1f}%`\n"
        f"Total P&L:   `{'+'if profit>=0 else ''}${profit:.2f}`\n"
        f"Balance:     `${balance:.2f}`\n"
        f"Best Asset:  `{top_asset}`\n"
        f"Date:        `{datetime.now().strftime('%Y-%m-%d')}`"
    )


def _fmt_bot_event(event: str, detail: str, balance: float = 0.0) -> str:
    icons = {
        "started":     "▶️",
        "stopped":     "⏹️",
        "connected":   "🔗",
        "disconnected":"🔌",
        "reconnected": "♻️",
        "error":       "⛔",
    }
    icon = icons.get(event.lower(), "ℹ️")
    parts = [
        f"{icon} *BOT {event.upper()}*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Info:     `{detail}`",
    ]
    if balance:
        parts.append(f"Balance:  `${balance:.2f}`")
    parts.append(f"Time:     `{_ts()}`")
    return "\n".join(parts)


def _fmt_adjustment(trigger: str, new_confidence: float,
                    consecutive_losses: int) -> str:
    return (
        f"⚙️ *AUTO-ADJUSTMENT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Trigger:     `{trigger}`\n"
        f"Con. Losses: `{consecutive_losses}`\n"
        f"New Conf:    `{new_confidence:.0%}`\n"
        f"Time:        `{_ts()}`"
    )


# ── Core Sender ───────────────────────────────────────────────────────────────

class _TelegramSender:
    """
    Low-level async Telegram sender using a background thread + queue.
    Messages are batched into a queue to avoid blocking the trading loop.
    """

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
    MAX_RETRIES  = 3
    RETRY_DELAY  = 2.0   # seconds between retries

    def __init__(self, token: str, chat_id: str) -> None:
        self.token   = token
        self.chat_id = chat_id
        self._queue: Queue[str] = Queue(maxsize=100)
        self._running = True
        self._thread  = threading.Thread(
            target=self._worker, daemon=True, name="TelegramSender"
        )
        self._thread.start()

    def enqueue(self, text: str) -> None:
        """Non-blocking enqueue. Drops message if queue is full."""
        try:
            self._queue.put_nowait(text)
        except Exception:
            logger.warning("Telegram queue full — message dropped")

    def _worker(self) -> None:
        """Background thread that drains the queue and sends to Telegram."""
        while self._running:
            try:
                text = self._queue.get(timeout=1.0)
                self._send_with_retry(text)
            except Empty:
                continue
            except Exception as exc:
                logger.error("Telegram worker error: %s", exc)

    def _send_with_retry(self, text: str) -> bool:
        url     = self.TELEGRAM_API.format(token=self.token)
        payload = _json.dumps({
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }).encode("utf-8")

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return True
            except urllib.error.HTTPError as e:
                logger.warning("Telegram HTTP %s (attempt %d/%d): %s",
                               e.code, attempt, self.MAX_RETRIES, e.reason)
            except Exception as exc:
                logger.warning("Telegram send error (attempt %d/%d): %s",
                               attempt, self.MAX_RETRIES, exc)
            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY)

        logger.error("Telegram: failed to send after %d attempts", self.MAX_RETRIES)
        return False

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=5.0)


# ── Public Notifier ───────────────────────────────────────────────────────────

class TelegramNotifier:
    """
    High-level Telegram notification interface for the Exnova bot.

    All public methods are non-blocking. If Telegram is not configured
    (missing token/chat_id) the notifier silently no-ops — the bot
    continues running regardless of Telegram availability.

    Example
    -------
        notifier = TelegramNotifier()
        notifier.trade_opened("EURUSD", "CALL", 2.0, 0.75, "NORMAL")
        notifier.trade_closed("EURUSD", "WIN", 1.68, win_rate=72.0, balance=1050.0)
        notifier.emergency("5 consecutive losses", 5, "CONSERVATIVE")
        notifier.daily_summary(wins=18, losses=7, profit=12.5, balance=1012.5,
                               top_asset="EURUSD")
    """

    def __init__(
        self,
        token:    Optional[str] = None,
        chat_id:  Optional[str] = None,
        level:    NotifLevel    = NotifLevel.TRADES,
    ) -> None:
        self._token   = token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._level   = level

        # Parse level from env if not passed explicitly
        env_level = os.getenv("TELEGRAM_LEVEL", "").upper()
        if env_level in _LEVEL_MAP:
            self._level = _LEVEL_MAP[env_level]

        if self._token and self._chat_id:
            self._sender: Optional[_TelegramSender] = _TelegramSender(
                self._token, self._chat_id
            )
            logger.info("✅ Telegram notifier active (chat_id=%s, level=%s)",
                        self._chat_id, self._level.name)
        else:
            self._sender = None
            logger.info("ℹ️  Telegram notifier disabled (no token/chat_id)")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send(self, text: str, min_level: NotifLevel = NotifLevel.ALL) -> None:
        """Send if configured and level allows."""
        if self._sender and self._level.value <= min_level.value:
            self._sender.enqueue(text)

    # ── Trade Events ──────────────────────────────────────────────────────────

    def trade_opened(
        self,
        asset:      str,
        direction:  str,
        amount:     float,
        confidence: float = 0.0,
        mode:       str   = "NORMAL",
    ) -> None:
        """Call when a new trade is opened."""
        self._send(
            _fmt_trade_open(asset, direction, amount, confidence, mode),
            NotifLevel.TRADES,
        )

    def trade_closed(
        self,
        asset:    str,
        result:   str,       # "WIN" | "LOSS"
        profit:   float = 0.0,
        win_rate: float = 0.0,
        balance:  float = 0.0,
    ) -> None:
        """Call when a trade is resolved."""
        self._send(
            _fmt_trade_close(asset, result, profit, win_rate, balance),
            NotifLevel.TRADES,
        )

    # ── Risk / Auto-Regulation Events ─────────────────────────────────────────

    def adjustment(
        self,
        trigger:           str,
        new_confidence:    float,
        consecutive_losses: int,
    ) -> None:
        """Call when auto-regulation adjusts parameters."""
        self._send(
            _fmt_adjustment(trigger, new_confidence, consecutive_losses),
            NotifLevel.ALERTS,
        )

    def emergency(
        self,
        reason:            str,
        consecutive_losses: int,
        current_mode:      str = "CONSERVATIVE",
    ) -> None:
        """Call when emergency mode is activated."""
        self._send(
            _fmt_emergency(reason, consecutive_losses, current_mode),
            NotifLevel.ALERTS,
        )

    def emergency_cleared(self, wins: int) -> None:
        """Call when emergency mode is deactivated."""
        self._send(_fmt_emergency_cleared(wins), NotifLevel.ALERTS)

    # ── Bot Lifecycle Events ──────────────────────────────────────────────────

    def bot_event(
        self,
        event:   str,
        detail:  str   = "",
        balance: float = 0.0,
    ) -> None:
        """Call for bot lifecycle events: started, stopped, connected, etc."""
        level = (
            NotifLevel.CRITICAL
            if event.lower() == "error"
            else NotifLevel.ALERTS
        )
        self._send(_fmt_bot_event(event, detail, balance), level)

    # ── Periodic Reports ──────────────────────────────────────────────────────

    def daily_summary(
        self,
        wins:      int,
        losses:    int,
        profit:    float,
        balance:   float,
        top_asset: str = "N/A",
    ) -> None:
        """Call at end of trading day."""
        self._send(
            _fmt_daily_summary(wins, losses, profit, balance, top_asset),
            NotifLevel.ALERTS,
        )

    def heartbeat(self, cycle: int, balance: float, mode: str) -> None:
        """Periodic alive ping (ALL level only — no spam on TRADES/ALERTS)."""
        text = (
            f"💓 *HEARTBEAT* — Cycle {cycle}\n"
            f"Balance: `${balance:.2f}` | Mode: `{mode}` | `{_ts()}`"
        )
        self._send(text, NotifLevel.ALL)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Gracefully stop the background sender thread."""
        if self._sender:
            self._sender.stop()


# ── Singleton ─────────────────────────────────────────────────────────────────

_notifier_instance: Optional[TelegramNotifier] = None
_notifier_lock = threading.Lock()


def get_notifier(
    token:   Optional[str] = None,
    chat_id: Optional[str] = None,
    level:   Optional[NotifLevel] = None,
) -> TelegramNotifier:
    """
    Returns the global TelegramNotifier singleton.

    On first call, reads TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and
    TELEGRAM_LEVEL from the environment (loaded from .env by config_service).
    Subsequent calls return the same instance.
    """
    global _notifier_instance
    with _notifier_lock:
        if _notifier_instance is None:
            _notifier_instance = TelegramNotifier(
                token=token,
                chat_id=chat_id,
                level=level or NotifLevel.TRADES,
            )
    return _notifier_instance
