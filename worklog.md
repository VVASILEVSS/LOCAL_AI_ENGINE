# LOCAL_AI_ENGINE — Work Log

---
Task ID: 7
Agent: Super Z
Task: Fix zone calculation — match user's manual markup (structure-based, senior TF priority)

Work Log:
- Analyzed root cause: `split_structure()` used `_ZONE_LOOKBACK=10` for prev_high (only last 10 pivots) instead of absolute max/min
- Found that Hermes had already fixed 3/4 parts: hard parent constraint (no 10% soft clamp), chain_broken removal, narrative text update
- Changed `split_structure()` prev_structure to use ABSOLUTE max/min of ALL pivots before BOS (removed _ZONE_LOOKBACK)
- Updated `analyze_topdown()` docstring to reflect new behavior
- Verified with unit test: prev.high=97932 (absolute max), prev.low=56804 (absolute min)
- Verified full top-down chain: all TFs share D1 low=56704, cascading high narrowing
- Confirmed scheduler already disabled in main.py (line 44 commented out)
- Confirmed fallback in ollama_client.py already uses full zone (upper/lower from zone_high/zone_low)

Stage Summary:
- Zone calculation now matches user's manual markup approach:
  - D1 zone = full structural range (absolute extremes of all pivots)
  - All child TFs inherit D1's low as hard floor (shared lows)
  - Senior TF high = ceiling for all child TFs (cascading narrowing)
  - No chain-breaking — structure is treated as unified entity
- File changed: core/structure.py (lines 259-268, 493-498)
- Scheduler: already disabled in main.py (no change needed)
- Ready for commit + Hermes review

---
Task ID: 1
Agent: Super Z
Task: AUTO_SIGNAL_ONLY — selective auto-notification mode

Work Log:
- Added AUTO_SIGNAL_ONLY config flag (default true) to core/config.py
- Added ACTIONABLE_SIGNALS = ("aggressive_breakout", "retest", "reversal")
- Modified scheduler.py: send_to_tg conditional on signal status in auto cycle
- Manual /analyse in handlers.py NOT affected (always sends)
- save_signal_log() called BEFORE filter (all signals logged to backtest)
- Resolved rebase conflict with Hermes f29d74f (his get_setting("auto_mode") replaced)

Stage Summary:
- Commit c0f8228 pushed. AUTO_SIGNAL_ONLY works: auto-cycle silent on no_signal/accumulation/false_breakout, sends on actionable signals.

---
Task ID: 2
Agent: Super Z
Task: Split LLM config — main bot local, dashboard cloud

Work Log:
- Added DASHBOARD_LLM_API_KEY, DASHBOARD_LLM_BASE_URL, DASHBOARD_MODEL_NAME to config.py
- Refactored ollama_service.py: generate() accepts optional api_key/base_url override
- _resolve_base_url(), _build_headers(), _endpoint_url() accept override params
- Added dashboard_info() for diagnostics
- .env.example updated with DASHBOARD_LLM_* section
- Backward compatible — existing calls without override params work unchanged

Stage Summary:
- Commit 530e207 pushed. Main bot stays local (LLM_API_KEY empty), dashboard passes override to generate() for cloud Alibaba GLM.

---
Task ID: 3
Agent: Hermes (commit 2590f2e)
Task: Dashboard /scan with cloud LLM + full context

Work Log:
- run_hourly_analysis accepts llm_api_key, llm_base_url, llm_model, symbol_filter overrides
- AUTO_SIGNAL_ONLY disabled via sched_mod for manual /scan
- load_dotenv() for DASHBOARD_LLM_* env vars
- /scanBTC (without space) works
- Fixed: TIMEFRAMES import, await fetch_and_plot, analyze_multi_images signature, getUpdates conflict

Stage Summary:
- Dashboard bot @my_hermes_lokal_ai_bot works on Alibaba GLM cloud.
- Main bot @KXROBObot stays on local LM Studio.
- Cloud quality >> local (all fields populated vs Unknown).

---
Task ID: PENDING-1 (Bug)
Agent: Super Z (assigned)
Task: Баг 1 — Матрешка зон нарушена

Problems:
1. H1 lower (61297) < H4 lower (61306) — nested zones violated
2. D1 upper = 82850 — LLM takes 100-candle historical high, not current zone
3. "1D/4H" hybrid key from tf_context string leaked into tf_zones

Solution:
1. Validate nesting in enforce_risk_rules: if H1 lower < H4 lower, expand H4 lower
2. Cap D1 zone to realistic range (±10% from current price)
3. Filter hybrid keys (containing "/") in format_json_for_tg

---
Task ID: PENDING-2 (Bug)
Agent: Super Z (assigned)
Task: Баг 2 — "1D/4H" в tf_zones

tf_context string from metrics leaks into tf_zones as "1D/4H" key.
Solution: filter keys containing "/" in format_json_for_tg.

---
Task ID: PENDING-3 (Feature)
Agent: Hermes (assigned, tomorrow)
Task: Дашборд-бот: кнопки при /start + автоскан

Requirements:
- /start → inline keyboard with: Анализ BTC, ETH, XAUT, Настройки, Статистика, Авто-режим
- "▶ Автоскан" button — cycle analysis all symbols via cloud every 30 min
- Architecture: @my_hermes_lokal_ai_bot (cloud) + @KXROBObot (local) running together---
Task ID: 1
Agent: Super Z (main)
Task: FEELS-inspired improvements + merge zones-sticking + read Hermes letters

Work Log:
- Read Hermes letters: 2026-07-13 (9-point priority list) and 2026-07-14 (zones sticking after D1 cap removed)
- Checked branches: main=38923ad, fix/zones-sticking=b26c43d (1 commit ahead)
- Merged _enforce_zone_uniqueness from fix/zones-sticking into main (kept LM prompt context, fixed РАЗНЫММ typo)
- Implemented log-distance for confluence_levels: |ln(level/price)| + proximity_score (inverted-U curve)
- Added source tracking in tf_zones (llm/vp/zigzag/liquidity_magnet)
- Updated TG format to show log_distance and proximity_score per confluence level
- Pushed as 8436c83 after rebase over d80dc50 (Hermes archive cleanup commit)

Stage Summary:
- Commit: 8436c83 "[LOCAL_AI_ENGINE] feat: FEELS-inspired improvements"
- _enforce_zone_uniqueness: D1±2.5%, H4±1.5%, H1±0.75% expansion when zones stick
- Log-distance: symmetric proximity scoring, -30% and +30% get same penalty
- Source tracking: tf_zones now carry "source" field for dashboard debugging
- Branch fix/zones-sticking can be deleted (merged manually)

---
Task ID: 1.2
Agent: Super Z
Task: Проверить analyze_topdown() на синтетических данных (Binance API заблокирован с сервера)

Work Log:
- Написал test_topdown_abs_extremes.py: 4 ТФ (D1→H4→H1→15M), синтетические BTC-подобные данные
- Все 4 ТФ: prev_structure берёт АБСОЛЮТНЫЙ max/min (✅), иерархия зон корректная (✅)
- 1H и 15M разделяют shared floor с 4H — parent constraint работает
- Коммит c0c7c8e уже на origin/feature/top-down-structure

Stage Summary:
- ✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ на синтетике
- Результат: скрипт в /home/z/my-project/scripts/test_topdown_abs_extremes.py

---
## ПРАВИЛА РАБОТЫ (обязательно соблюдать)

1. **НЕ СПАМИТЬ** — не писать письма Гермесу, не коммитить, не пушить без явной просьбы пользователя. Один результат — одно письмо/коммит. Не дробить на 5 мелких.

2. **Веточная дисциплина** — каждая ветка мержится только в main, между собой НЕ мержим.

3. **Письма только в exchange/** — формат: `YYYY-MM-DD_от-кого-тема.md`

4. **Старый код не трогать** — на fix/zones-sticking работаем только с auto_chart.py/ollama_client.py/handlers.py/main.py
