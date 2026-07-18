---
name: local-ai-engine-audit
description: Audit, review, and harden the LOCAL_AI_ENGINE Telegram bot (aiogram + local LLM + ccxt + ZigZag/A/D/Liquidity). Use when working in repo github.com/VVASILEVSS/LOCAL_AI_ENGINE.
category: crypto-trading
---

# Skill: LOCAL_AI_ENGINE Audit & Hardening

## When to use this skill

Use this skill when:
- User asks to audit, review, or evaluate the LOCAL_AI_ENGINE project
- User asks to harden, refactor, or fix issues in LOCAL_AI_ENGINE
- User asks to work on the Telegram bot in repo `VVASILEVSS/LOCAL_AI_ENGINE`
- User references the Wyckoff/ZigZag/A/D/Liquidity trading model

## Project identity

- **Repo:** `github.com/VVASILEVSS/LOCAL_AI_ENGINE`
- **Type:** Telegram bot for crypto technical analysis using local LLM
- **Stack:** Python 3.13, aiogram 3.x, ccxt 4.5.54, pandas, numpy, matplotlib, APScheduler
- **LLM:** LM Studio local, model `qwen2.5-vl-7b-instruct`, endpoint `http://localhost:1234/v1/chat/completions`
- **Pairs:** BTCUSDT, ETHUSDT, XAUTUSDT (spot); XAGUSDT (forced futures)
- **Timeframes:** 15m, 1h, 4h, 1D
- **Storage:** SQLite `forecasts.db` + JSON state files in `data/state/`

## Architecture map

```
main.py                      — entry, aiogram polling
core/config.py              — TOKEN, endpoint, system prompts (HARDCODED, needs env)
core/handlers.py (498)      — TG commands, inline keyboards
core/scheduler.py (434)     — APScheduler, auto-analysis loop
core/ollama_client.py (1753)— LLM client, JSON parser, risk rules (CRITICAL)
core/auto_chart.py (656)   — OHLCV charts, Fib, structural levels
core/liquidity_magnet.py (610) — DUPLICATE of core/liquidity_magnet/liquidity_magnet.py
core/state_tracker.py (455)— zone history (saved/broken/rebuilt/retest)
core/volume_filters.py (383)— A/D context, divergence, bias
core/data_provider.py (208)— OHLCV fetcher + CSV archive
core/db.py (104)            — SQLite forecasts + settings
core/utils.py (106)         — markets cache, symbol validation
core/zigzag/                — isolated ZigZag module (structural_zigzag, benchmark, compare)
```

## Known issues (priority order)

### P0 — Critical
1. **Duplicate liquidity_magnet**: `core/liquidity_magnet.py` == `core/liquidity_magnet/liquidity_magnet.py` (identical, 610 lines each). Import resolution ambiguity. Fix: delete the file, keep the package; or vice versa.
2. **requirements.txt is UTF-16**: `pip install` may fail on Linux/CI. Fix: `iconv -f UTF-16 -t UTF-8 && dos2unix`.
3. **config.py hardcoded**: `LOCAL_AI_ENDPOINT` and `MODEL_NAME` hardcoded, no env fallback. Fix: `os.getenv()`.
4. **main.py overwrites settings**: `set_setting('symbols', ...)` runs at every startup, overwriting user TG-menu settings. Fix: remove or guard with `if not get_setting(...)`.
5. **No signal hierarchy**: Wyckoff ТЗ P0.1 requires hierarchy — model can output `false_breakout` + `accumulation` + `impulse` simultaneously. Fix: priority list in `enforce_risk_rules`.

### P1 — Important
6. **No LLM retry/fallback**: `ollama_client.py` httpx call to localhost:1234 has no retry. If LM Studio is down, bot silently fails.
7. **liquidity_heatmap.py not integrated**: 366-line module exists but not called in scheduler.
8. **_pick_tp_levels ignores liquidity pools**: Only uses ZigZag levels for TP candidates.
9. **No state GC**: `data/state/` accumulates JSON files without cleanup.
10. **No pytest suite**: Only ad-hoc scripts. Need `tests/test_json_parser.py`, `tests/test_risk_rules.py`, `tests/test_db.py`.
11. **No CI**: No GitHub Actions.

### P2 — Nice-to-have
12. File logging + rotation
13. Graceful shutdown (scheduler.shutdown())
14. Type hints everywhere (mypy --strict)
15. Rename Cyrillic dirs: `tests/зиг заг/` → `tests/zigzag/`, `tests/ликвидации/` → `tests/liquidity/`
16. Dockerfile + docker-compose
17. Root README.md
18. Backtest v2 (TP/SL aware, not just price>pred_price)

## Key files to read first

When starting work on this project, read in this order:
1. `TZ/README.md` — technical specification (Wyckoff upgrade roadmap P0→P2)
2. `core/config.py` — configuration and system prompts
3. `core/ollama_client.py` lines 1-120 — `PRO_TA_SYSTEM_PROMPT` and `PRO_TA_USER_PROMPT` (the JSON schema)
4. `core/ollama_client.py` lines 200-420 — JSON parser and normalizer (most critical code)
5. `core/scheduler.py` — auto-analysis loop
6. `core/db.py` — data model
7. `core/zigzag/README.md` — ZigZag module overview

## Critical code paths

### LLM JSON parsing (ollama_client.py)
The `_parse_json_response` function (around line 200) is the **most critical code** in the project. It:
- Extracts JSON from LLM raw text (handles markdown fences, extra text)
- Fixes unicode quotes (`"`/`"`, `'`/`'`)
- Inserts missing commas between string values on newlines
- Removes trailing commas
- Normalizes numeric fields (`_safe_float`)
- Normalizes `tf_zones`, `key_zones`, `tf_span_map`, `confluence_levels`, `risk_management`
- Has fallback for `risk_management` structure (primary/alternative branches)

When modifying this function, ALWAYS test with real LLM outputs — local 7B models frequently break JSON.

### Risk rules enforcement (ollama_client.py)
`enforce_risk_rules` post-processes the LLM JSON to:
- Pick TP1/TP2/TP3 from candidate levels (ZigZag confluence)
- Ensure SL is on the correct side of entry
- Validate direction consistency

This is where the **signal hierarchy (P0 issue)** should be added.

### State tracker (state_tracker.py)
Uses `ZONE_STATUS_VALUES` = {saved, broken, rebuilt, false_breakout, retest, updated_inside_range, unknown}.
Files stored in `data/state/{symbol}_{timeframe}.json`.

## Git workflow for this repo

- Main branch: `main`
- Cleanup commit already done: `9f3cf17` (junk moved to `_junk/`, forecasts.db untracked, .gitignore expanded)
- Cherry-pick commit: `79a0d52` (4 useful files from copilot branch)
- All 3 `copilot/*` branches deleted
- Audit branch: `ai-review/agent-audit` (this skill's branch)

## Verification approach

No pytest suite exists. For verification use:
1. `python -m py_compile <file.py>` — syntax check
2. Ad-hoc scripts in `C:\Users\Asus-pc\AppData\Local\Temp\hermes-verify-*.py` — one-off assertions
3. Import test: `python -c "from core.ollama_client import _parse_json_response"` — checks imports resolve

## User preferences (from memory)

- User is owner of auto-service (STO) in Kazakhstan, develops this as a side crypto project
- Prefers Russian language for communication
- Prefers free/affordable models (GLM via Alibaba DashScope)
- Hands-on, iterative development style
- Wants high-signal responses without excessive fluff

## Pitfalls

1. **Don't merge copilot branches blindly** — they contain junk (forecasts.db, .zip, .pyc, .bak). Always cherry-pick specific files.
2. **Don't delete `_junk/`** — user wants files isolated, not physically deleted.
3. **Don't re-track `forecasts.db`** — it was deliberately untracked in commit 9f3cf17.
4. **Cyrillic folder names** (`tests/зиг заг/`, `tests/ликвидации/`) — may break on Windows CI.
5. **`start_bot.bat` hardcodes `D:\telega\LOCAL_AI_ENGINE`** — not portable.
6. **`core/backups/`** contains old module versions — don't edit, treat as archive.
7. **Two `_safe_float` functions exist** — in `ollama_client.py`, `liquidity_magnet.py`, `volume_filters.py`. Each slightly different. Don't assume they're interchangeable.

## Commands for common tasks

### Re-clone and setup
```bash
cd /c/Users/Asus-pc
gh repo clone VVASILEVSS/LOCAL_AI_ENGINE
cd LOCAL_AI_ENGINE
python -m venv venv
source venv/Scripts/activate  # Windows git-bash
pip install -r requirements.txt
# Create .env with TOKEN=... and MY_CHAT_ID=...
```

### Run bot
```bash
python main.py
# Requires LM Studio running on localhost:1234 with qwen2.5-vl-7b-instruct loaded
```

### Syntax check all Python files
```bash
find core -name "*.py" -exec python -m py_compile {} \;
```

### Inspect forecasts.db
```bash
sqlite3 forecasts.db "SELECT * FROM forecasts ORDER BY timestamp DESC LIMIT 10;"
sqlite3 forecasts.db "SELECT * FROM settings;"
```

### Check for duplicate logic
```bash
diff core/liquidity_magnet.py core/liquidity_magnet/liquidity_magnet.py
```

### Convert requirements.txt encoding
```bash
iconv -f UTF-16 -t UTF-8 requirements.txt > tmp && mv tmp requirements.txt
dos2unix requirements.txt  # or: sed -i 's/\r$//' requirements.txt
```
