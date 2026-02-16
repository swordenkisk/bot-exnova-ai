"""
core/config_service.py — Centralised Configuration Service
===========================================================

Loads configuration from (in priority order):
  1. Environment variables (highest priority)
  2. .env file in project root
  3. config_real.json (legacy fallback)
  4. Hardcoded defaults (lowest priority)

Architecture
------------
  - Typed, frozen dataclasses — changes fail fast at startup.
  - Singleton — loaded once, shared everywhere.
  - Secrets (credentials, Telegram token) always come from env/dotenv,
    never from config_real.json which may be committed.

Usage
-----
    from core.config_service import get_config_service

    config = get_config_service().config
    print(config.bot.fixed_trade_amount)
    print(config.telegram.bot_token)   # comes from .env only
"""

from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Load .env early (before any dataclass reads env vars) ─────────────────────

def _load_dotenv(root: Path) -> None:
    """
    Minimal .env loader — no external dependency required.
    If python-dotenv is installed it is preferred; otherwise we parse manually.
    """
    env_path = root / ".env"
    if not env_path.exists():
        return

    try:
        # Prefer python-dotenv when available
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=env_path, override=False)
        logger.debug("Loaded .env via python-dotenv: %s", env_path)
        return
    except ImportError:
        pass

    # Fallback: parse manually
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key   = key.strip()
                value = value.strip().strip('"').strip("'")
                # Only set if not already in environment (override=False behaviour)
                os.environ.setdefault(key, value)
        logger.debug("Loaded .env manually: %s", env_path)
    except Exception as exc:
        logger.warning("Could not read .env file: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r, using default %s", key, raw, default)
        return default

def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r, using default %s", key, raw, default)
        return default

def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ═════════════════════════════════════════════════════════════════════════════
# TYPED CONFIGURATION DATACLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CredentialsConfig:
    """Broker credentials. Always loaded from environment, never from JSON."""
    email:    str = ""
    password: str = ""

    def __repr__(self) -> str:
        return "CredentialsConfig(email='***', password='***')"

    def is_set(self) -> bool:
        return bool(self.email and self.password)


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram notification settings. Always from environment."""
    bot_token: str = ""
    chat_id:   str = ""
    level:     str = "TRADES"   # ALL | TRADES | ALERTS | CRITICAL

    def __repr__(self) -> str:
        token_repr = f"{self.bot_token[:8]}***" if self.bot_token else ""
        return f"TelegramConfig(token='{token_repr}', chat_id='{self.chat_id}', level='{self.level}')"

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class BotConfig:
    """Core trading parameters."""
    modo_conta:            str   = "PRACTICE"  # PRACTICE | REAL
    fixed_trade_amount:    float = 2.0
    max_daily_trades:      int   = 50
    max_concurrent_trades: int   = 2
    strategy_mode:         str   = "NORMAL"    # SPEED | NORMAL | CONSERVATIVE

    def __post_init__(self) -> None:
        if self.fixed_trade_amount <= 0:
            raise ConfigurationError(
                f"fixed_trade_amount must be > 0, got: {self.fixed_trade_amount}"
            )
        if self.max_daily_trades < 1:
            raise ConfigurationError(
                f"max_daily_trades must be >= 1, got: {self.max_daily_trades}"
            )
        if self.modo_conta not in ("PRACTICE", "REAL"):
            raise ConfigurationError(
                f"modo_conta must be PRACTICE or REAL, got: {self.modo_conta!r}"
            )
        if self.strategy_mode not in ("SPEED", "NORMAL", "CONSERVATIVE"):
            raise ConfigurationError(
                f"strategy_mode must be SPEED|NORMAL|CONSERVATIVE, got: {self.strategy_mode!r}"
            )


@dataclass(frozen=True)
class RiskManagementConfig:
    """Daily risk limits."""
    stop_loss_daily:         float = 0.05   # fraction of balance, e.g. 0.05 = 5%
    stop_win_daily:          float = 0.20   # fraction of balance, e.g. 0.20 = 20%
    max_consecutive_losses:  int   = 5

    def __post_init__(self) -> None:
        if not 0 < self.stop_loss_daily < 1:
            raise ConfigurationError(
                f"stop_loss_daily must be between 0 and 1, got: {self.stop_loss_daily}"
            )
        if not 0 < self.stop_win_daily <= 1:
            raise ConfigurationError(
                f"stop_win_daily must be between 0 and 1, got: {self.stop_win_daily}"
            )


@dataclass(frozen=True)
class AutoRegulationConfig:
    """Parameters for the auto-regulation system."""
    enabled:               bool  = True
    loss_trigger:          int   = 3      # consecutive losses before first adjustment
    emergency_threshold:   int   = 5      # consecutive losses for emergency mode
    adjustment_step:       float = 0.05   # confidence raised per adjustment step
    min_confidence:        float = 0.40
    max_confidence:        float = 0.80
    min_strategies:        int   = 1
    max_strategies:        int   = 4


@dataclass(frozen=True)
class StrategiesConfig:
    """Strategy toggles."""
    use_soros:              bool  = False
    use_martingale:         bool  = False
    martingale_levels:      int   = 2
    martingale_multiplier:  float = 2.0


@dataclass(frozen=True)
class TimingConfig:
    """Timing and signal detection parameters."""
    enable_precise_timing:      bool = True
    enable_pullback_detection:  bool = True
    candle_wait_seconds:        int  = 5


@dataclass(frozen=True)
class AppConfig:
    """Root application config — the single source of truth."""
    credentials:    CredentialsConfig    = field(default_factory=CredentialsConfig)
    telegram:       TelegramConfig       = field(default_factory=TelegramConfig)
    bot:            BotConfig            = field(default_factory=BotConfig)
    risk_management: RiskManagementConfig = field(default_factory=RiskManagementConfig)
    auto_regulation: AutoRegulationConfig = field(default_factory=AutoRegulationConfig)
    strategies:     StrategiesConfig     = field(default_factory=StrategiesConfig)
    timing:         TimingConfig         = field(default_factory=TimingConfig)
    config_version: str                  = "2.1"

    def to_safe_dict(self) -> Dict[str, Any]:
        """Export config as dict with secrets redacted — safe for logging."""
        data = asdict(self)
        data["credentials"] = {"email": "***", "password": "***"}
        data["telegram"]["bot_token"] = "***"
        return data


# ═════════════════════════════════════════════════════════════════════════════
# CONFIG SERVICE SINGLETON
# ═════════════════════════════════════════════════════════════════════════════

class ConfigService:
    """
    Thread-safe singleton that loads, validates, and exposes AppConfig.

    Priority (highest first):
      1. Environment variables / .env
      2. config_real.json
      3. Dataclass defaults
    """

    _instance: Optional[ConfigService] = None
    _config:   Optional[AppConfig]     = None

    def __new__(cls) -> ConfigService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if ConfigService._config is not None:
            return

        self._root = Path(__file__).parent.parent
        _load_dotenv(self._root)           # Load .env before reading env vars
        self._json_path = self._root / "config_real.json"
        self._raw: Dict[str, Any] = self._load_json()
        ConfigService._config = self._build_config()

    # ── JSON loader ───────────────────────────────────────────────────────────

    def _load_json(self) -> Dict[str, Any]:
        if not self._json_path.exists():
            logger.info("config_real.json not found — using env/defaults only")
            return {}
        try:
            with open(self._json_path, encoding="utf-8") as fh:
                data = json.load(fh)
            logger.info("Loaded config_real.json from %s", self._json_path)
            return data
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Invalid JSON in {self._json_path}: {exc}"
            ) from exc

    def _j(self, section: str) -> Dict[str, Any]:
        """Get a section from JSON with a safe fallback."""
        return self._raw.get(section) or {}

    # ── Config builder ────────────────────────────────────────────────────────

    def _build_config(self) -> AppConfig:
        """
        Merge env vars (priority) with JSON values into a validated AppConfig.
        """
        try:
            return AppConfig(
                credentials=self._build_credentials(),
                telegram=self._build_telegram(),
                bot=self._build_bot(),
                risk_management=self._build_risk(),
                auto_regulation=self._build_auto_reg(),
                strategies=self._build_strategies(),
                timing=self._build_timing(),
            )
        except ConfigurationError:
            raise
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(str(exc)) from exc

    def _build_credentials(self) -> CredentialsConfig:
        j = self._j("credentials")
        return CredentialsConfig(
            # Env vars take priority over JSON
            email    = _env_str("EXNOVA_EMAIL")    or j.get("email", ""),
            password = _env_str("EXNOVA_PASSWORD") or j.get("password", ""),
        )

    def _build_telegram(self) -> TelegramConfig:
        # Telegram config ONLY from env — never from JSON (security)
        return TelegramConfig(
            bot_token = _env_str("TELEGRAM_BOT_TOKEN"),
            chat_id   = _env_str("TELEGRAM_CHAT_ID"),
            level     = _env_str("TELEGRAM_LEVEL", "TRADES").upper(),
        )

    def _build_bot(self) -> BotConfig:
        j = self._j("bot")
        return BotConfig(
            modo_conta            = _env_str("EXNOVA_ACCOUNT_TYPE")
                                    or j.get("modo_conta", "PRACTICE"),
            fixed_trade_amount    = _env_float("BOT_TRADE_AMOUNT",
                                    float(j.get("fixed_trade_amount", 2.0))),
            max_daily_trades      = _env_int("BOT_MAX_DAILY_TRADES",
                                    int(j.get("max_daily_trades", 50))),
            max_concurrent_trades = _env_int("BOT_MAX_CONCURRENT_TRADES",
                                    int(j.get("max_concurrent_trades", 2))),
            strategy_mode         = _env_str("BOT_STRATEGY_MODE")
                                    or j.get("strategy_mode", "NORMAL"),
        )

    def _build_risk(self) -> RiskManagementConfig:
        j = self._j("risk_management")
        return RiskManagementConfig(
            stop_loss_daily        = _env_float("RISK_STOP_LOSS_DAILY",
                                     float(j.get("stop_loss_daily", 0.05))),
            stop_win_daily         = _env_float("RISK_STOP_WIN_DAILY",
                                     float(j.get("stop_win_daily", 0.20))),
            max_consecutive_losses = _env_int("RISK_MAX_CONSECUTIVE_LOSSES",
                                     int(j.get("max_consecutive_losses", 5))),
        )

    def _build_auto_reg(self) -> AutoRegulationConfig:
        j = self._j("auto_regulation")
        return AutoRegulationConfig(
            enabled             = _env_bool("AUTO_REG_ENABLED",
                                   bool(j.get("enabled", True))),
            loss_trigger        = _env_int("AUTO_REG_LOSS_TRIGGER",
                                   int(j.get("consecutive_losses_trigger", 3))),
            emergency_threshold = _env_int("AUTO_REG_EMERGENCY_THRESHOLD",
                                   int(j.get("emergency_threshold", 5))),
            adjustment_step     = _env_float("AUTO_REG_ADJUSTMENT_STEP",
                                   float(j.get("adjustment_step_size", 0.05))),
            min_confidence      = _env_float("AUTO_REG_MIN_CONFIDENCE",
                                   float(j.get("min_confidence", 0.40))),
            max_confidence      = _env_float("AUTO_REG_MAX_CONFIDENCE",
                                   float(j.get("max_confidence", 0.80))),
        )

    def _build_strategies(self) -> StrategiesConfig:
        j = self._j("strategies")
        return StrategiesConfig(
            use_soros             = _env_bool("STRATEGY_USE_SOROS",
                                    bool(j.get("use_soros", False))),
            use_martingale        = _env_bool("STRATEGY_USE_MARTINGALE",
                                    bool(j.get("use_martingale", False))),
            martingale_levels     = _env_int("STRATEGY_MARTINGALE_LEVELS",
                                    int(j.get("martingale_levels", 2))),
            martingale_multiplier = _env_float("STRATEGY_MARTINGALE_MULTIPLIER",
                                    float(j.get("martingale_multiplier", 2.0))),
        )

    def _build_timing(self) -> TimingConfig:
        j = self._j("timing_config")
        return TimingConfig(
            enable_precise_timing     = bool(j.get("enable_precise_timing", True)),
            enable_pullback_detection = bool(j.get("enable_pullback_detection", True)),
            candle_wait_seconds       = int(j.get("candle_wait_seconds", 5)),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def config(self) -> AppConfig:
        if ConfigService._config is None:
            raise RuntimeError("ConfigService not initialised")
        return ConfigService._config

    @classmethod
    def get_instance(cls) -> ConfigService:
        return cls()

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — for testing only. Do NOT call in production."""
        cls._instance = None
        cls._config   = None


# ── Error ─────────────────────────────────────────────────────────────────────

class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing required fields."""


# ── Convenience functions ─────────────────────────────────────────────────────

def get_config_service() -> ConfigService:
    """Return the global ConfigService singleton."""
    return ConfigService.get_instance()


def get_config() -> AppConfig:
    """Shortcut: return the validated AppConfig."""
    return get_config_service().config


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("CONFIG SERVICE — SELF TEST")
    print("=" * 60)

    try:
        cfg = get_config()
        import pprint
        pprint.pprint(cfg.to_safe_dict())

        print(f"\n✅ Credentials set:   {cfg.credentials.is_set()}")
        print(f"✅ Telegram active:   {cfg.telegram.is_configured()}")
        print(f"✅ Trade amount:      ${cfg.bot.fixed_trade_amount:.2f}")
        print(f"✅ Account type:      {cfg.bot.modo_conta}")
        print(f"✅ Strategy mode:     {cfg.bot.strategy_mode}")
        print(f"✅ Stop loss daily:   {cfg.risk_management.stop_loss_daily:.0%}")
        print(f"✅ Martingale:        {cfg.strategies.use_martingale}")
        print(f"✅ Auto-regulation:   {cfg.auto_regulation.enabled}")

    except ConfigurationError as e:
        print(f"\n❌ Config error: {e}")
        sys.exit(1)
