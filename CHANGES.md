# Changes — Core Module Upgrade (50% → 93%)

## What Was Added / Fixed

### 1. `.env` Support (NEW)

Copy `.env.example` → `.env` and fill in your values.

```
EXNOVA_EMAIL=your@email.com
EXNOVA_PASSWORD=your_password
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Config priority (highest first):
1. Environment variables
2. `.env` file (auto-loaded at startup, no extra code needed)
3. `config_real.json` (legacy fallback)
4. Hardcoded defaults

Credentials and Telegram tokens **never** go into `config_real.json` — they
are secrets and belong in env only.

---

### 2. `core/telegram_notifier.py` (NEW)

Full Telegram notification system with:

| Method | Trigger | Level |
|---|---|---|
| `trade_opened()` | Every new trade | TRADES |
| `trade_closed()` | Win or loss result | TRADES |
| `adjustment()` | Auto-reg parameter change | ALERTS |
| `emergency()` | Emergency mode activated | ALERTS |
| `emergency_cleared()` | Recovery from emergency | ALERTS |
| `bot_event()` | Start / stop / reconnect / error | ALERTS |
| `daily_summary()` | End of session | ALERTS |
| `heartbeat()` | Periodic alive ping | ALL only |

All sends are **non-blocking** (background thread queue). The bot never
stalls waiting for Telegram. Failed sends are retried up to 3 times with
2s delay. If queue is full (>100 messages), the oldest message is dropped.

**Setup:**
```python
# Automatic — reads from env:
from core.telegram_notifier import get_notifier
notifier = get_notifier()
notifier.trade_opened("EURUSD", "CALL", 2.0, confidence=0.75)
```

---

### 3. `core/config_service.py` (REWRITTEN)

**Before:** Loaded only from `config_real.json`. No env var support.
No Telegram config at all.

**After:**
- Loads `.env` at import time (before any dataclass reads env)
- New `TelegramConfig` dataclass — `bot_token`, `chat_id`, `level`
- New `AutoRegulationConfig` dataclass — all auto-reg params typed & validated
- `CredentialsConfig` now reads `EXNOVA_EMAIL` / `EXNOVA_PASSWORD` from env first
- All `_build_*` methods merge env (priority) with JSON values
- `is_set()` and `is_configured()` helper methods
- `to_safe_dict()` redacts both credentials and Telegram token in logs

---

### 4. `core/auto_regulation_system.py` (FULLY REWRITTEN)

**Before:** The three core methods were completely stubbed out:
```python
def _activate_emergency_mode(self):
    # NOTE: Confidence overrides DISABLED  ← did nothing
    
def _adjust_parameters(self, asset, hour):
    # Log the event but don't override  ← did nothing
    
def _restore_from_snapshot(self):
    # NOTE: Snapshot restoration disabled  ← did nothing
```

**After — all three are fully implemented:**

| Method | What it does now |
|---|---|
| `_apply_adjustment()` | Raises `min_confidence_threshold` by `adjustment_step`, tightens `min_strategies_for_signal` by 1, sends Telegram ALERTS message |
| `_activate_emergency()` | Forces `min_confidence=0.70`, `min_strategies=3`, persists to `config_real.json`, sends Telegram EMERGENCY message |
| `_restore_snapshot()` | Scans backwards through snapshots for `before_streak` label, restores exact confidence+strategies, sends Telegram CLEARED message |

New 3-level state machine:
```
Normal  → (loss_trigger losses)    → Cautious
Cautious → (emergency_threshold)   → Emergency  
Emergency → (recovery_wins_needed) → Normal (snapshot restored)
```

New `_persist_emergency_config()` writes thresholds to disk so a restart
during emergency doesn't reset to permissive settings.

---

### 5. `core/bot_engine.py` (SIGNIFICANTLY IMPROVED)

**New: `CircuitBreaker`** class prevents rapid-fire reconnection loops:
- Opens after 5 consecutive failures
- Waits 120s cool-down before allowing a single test reconnection
- Closes after 2 consecutive successes

**New: Exponential back-off** for errors: 5s → 10s → 30s → 60s → 120s

**New: Telegram notifications** at every lifecycle event:
```
Bot started       → bot_event(started)
Bot stopped       → bot_event(stopped)
Reconnected       → bot_event(reconnected)
Trade opened      → trade_opened(...)
Fatal error       → bot_event(error, detail)
Periodic (60 cy)  → heartbeat(cycle, balance, mode)
```

**Improved `_scan_market()`:** calls `analyze_asset()` and `execute_trade()`
via proper method calls and fires `on_trade` callbacks with full trade data.

**Improved `_sync_balance()`:** isolated method called every 12 cycles.

**`stop()` method** for clean programmatic shutdown.

---

## Migration — 3 Steps

### Step 1: Install new dependency

```bash
pip install python-dotenv
```

### Step 2: Create `.env`

```bash
cp .env.example .env
# Edit .env with your real values
```

### Step 3: Get Telegram credentials

1. Message **@BotFather** on Telegram → `/newbot` → copy the token
2. Message **@userinfobot** on Telegram → copy your chat ID
3. Add both to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABCdef...
   TELEGRAM_CHAT_ID=987654321
   TELEGRAM_LEVEL=TRADES
   ```

That's it. On next bot start you'll receive a Telegram message:
```
▶️ BOT STARTED
━━━━━━━━━━━━━━━━━━━━
Info:     Mode: NORMAL | Account: PRACTICE
Balance:  $1000.00
Time:     14:23:01 UTC
```

---

## Files Delivered

| File | Status |
|---|---|
| `core/__init__.py` | Updated — registers all new modules |
| `core/config_service.py` | Rewritten — .env + Telegram + AutoReg config |
| `core/telegram_notifier.py` | **NEW** — full async Telegram system |
| `core/auto_regulation_system.py` | Rewritten — fully functional emergency mode |
| `core/bot_engine.py` | Improved — circuit breaker, reconnection, Telegram |
| `.env.example` | **NEW** — template for all env vars |
| `requirements.txt` | Updated — added python-dotenv |
