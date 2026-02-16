"""
core/auto_regulation_system.py — Reactive Auto-Regulation System
=================================================================

Automatically adjusts bot parameters in real-time based on performance:

  - Responds immediately to losing streaks (adaptive confidence, strategy count)
  - Three-level escalation: Normal → Cautious → Emergency
  - Learns optimal settings per hour and per asset
  - Saves/restores snapshots of successful configurations
  - Sends Telegram alerts at every regulation event

Levels
------
  Level 0  Normal        All parameters at user-set values.
  Level 1  Cautious      consecutive_losses >= loss_trigger
             → raises min_confidence by adjustment_step
             → logs Telegram adjustment alert
  Level 2  Emergency     consecutive_losses >= emergency_threshold
             → forces CONSERVATIVE settings from modes_config
             → pauses low-confidence signals
             → sends Telegram emergency alert
  Recovery               2 consecutive wins while in emergency
             → restores last good snapshot
             → sends Telegram cleared alert
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled":                  True,
    "loss_trigger":             3,      # Cautious mode after N losses
    "emergency_threshold":      5,      # Emergency mode after N losses
    "adjustment_step":          0.05,   # Confidence step per adjustment
    "min_confidence":           0.40,
    "max_confidence":           0.80,
    "min_strategies":           1,
    "max_strategies":           4,
    "emergency_min_confidence": 0.70,   # Hard floor during emergency
    "emergency_min_strategies": 3,      # Min strategy consensus in emergency
    "recovery_wins_needed":     2,      # Consecutive wins to exit emergency
}


# ═════════════════════════════════════════════════════════════════════════════
# MAIN CLASS
# ═════════════════════════════════════════════════════════════════════════════

class AutoRegulationSystem:
    """
    Reactive parameter regulation for the Exnova trading bot.

    Attach to a bot instance and call record_trade() after every resolved trade.
    All adjustments happen synchronously in the calling thread; Telegram
    notifications are queued asynchronously so they never block trading.
    """

    def __init__(self, bot: Any, config: Optional[Dict[str, Any]] = None) -> None:
        self.bot    = bot
        self.config: Dict[str, Any] = {**_DEFAULT_CONFIG, **(config or {})}

        # ── State ─────────────────────────────────────────────────────────────
        self.consecutive_losses:  int  = 0
        self.consecutive_wins:    int  = 0
        self.emergency_mode:      bool = False
        self.cautious_mode:       bool = False
        self.adjustments_made:    int  = 0

        # ── Histories ─────────────────────────────────────────────────────────
        self.trade_history: deque[Dict[str, Any]] = deque(maxlen=200)

        # Performance stats keyed by hour (0-23) and by asset name
        self.performance_by_hour: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total": 0,
                     "best_confidence": 0.55, "best_strategies": 2}
        )
        self.performance_by_asset: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total": 0,
                     "best_confidence": 0.55, "best_strategies": 2}
        )

        # Configuration snapshots for rollback
        self.config_snapshots: deque[Dict[str, Any]] = deque(maxlen=10)

        # A/B tests
        self.ab_tests: Dict[str, Any] = {}

        # ── Telegram ──────────────────────────────────────────────────────────
        self._notifier = self._init_notifier()

        logger.info("🤖 AutoRegulationSystem initialised (loss_trigger=%d, emergency=%d)",
                    self.config["loss_trigger"], self.config["emergency_threshold"])

    # ── Notifier ──────────────────────────────────────────────────────────────

    def _init_notifier(self):
        """Lazily import TelegramNotifier to avoid circular imports."""
        try:
            from core.telegram_notifier import get_notifier
            return get_notifier()
        except Exception as exc:
            logger.warning("Telegram notifier unavailable: %s", exc)
            return None

    def _notify(self, method: str, *args, **kwargs) -> None:
        """Call a notifier method safely, never raising."""
        if self._notifier:
            try:
                getattr(self._notifier, method)(*args, **kwargs)
            except Exception as exc:
                logger.debug("Telegram notification error: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def record_trade(
        self,
        result:           bool,
        asset:            str,
        confidence:       float,
        strategies_used:  int,
        profit:           float = 0.0,
    ) -> None:
        """
        Record a resolved trade and react to the result.

        Parameters
        ----------
        result:          True = win, False = loss.
        asset:           Symbol (e.g. "EURUSD").
        confidence:      Signal confidence at time of entry.
        strategies_used: Number of strategies that agreed.
        profit:          Realised P&L in account currency.
        """
        if not self.config.get("enabled", True):
            return

        now  = datetime.now().astimezone()
        hour = now.hour

        entry: Dict[str, Any] = {
            "timestamp":  now.isoformat(),
            "result":     result,
            "asset":      asset,
            "confidence": confidence,
            "strategies": strategies_used,
            "profit":     profit,
            "hour":       hour,
        }
        self.trade_history.append(entry)

        # ── Update consecutive counters ────────────────────────────────────────
        if result:
            self.consecutive_wins  += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins   = 0

        # ── Update per-hour stats ──────────────────────────────────────────────
        h = self.performance_by_hour[hour]
        h["total"] += 1
        if result:
            h["wins"] += 1
            h["best_confidence"] = confidence
            h["best_strategies"] = strategies_used
        else:
            h["losses"] += 1

        # ── Update per-asset stats ─────────────────────────────────────────────
        a = self.performance_by_asset[asset]
        a["total"] += 1
        if result:
            a["wins"] += 1
            a["best_confidence"] = confidence
            a["best_strategies"] = strategies_used
        else:
            a["losses"] += 1

        # ── React ──────────────────────────────────────────────────────────────
        self._react(result, asset, hour)

        logger.info(
            "📊 Trade recorded: %s | %s | streak: %dW/%dL",
            asset,
            "WIN ✅" if result else "LOSS ❌",
            self.consecutive_wins,
            self.consecutive_losses,
        )

    # ── Reaction Logic ────────────────────────────────────────────────────────

    def _react(self, result: bool, asset: str, hour: int) -> None:
        """Core state machine that reacts to each trade result."""

        # ── Recovery from emergency ────────────────────────────────────────────
        if result and self.emergency_mode:
            if self.consecutive_wins >= self.config["recovery_wins_needed"]:
                self._deactivate_emergency()
            return

        # ── Reactions to losses ────────────────────────────────────────────────
        if not result:
            # Level 0 → save snapshot on first loss of a streak
            if self.consecutive_losses == 1:
                self._save_snapshot("before_streak")

            # Level 2 — Emergency
            if self.consecutive_losses >= self.config["emergency_threshold"]:
                if not self.emergency_mode:
                    self._activate_emergency()
                return

            # Level 1 — Cautious adjustment
            if self.consecutive_losses >= self.config["loss_trigger"]:
                self._apply_adjustment(asset, hour)

    # ── Level 1: Cautious Adjustment ──────────────────────────────────────────

    def _apply_adjustment(self, asset: str, hour: int) -> None:
        """
        Progressively raise the minimum confidence threshold to filter
        weaker signals during a losing streak.
        """
        self.adjustments_made += 1
        step = self.config["adjustment_step"]

        sm = self._strategy_manager()
        if sm is None:
            logger.warning("⚙️ Adjustment #%d: strategy_manager not accessible",
                           self.adjustments_made)
            return

        current = getattr(sm, "min_confidence_threshold", 0.50)
        new_confidence = min(
            current + step,
            self.config["max_confidence"]
        )

        # Also tighten minimum strategy consensus
        current_strats = getattr(sm, "min_strategies_for_signal", 1)
        new_strats = min(current_strats + 1, self.config["max_strategies"])

        # Apply
        sm.min_confidence_threshold = new_confidence
        sm.min_strategies_for_signal = new_strats
        self.cautious_mode = True

        logger.warning(
            "⚙️ ADJUSTMENT #%d: consecutive_losses=%d | confidence %.2f→%.2f | strategies %d→%d",
            self.adjustments_made, self.consecutive_losses,
            current, new_confidence, current_strats, new_strats,
        )

        self._notify(
            "adjustment",
            trigger=f"{self.consecutive_losses} consecutive losses",
            new_confidence=new_confidence,
            consecutive_losses=self.consecutive_losses,
        )

    # ── Level 2: Emergency Mode ───────────────────────────────────────────────

    def _activate_emergency(self) -> None:
        """
        Force strict trading parameters: high confidence + many strategy
        confirmations required. Pauses new trades until recovery.
        """
        self.emergency_mode = True
        self._save_snapshot("pre_emergency")

        em_conf  = self.config["emergency_min_confidence"]
        em_strats = self.config["emergency_min_strategies"]

        sm = self._strategy_manager()
        if sm is not None:
            sm.min_confidence_threshold = em_conf
            sm.min_strategies_for_signal = em_strats
            logger.critical(
                "🚨 EMERGENCY MODE: confidence→%.2f strategies→%d",
                em_conf, em_strats,
            )
        else:
            logger.critical("🚨 EMERGENCY MODE ACTIVE (strategy_manager unavailable)")

        self._persist_emergency_config(em_conf, em_strats)

        self._notify(
            "emergency",
            reason=f"{self.consecutive_losses} consecutive losses",
            consecutive_losses=self.consecutive_losses,
            current_mode="CONSERVATIVE",
        )

    def _deactivate_emergency(self) -> None:
        """Restore settings from the last successful snapshot and resume normal trading."""
        self.emergency_mode = False
        self.cautious_mode  = False
        restored = self._restore_snapshot()

        logger.info(
            "✅ EMERGENCY CLEARED after %d consecutive wins. Snapshot restored: %s",
            self.consecutive_wins,
            "yes" if restored else "no (no snapshot available)",
        )
        self._notify("emergency_cleared", wins=self.consecutive_wins)

    # ── Snapshot Management ───────────────────────────────────────────────────

    def _save_snapshot(self, label: str) -> None:
        """Capture current strategy_manager thresholds for rollback."""
        sm = self._strategy_manager()
        snapshot: Dict[str, Any] = {
            "timestamp":     datetime.now().isoformat(),
            "label":         label,
            "min_confidence": getattr(sm, "min_confidence_threshold", 0.55)
                              if sm else 0.55,
            "min_strategies": getattr(sm, "min_strategies_for_signal", 2)
                              if sm else 2,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins":   self.consecutive_wins,
        }
        self.config_snapshots.append(snapshot)
        logger.debug("💾 Config snapshot saved: %s", label)

    def _restore_snapshot(self) -> bool:
        """
        Restore the most recent snapshot tagged before a loss streak.
        Scans backwards to find a 'before_streak' snapshot.
        Returns True if a snapshot was found and applied.
        """
        # Find the most recent pre-streak snapshot
        target: Optional[Dict[str, Any]] = None
        for snap in reversed(self.config_snapshots):
            if snap.get("label") in ("before_streak", "before_losses"):
                target = snap
                break

        if target is None and self.config_snapshots:
            # Fall back to the oldest snapshot
            target = self.config_snapshots[0]

        if target is None:
            logger.warning("⚠️ No snapshot available to restore")
            return False

        sm = self._strategy_manager()
        if sm is not None:
            sm.min_confidence_threshold  = target["min_confidence"]
            sm.min_strategies_for_signal = target["min_strategies"]
            logger.info(
                "♻️ Restored: confidence=%.2f strategies=%d (from snapshot '%s' at %s)",
                target["min_confidence"], target["min_strategies"],
                target["label"], target["timestamp"],
            )
            return True

        return False

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_emergency_config(self, confidence: float, strategies: int) -> None:
        """Write emergency thresholds to config_real.json so they survive restarts."""
        try:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "config_real.json"
            )
            if not os.path.exists(config_path):
                return

            with open(config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            cr = data.setdefault("confluence_requirements", {})
            cr["min_confidence_threshold"]  = confidence
            cr["min_strategies_for_signal"] = strategies
            data["_emergency_mode"] = True
            data["_emergency_at"]   = datetime.now().isoformat()

            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

            logger.debug("💾 Emergency config persisted to config_real.json")
        except Exception as exc:
            logger.error("❌ Failed to persist emergency config: %s", exc)

    # ── Query Methods ─────────────────────────────────────────────────────────

    def get_performance_report(self) -> Dict[str, Any]:
        """Return a complete performance summary."""
        total = len(self.trade_history)
        if total == 0:
            return {"status": "no_data", "total_trades": 0}

        wins   = sum(1 for t in self.trade_history if t["result"])
        losses = total - wins
        profit = sum(t.get("profit", 0.0) for t in self.trade_history)

        sm = self._strategy_manager()
        return {
            "status":              "emergency" if self.emergency_mode
                                   else "cautious" if self.cautious_mode
                                   else "normal",
            "total_trades":        total,
            "wins":                wins,
            "losses":              losses,
            "win_rate":            round(wins / total * 100, 2),
            "consecutive_wins":    self.consecutive_wins,
            "consecutive_losses":  self.consecutive_losses,
            "adjustments_made":    self.adjustments_made,
            "total_profit":        round(profit, 2),
            "emergency_mode":      self.emergency_mode,
            "cautious_mode":       self.cautious_mode,
            "current_thresholds": {
                "min_confidence":  getattr(sm, "min_confidence_threshold", "N/A") if sm else "N/A",
                "min_strategies":  getattr(sm, "min_strategies_for_signal", "N/A") if sm else "N/A",
            },
        }

    def get_optimal_config_for_hour(self, hour: int) -> Dict[str, Any]:
        stats = self.performance_by_hour[hour]
        if stats["total"] < 5:
            return {"confidence": 0.55, "strategies": 2, "note": "insufficient_data"}
        wr = stats["wins"] / stats["total"]
        return {
            "confidence":   stats["best_confidence"],
            "strategies":   stats["best_strategies"],
            "win_rate":     round(wr, 4),
            "total_trades": stats["total"],
        }

    def get_optimal_config_for_asset(self, asset: str) -> Dict[str, Any]:
        stats = self.performance_by_asset[asset]
        if stats["total"] < 5:
            return {"confidence": 0.55, "strategies": 2, "note": "insufficient_data"}
        wr = stats["wins"] / stats["total"]
        return {
            "confidence":   stats["best_confidence"],
            "strategies":   stats["best_strategies"],
            "win_rate":     round(wr, 4),
            "total_trades": stats["total"],
        }

    def get_performance_by_hour(self) -> Dict[int, Dict[str, Any]]:
        return {
            h: {
                "win_rate":        round(s["wins"] / s["total"] * 100, 2),
                "total":           s["total"],
                "best_confidence": s["best_confidence"],
                "best_strategies": s["best_strategies"],
            }
            for h, s in self.performance_by_hour.items()
            if s["total"] > 0
        }

    def get_performance_by_asset(self) -> Dict[str, Dict[str, Any]]:
        return {
            asset: {
                "win_rate":        round(s["wins"] / s["total"] * 100, 2),
                "total":           s["total"],
                "best_confidence": s["best_confidence"],
                "best_strategies": s["best_strategies"],
            }
            for asset, s in self.performance_by_asset.items()
            if s["total"] > 0
        }

    # ── A/B Testing ───────────────────────────────────────────────────────────

    def start_ab_test(
        self, strategy_name: str, duration_minutes: int = 60
    ) -> str:
        test_id = (
            f"ab_{strategy_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.ab_tests[test_id] = {
            "strategy":         strategy_name,
            "start_time":       datetime.now().isoformat(),
            "duration_minutes": duration_minutes,
            "group_a":          {"trades": [], "wins": 0, "losses": 0},
            "group_b":          {"trades": [], "wins": 0, "losses": 0},
            "status":           "active",
        }
        logger.info("🧪 A/B test started: %s", test_id)
        return test_id

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _strategy_manager(self) -> Optional[Any]:
        """Safe accessor for bot.strategy_manager."""
        return getattr(self.bot, "strategy_manager", None)


# ═════════════════════════════════════════════════════════════════════════════
# INTEGRATION HELPER
# ═════════════════════════════════════════════════════════════════════════════

def integrate_auto_regulation(bot: Any) -> None:
    """
    Attach AutoRegulationSystem to an ExnovaBot instance.

    Loads auto_regulation config from config_real.json (or falls back to env),
    creates the system, and monkey-patches bot._record_trade_result so that
    every resolved trade automatically feeds the regulation loop.

    Args
    ----
    bot : ExnovaBot or compatible object with `strategy_manager` attribute.
    """
    # Load regulation config
    ar_config: Dict[str, Any] = {}
    try:
        # Prefer config_service (env + JSON merged)
        from core.config_service import get_config
        cfg = get_config()
        ar  = cfg.auto_regulation
        ar_config = {
            "enabled":               ar.enabled,
            "loss_trigger":          ar.loss_trigger,
            "emergency_threshold":   ar.emergency_threshold,
            "adjustment_step":       ar.adjustment_step,
            "min_confidence":        ar.min_confidence,
            "max_confidence":        ar.max_confidence,
        }
    except Exception as exc:
        logger.warning("Could not load auto_regulation from ConfigService: %s", exc)
        # Fallback: raw JSON
        try:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "config_real.json"
            )
            if os.path.exists(config_path):
                with open(config_path, encoding="utf-8") as fh:
                    ar_config = json.load(fh).get("auto_regulation", {})
        except Exception as exc2:
            logger.warning("Could not load auto_regulation from JSON: %s", exc2)

    bot.auto_regulation = AutoRegulationSystem(bot, ar_config)

    # Patch _record_trade_result
    original = getattr(bot, "_record_trade_result", None)

    def patched_record_trade(
        result: bool, asset: str,
        confidence: float = 0.50, strategies: int = 1, profit: float = 0.0
    ) -> None:
        bot.auto_regulation.record_trade(result, asset, confidence, strategies, profit)
        if original is not None:
            original(result, asset, confidence, strategies, profit)

    bot._record_trade_result = patched_record_trade

    # Expose A/B test launcher
    bot.start_ab_test = lambda strategy, duration=60: \
        bot.auto_regulation.start_ab_test(strategy, duration)

    logger.info("🤖 AutoRegulationSystem integrated into bot")
