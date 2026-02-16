# 🤖 Bot Exnova AI

> **Automated binary options trading bot with quantum-grade signal analysis, adaptive self-regulation, and real-time Telegram notifications.**

[![License: All Rights Reserved](https://img.shields.io/badge/License-All%20Rights%20Reserved-red.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Telegram](https://img.shields.io/badge/Notifications-Telegram-26A5E4?logo=telegram)](https://telegram.org)
[![Status](https://img.shields.io/badge/Status-Active%20Development-green)]()

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Telegram Notifications](#-telegram-notifications)
- [Auto-Regulation System](#-auto-regulation-system)
- [Trading Modes](#-trading-modes)
- [Risk Management](#-risk-management)
- [Project Structure](#-project-structure)
- [API Reference](#-api-reference)
- [Roadmap](#-roadmap)

---

## 🔍 Overview

Bot Exnova AI is a fully automated trading bot for the [Exnova](https://exnova.com) binary options platform. It combines multiple technical analysis strategies (ICT, VA-MOD, RSI, Bollinger Bands, and more) with a reactive **Auto-Regulation System** that adapts parameters in real time based on performance — without requiring manual intervention.

All significant events (trade opens, wins, losses, emergency mode, daily summaries) are pushed instantly to your **Telegram** account so you always know what the bot is doing, even when you're away from your desk.

---

## ✨ Features

### Trading Engine
- **Multi-strategy signal confluence** — a trade is only entered when multiple strategies agree
- **ICT (Inner Circle Trader) analysis** — Order Blocks, Fair Value Gaps, Liquidity Sweeps, BOS, OTE, Pullback detection
- **VA-MOD feed** — Deriv real-price integration via dual-feed architecture
- **Precise candle-entry timing** — waits for optimal entry point within the candle
- **Martingale & Soros** recovery strategies (configurable)

### Adaptive Intelligence
- **3-level auto-regulation** — Normal → Cautious → Emergency, with automatic recovery
- **Per-hour performance learning** — optimal confidence levels per hour of day
- **Per-asset performance learning** — tracks which assets perform best for you
- **Config snapshot & rollback** — restores best settings after a bad streak
- **A/B strategy testing** — compare strategy variants in live conditions

### Notifications
- **Real-time Telegram alerts** — trade opened, win, loss, emergency, daily summary
- **Non-blocking** — notifications never delay trade execution
- **4 notification levels** — ALL / TRADES / ALERTS / CRITICAL
- **Automatic retry** — failed messages retried 3× with backoff

### Reliability
- **Circuit breaker** — prevents rapid-fire reconnection loops
- **Exponential back-off** — 5s → 10s → 30s → 60s → 120s on errors
- **Graceful shutdown** — open trades are preserved on stop
- **Emergency persistence** — emergency-mode settings survive a restart

### Configuration
- **`.env` file support** — secrets never go in committed JSON
- **Environment variable priority** — override any setting without touching files
- **Typed & validated config** — errors caught at startup, not mid-trade

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Bot Exnova AI                       │
├──────────────┬──────────────────┬───────────────────────┤
│  BotEngine   │  AutoRegulation  │  TelegramNotifier     │
│  (main loop) │  (adaptive AI)   │  (async notifications)│
├──────────────┴──────────────────┴───────────────────────┤
│                  ConfigService                          │
│         .env → env vars → config_real.json             │
├─────────────────────────────────────────────────────────┤
│                  ExnovaBot (legacy)                     │
│   Strategy analysis · Trade execution · Result tracking │
├────────────┬──────────────────────────────────────────  │
│  ICT       │  VA-MOD  │  RSI  │  BB  │  Correlation    │
│  Strategy  │  Feed    │       │      │  Sentiment      │
└────────────┴──────────┴───────┴──────┴─────────────────-┘
```

### Core Modules

| Module | Role |
|---|---|
| `core/bot_engine.py` | Main trading loop, connection management, circuit breaker |
| `core/auto_regulation_system.py` | Adaptive parameter tuning based on win/loss streaks |
| `core/telegram_notifier.py` | Async Telegram notification queue |
| `core/config_service.py` | Typed config loader (env + .env + JSON) |
| `core/config_manager.py` | Legacy JSON config read/write helper |
| `core/bot_state.py` | Shared runtime state (balance, mode, stats) |
| `core/exnova_bot.py` | Broker connection, strategy execution, trade tracking |

---

## ⚡ Quick Start

### 1. Clone & install

```bash
git clone https://github.com/swordenkisk/bot-exnova-ai.git
cd bot-exnova-ai
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
EXNOVA_EMAIL=your@email.com
EXNOVA_PASSWORD=your_password
EXNOVA_ACCOUNT_TYPE=PRACTICE

TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=987654321
TELEGRAM_LEVEL=TRADES
```

### 3. Run

```bash
# Practice account (safe default)
python main.py

# Real account
EXNOVA_ACCOUNT_TYPE=REAL python main.py
```

### 4. Verify config (optional)

```bash
python core/config_service.py
```

Expected output:
```
✅ Credentials set:   True
✅ Telegram active:   True
✅ Trade amount:      $2.00
✅ Account type:      PRACTICE
✅ Strategy mode:     NORMAL
✅ Auto-regulation:   True
```

---

## ⚙️ Configuration

All settings can be controlled via **environment variables** (highest priority), the **`.env` file**, or `config_real.json` (lowest priority). Secrets (passwords, API tokens) should **only** be in `.env`.

### Broker

| Variable | Default | Description |
|---|---|---|
| `EXNOVA_EMAIL` | — | Your Exnova login email |
| `EXNOVA_PASSWORD` | — | Your Exnova password |
| `EXNOVA_ACCOUNT_TYPE` | `PRACTICE` | `PRACTICE` or `REAL` |

### Bot

| Variable | Default | Description |
|---|---|---|
| `BOT_TRADE_AMOUNT` | `2.0` | Trade size in account currency |
| `BOT_MAX_DAILY_TRADES` | `50` | Hard cap on trades per day |
| `BOT_MAX_CONCURRENT_TRADES` | `2` | Max open trades at once |
| `BOT_STRATEGY_MODE` | `NORMAL` | `SPEED` / `NORMAL` / `CONSERVATIVE` |

### Risk Management

| Variable | Default | Description |
|---|---|---|
| `RISK_STOP_LOSS_DAILY` | `0.05` | Stop trading after losing 5% of balance |
| `RISK_STOP_WIN_DAILY` | `0.20` | Stop trading after gaining 20% of balance |
| `RISK_MAX_CONSECUTIVE_LOSSES` | `5` | Trigger emergency mode |

### Auto-Regulation

| Variable | Default | Description |
|---|---|---|
| `AUTO_REG_ENABLED` | `true` | Enable/disable the regulation system |
| `AUTO_REG_LOSS_TRIGGER` | `3` | Consecutive losses before first adjustment |
| `AUTO_REG_EMERGENCY_THRESHOLD` | `5` | Consecutive losses for emergency mode |
| `AUTO_REG_MIN_CONFIDENCE` | `0.40` | Floor for confidence adjustments |
| `AUTO_REG_MAX_CONFIDENCE` | `0.80` | Ceiling for confidence adjustments |

---

## 📱 Telegram Notifications

### Setup (2 minutes)

1. Message **[@BotFather](https://t.me/BotFather)** → `/newbot` → copy the token
2. Message **[@userinfobot](https://t.me/userinfobot)** → copy your chat ID
3. Add to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNO...
   TELEGRAM_CHAT_ID=987654321
   TELEGRAM_LEVEL=TRADES
   ```

### Notification Levels

| Level | What you receive |
|---|---|
| `ALL` | Everything including periodic heartbeats |
| `TRADES` | Every trade open + close (win/loss) — **recommended** |
| `ALERTS` | Wins, losses, emergency mode, bot start/stop only |
| `CRITICAL` | Emergency mode and fatal errors only |

### Message Examples

**Trade opened:**
```
📈 TRADE OPENED
━━━━━━━━━━━━━━━━━━━━
Asset:       EURUSD
Direction:   CALL
Amount:      $2.00
Confidence:  75%
Mode:        NORMAL
Time:        14:23:01 UTC
```

**Trade closed (win):**
```
✅ TRADE CLOSED — WIN
━━━━━━━━━━━━━━━━━━━━
Asset:       EURUSD
P&L:         +$1.68
Session WR:  72.3%
Balance:     $1,052.40
Time:        14:24:30 UTC
```

**Emergency mode:**
```
🚨 EMERGENCY MODE ACTIVATED
━━━━━━━━━━━━━━━━━━━━
Reason:      5 consecutive losses
Con. Losses: 5
New Mode:    CONSERVATIVE
Time:        16:45:12 UTC

⚠️ Bot has paused trading. Review required.
```

**Daily summary:**
```
📊 DAILY SUMMARY
━━━━━━━━━━━━━━━━━━━━
Trades:      25 (W:18 / L:7)
Win Rate:    72.0%
Total P&L:   +$18.90
Balance:     $1,018.90
Best Asset:  EURUSD
Date:        2025-02-16
```

---

## 🤖 Auto-Regulation System

The Auto-Regulation System monitors every trade result and automatically adjusts parameters to protect your capital during bad streaks.

### State Machine

```
         ┌─────────────────────────────────────────┐
         │              NORMAL MODE                │
         │  Default confidence & strategy settings │
         └──────────────┬──────────────────────────┘
                        │ N consecutive losses
                        │ (AUTO_REG_LOSS_TRIGGER, default: 3)
                        ▼
         ┌─────────────────────────────────────────┐
         │             CAUTIOUS MODE               │
         │  Confidence ↑ by step (default +5%)     │
         │  Min strategies required ↑ by 1         │
         │  Telegram: ⚙️ ADJUSTMENT alert           │
         └──────────────┬──────────────────────────┘
                        │ N consecutive losses
                        │ (AUTO_REG_EMERGENCY_THRESHOLD, default: 5)
                        ▼
         ┌─────────────────────────────────────────┐
         │            EMERGENCY MODE               │
         │  Confidence forced to 0.70              │
         │  Strategies required: 3                 │
         │  Settings persisted to disk             │
         │  Telegram: 🚨 EMERGENCY alert           │
         └──────────────┬──────────────────────────┘
                        │ 2 consecutive wins
                        │ (recovery_wins_needed)
                        ▼
         ┌─────────────────────────────────────────┐
         │      RECOVERY — Snapshot Restored       │
         │  Pre-streak settings reloaded           │
         │  Telegram: ✅ CLEARED alert              │
         └─────────────────────────────────────────┘
```

### Performance Learning

After accumulating 5+ trades per hour/asset, the system builds an optimal profile:

```python
# Get best settings for the current hour
config = bot.auto_regulation.get_optimal_config_for_hour(14)
# → {"confidence": 0.68, "strategies": 3, "win_rate": 0.74, "total_trades": 22}

# Get best settings for a specific asset
config = bot.auto_regulation.get_optimal_config_for_asset("EURUSD")
# → {"confidence": 0.72, "strategies": 2, "win_rate": 0.71, "total_trades": 41}
```

---

## 📊 Trading Modes

| Mode | Min Confidence | Description |
|---|---|---|
| `SPEED` | Low | More signals, faster execution, higher risk |
| `NORMAL` | Medium | Balanced signal quality and frequency — **default** |
| `CONSERVATIVE` | High | Fewer but higher-quality signals, lower risk |

Switch mode without restarting:

```bash
# Via environment variable (takes effect on next run)
BOT_STRATEGY_MODE=CONSERVATIVE python main.py

# Via dashboard API
curl -X POST http://localhost:5000/api/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "CONSERVATIVE"}'
```

---

## 🛡️ Risk Management

The bot enforces three layers of risk protection:

1. **Daily stop-loss** (`RISK_STOP_LOSS_DAILY`) — trading halts if losses exceed X% of starting balance
2. **Daily take-profit** (`RISK_STOP_WIN_DAILY`) — trading halts after reaching profit target  
3. **Auto-regulation** — real-time parameter tightening during losing streaks

All three are configurable per the [Configuration](#️-configuration) table above.

---

## 📁 Project Structure

```
bot-exnova-ai/
├── core/
│   ├── __init__.py              # Package exports
│   ├── auto_regulation_system.py # Adaptive parameter tuning
│   ├── bot_engine.py            # Main trading loop + circuit breaker
│   ├── bot_state.py             # Shared runtime state
│   ├── config_manager.py        # JSON config read/write
│   ├── config_service.py        # Typed config (.env + JSON + defaults)
│   ├── exnova_bot.py            # Broker connection + trade execution
│   ├── logger.py                # Logging setup
│   └── telegram_notifier.py     # Async Telegram notifications
├── strategies/
│   ├── ict_strategy.py          # ICT: OB, FVG, Liquidity, BOS, OTE
│   └── vamod_strategy.py        # VA-MOD: Deriv price feed integration
├── utils/
│   └── iq_style_dashboard.py    # Optional web dashboard
├── data/
│   └── trade_history.json       # Persistent trade log
├── docs/
│   └── ...                      # Additional documentation
├── .env.example                 # ← Copy to .env and fill in secrets
├── .env                         # Your secrets (never commit this)
├── .gitignore
├── config_real.json             # Non-secret bot settings
├── requirements.txt
├── main.py                      # Entry point
├── CHANGES.md                   # Upgrade notes
└── README.md                    # This file
```

---

## 📖 API Reference

### `TelegramNotifier`

```python
from core.telegram_notifier import get_notifier, NotifLevel

notifier = get_notifier()

# Trade events
notifier.trade_opened("EURUSD", "CALL", 2.0, confidence=0.75, mode="NORMAL")
notifier.trade_closed("EURUSD", "WIN", profit=1.68, win_rate=72.0, balance=1050.0)

# Risk events
notifier.adjustment("3 consecutive losses", new_confidence=0.65, consecutive_losses=3)
notifier.emergency("5 consecutive losses", consecutive_losses=5, current_mode="CONSERVATIVE")
notifier.emergency_cleared(wins=2)

# Lifecycle
notifier.bot_event("started", detail="Mode: NORMAL", balance=1000.0)
notifier.bot_event("stopped", detail="Daily target reached")
notifier.daily_summary(wins=18, losses=7, profit=12.5, balance=1012.5, top_asset="EURUSD")
```

### `AutoRegulationSystem`

```python
from core.auto_regulation_system import integrate_auto_regulation

# Attach to bot
integrate_auto_regulation(bot)

# Record a trade result
bot._record_trade_result(result=True, asset="EURUSD",
                         confidence=0.73, strategies=3, profit=1.68)

# Get performance report
report = bot.auto_regulation.get_performance_report()
# → {"status": "normal", "win_rate": 72.0, "total_trades": 25, ...}

# Get per-hour optimal config
opt = bot.auto_regulation.get_optimal_config_for_hour(14)

# Start an A/B test
test_id = bot.start_ab_test("ICT_v2", duration=60)
```

### `ConfigService`

```python
from core.config_service import get_config

config = get_config()

# Access all settings with full IntelliSense
print(config.bot.fixed_trade_amount)      # 2.0
print(config.bot.strategy_mode)           # "NORMAL"
print(config.telegram.is_configured())    # True / False
print(config.risk_management.stop_loss_daily)  # 0.05

# Safe export for logging (secrets redacted)
import pprint
pprint.pprint(config.to_safe_dict())
```

---

## 🛣️ Roadmap

### v1.1 (Current)
- [x] `.env` file support with priority loading
- [x] Full Telegram notification system
- [x] Working auto-regulation (emergency mode, snapshots, recovery)
- [x] Circuit breaker + exponential back-off reconnection
- [x] Typed configuration with validation

### v1.2 (Next)
- [ ] Web dashboard redesign (React frontend)
- [ ] SQLite trade history (replace flat JSON)
- [ ] Email notification fallback
- [ ] Backtesting mode against historical data
- [ ] Docker compose deployment

### v2.0
- [ ] Async bot engine (asyncio)
- [ ] Multi-broker support
- [ ] Mobile app for remote control
- [ ] AI-based signal scoring (ML model trained on your own history)

---

## ⚠️ Disclaimer

Binary options trading involves significant financial risk. This bot is provided for educational and experimental purposes. Past performance does not guarantee future results.

- Start with a **PRACTICE account** and observe behavior before going live
- Never risk money you cannot afford to lose
- The auto-regulation system reduces risk but cannot eliminate it
- Monitor the bot regularly — do not leave it fully unattended

---

## 📜 License

**© 2025 [swordenkisk](https://github.com/swordenkisk) — All Rights Reserved**

Unauthorized copying, distribution, or commercial use of this software is strictly prohibited without express written permission.

---

*Built with ❤️ for serious traders who want automation without blind trust.*
