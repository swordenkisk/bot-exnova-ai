"""
Core package — Bot Exnova AI
=============================

Public API
----------
    from core import logger, BotLogger
    from core import ConfigManager, get_config_service, get_config
    from core import TelegramNotifier, get_notifier
    from core import AutoRegulationSystem, integrate_auto_regulation
    from core import BotEngine

Loading order
-------------
  1. Logger      — always available, no dependencies
  2. Config      — loads .env + config_real.json, validates, builds AppConfig
  3. Telegram    — reads TELEGRAM_* from env (populated by config step)
  4. Auto-reg    — reactive parameter adjustment + Telegram hooks
  5. Bot engine  — main trading loop
  6. Economic    — optional (yfinance may have issues on Python 3.14+)
"""

# ── 1. Logger ─────────────────────────────────────────────────────────────────
from .logger import BotLogger, logger  # always available

# ── 2. Config ─────────────────────────────────────────────────────────────────
try:
    from .config_manager import ConfigManager
except ImportError:
    ConfigManager = None  # type: ignore[assignment,misc]

try:
    from .config_service import (
        ConfigService,
        AppConfig,
        get_config_service,
        get_config,
        ConfigurationError,
    )
except ImportError as _e:
    logger.warning("config_service unavailable: %s", _e)
    ConfigService     = None  # type: ignore[assignment,misc]
    AppConfig         = None  # type: ignore[assignment,misc]
    get_config_service = None  # type: ignore[assignment]
    get_config        = None  # type: ignore[assignment]
    ConfigurationError = Exception  # type: ignore[assignment,misc]

# ── 3. Telegram ───────────────────────────────────────────────────────────────
try:
    from .telegram_notifier import TelegramNotifier, get_notifier, NotifLevel
except ImportError as _e:
    logger.warning("telegram_notifier unavailable: %s", _e)
    TelegramNotifier = None  # type: ignore[assignment,misc]
    get_notifier     = None  # type: ignore[assignment]
    NotifLevel       = None  # type: ignore[assignment,misc]

# ── 4. Auto-regulation ────────────────────────────────────────────────────────
try:
    from .auto_regulation_system import (
        AutoRegulationSystem,
        integrate_auto_regulation,
    )
except ImportError as _e:
    logger.warning("auto_regulation_system unavailable: %s", _e)
    AutoRegulationSystem     = None  # type: ignore[assignment,misc]
    integrate_auto_regulation = None  # type: ignore[assignment]

# ── 5. Bot engine ─────────────────────────────────────────────────────────────
try:
    from .bot_engine import BotEngine
except ImportError as _e:
    logger.warning("bot_engine unavailable: %s", _e)
    BotEngine = None  # type: ignore[assignment,misc]

# ── 6. Economic modules (optional) ───────────────────────────────────────────
try:
    from .economic import (
        InvestingEconomicCalendar,
        YahooEconomicData,
        EconomicNewsIndicator,
    )
except ImportError as _e:
    logger.debug("Economic modules unavailable: %s", _e)
    InvestingEconomicCalendar = None  # type: ignore[assignment,misc]
    YahooEconomicData         = None  # type: ignore[assignment,misc]
    EconomicNewsIndicator     = None  # type: ignore[assignment,misc]


__all__ = [
    # Logger
    "BotLogger",
    "logger",
    # Config
    "ConfigManager",
    "ConfigService",
    "AppConfig",
    "get_config_service",
    "get_config",
    "ConfigurationError",
    # Telegram
    "TelegramNotifier",
    "get_notifier",
    "NotifLevel",
    # Auto-regulation
    "AutoRegulationSystem",
    "integrate_auto_regulation",
    # Engine
    "BotEngine",
    # Economic (optional)
    "InvestingEconomicCalendar",
    "YahooEconomicData",
    "EconomicNewsIndicator",
]
