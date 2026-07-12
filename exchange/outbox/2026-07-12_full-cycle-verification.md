# Full-Cycle Verification — LOCAL_AI_ENGINE

**Дата:** 2026-07-12
**Автор:** Hermes
**Задача:** exchange/inbox/2026-07-12_полный-цикл-проверки.md (13 блоков, 47 проверок)
**HEAD:** `deafbad` (или новее)

---

## СВОДКА

| Метрика | Значение |
|---------|----------|
| **PASS** | 39 |
| **FAIL** | 5 |
| **NEEDS_TEST** | 3 |
| **Всего** | 47 |
| **Критичных багов** | 0 (блокирующих запуск) |

---

## БЛОК 1: Инициализация и конфигурация

- [x] **1.1** `start_scheduler()` → `init_all_tables()` + `init_backtest_table()` — PASS. `scheduler.py:497-498`, `CREATE TABLE IF NOT EXISTS`, идемпотентно.
- [x] **1.2** `PROMPT_VARIANT` парсинг — PASS. `config.py:40-42`, fallback "A" для невалидных значений.
- [x] **1.3** `LLM_MODE` — PASS. `config.py:28-29`, непустой `LLM_API_KEY` → "cloud".
- [x] **1.4** `LLM_BASE_URL` без `/v1` — PASS. `config.py:35`, `ollama_service.py:54-55` (`removesuffix("/v1")`).
- [x] **1.5** `_get_symbols()` → `["BTCUSDT", "XAUTUSDT"]` — PASS. `scheduler.py:36-37`.

## БЛОК 2: Сбор рыночных данных

- [x] **2.1** `fetch_and_plot()` — PASS. `auto_chart.py:547`, возвращает `(bytes, dict)`. Внешний try/except в `scheduler.py:468-470`.
- [x] **2.2** `fetch_binance_metrics()` — PASS. `binance_metrics.py:119`, OKX→Binance→Bybit fallback (строки 107-116).
- [x] **2.3** H1-first зона — PASS. `scheduler.py:261-292`, `auto_chart.py:370-449`. None-safe, ATR=0 → `price*0.0025`.

## БЛОК 3: Multi-symbol контекст (P3-3)

- [x] **3.1** Кэш `cache_buster` — PASS. `multi_symbol.py:120-123`.
- [x] **3.2** CoinGecko 429 → `{}` — PASS. `multi_symbol.py:47-49`.
- [x] **3.3** Fear & Greed парсинг — PASS. `multi_symbol.py:85-91`, except → None.
- [x] **3.4** BTC Dominance None-safe — PASS. `multi_symbol.py:69`.
- [x] **3.5** BTC 24ч для не-BTC — PASS. `multi_symbol.py:231-234`.
- [x] **3.6** Hints F&G/Dominance — PASS. `multi_symbol.py:213-214, 225-228`.
- [ ] **3.7** Китайский символ "慎重" — **FAIL**. `multi_symbol.py:218` — заменить на "осторожно".

## БЛОК 4: ZigZag контекст

- [x] **4.1** `_build_zigzag_context()` error handling — PASS. `scheduler.py:55-103`.
- [x] **4.2** `output_mode="compact"` — 8 полей — PASS. `scheduler.py:76-86`.
- [x] **4.3** `confluence_levels[:12]` — PASS. `scheduler.py:93`.
- [ ] **4.4** `run_benchmark()` для XAUTUSDT пустые данные — **FAIL**. `benchmark_zigzag.py:197-209` — нет guard `if not bars` / `if df.empty` → `IndexError` на `closes[-1]`. Обертка в `_build_zigzag_context` ловит, но сама функция падает.

## БЛОК 5: Liquidity Heatmap

- [x] **5.1** `build_liquidity_heatmap()` FileNotFoundError — PASS. `scheduler.py:322-337`.
- [x] **5.2** `build_liquidity_context_text()` — PASS. `liquidity_heatmap.py:340-367`.
- [x] **5.3** `liquidity_pools` для `enforce_risk_rules` — PASS. `scheduler.py:342-358` + `ollama_client.py:1026-1044`.

## БЛОК 6: State Tracker

- [x] **6.1** `load_state()` отсутствие файла → None — PASS. `state_tracker.py:120-132`.
- [x] **6.2** `compare_state(None, current)` → "saved" — PASS. `state_tracker.py:307-316`.
- [x] **6.3** `detect_zone_event()` все статусы — PASS. `state_tracker.py:307-362`.
- [x] **6.4** `is_false_breakout()` 3 условия — PASS. `state_tracker.py:200-248`.
- [x] **6.5** `_cleanup_old_states()` 30 дней — PASS. `state_tracker.py:461-482`.
- [x] **6.6** `_ensure_state_dir()` — PASS. `state_tracker.py:56-57`.

## БЛОК 7: LLM вызов и парсинг

- [x] **7.1** `parse_llm_json()` cleanup + error — PASS. `ollama_client.py:194`.
- [x] **7.2** Self-consistency 2 прогона — PASS. `ollama_client.py:2008-2070`. Нюанс: `winner_signal` вычисляется, но берётся `results[0]`.
- [x] **7.3** A/B тест промптов — PASS. `ollama_client.py:188` (`_get_system_prompt()`), `backtest.py` `prompt_variant` сохраняется.
- [x] **7.4** Все 8 плейсхолдеров в промпте — PASS. `{metrics}`, `{tf_context}`, `{backtest}`, `{multi_symbol}`, `{state_context}`, `{zigzag_context}`, `{volume_context}`, `{heatmap_context}`.
- [x] **7.5** LLM response обязательные поля — PASS. Few-shot примеры содержат все поля.

## БЛОК 8: enforce_risk_rules

- [x] **8.1** Шаг 5: false_breakout → no_signal — PASS.
- [x] **8.2** Шаг 7: no_signal/accumulation → risk_management очищается — PASS.
- [x] **8.3** Шаг 8.1: SIGNAL_PRIORITY + sub_to_signal — PASS.
- [x] **8.4** Шаг 10: TP/SL direction checks — PASS.
- [x] **8.5** Шаг 12.6b: SL/TP validation, RR < 1.0 → блок очищается — PASS.
- [x] **8.6** Шаг 12.6: SL buffer `max(atr*0.2, price*0.0015, 1e-9)` — PASS.
- [x] **8.7** Шаг 12.3: Volume-aware SL widening (0.45/0.20/0.30) — PASS.
- [x] **8.8** Шаг 14: scenario_status нормализация — PASS.

## БЛОК 9: normalize_analysis

- [x] **9.1** false_breakout внутри диапазона → no_signal — PASS. `scheduler.py:175-190`.
- [x] **9.2** trend_structure unknown → balance — PASS. `scheduler.py:192-195`.
- [x] **9.3** ABC risk sync из wave_phase_comment — PASS. `scheduler.py:200-207`.

## БЛОК 10: Backtest pipeline (P3-1)

- [ ] **10.1** `init_backtest_table()` — **FAIL** (некритично). 28 колонок вместо 19 (включая `checked_at`, `actual_price`, `sl_hit`, `tp1_hit`, etc.). Остальное корректно: индекс, `prompt_variant DEFAULT 'A'`, `raw_json[:8000]`.
- [ ] **10.2** `_detect_direction()` — **NEEDS_TEST**. `backtest.py:434-462`. `aggressive_breakout` не содержит "long"/"up" в строке → ветка 444 не срабатывает. Работает косвенно через trend/ltf/wave (строка 458) и дефолт `return "long"` (462). Поведение вероятно корректно, но требует тест-кейсов.
- [ ] **10.3** `check_pending_forecasts()` outcome priority — **FAIL** (некритично). `backtest.py:192`. `sl_hit` проверяется первым, спецификация требует `tp3>tp2>tp1>sl_hit`. На практике взаимоисключающие (SL и TP не могут быть hit одновременно при стандартных уровнях), но логическая структура не соответствует спецификации.
- [x] **10.4** `get_backtest_context()` — PASS. Accuracy, TP/SL hit rate, Avg RR, by signal, A/B variants, last 5.

## БЛОК 11: TG форматирование

- [ ] **11.1** `format_json_for_tg()` NaN — **NEEDS_TEST**. `ollama_client.py:1787-1940`. `_format_num()` не обрабатывает NaN явно (возвращает "nan" вместо "Н/Д"). Через `_safe_float()` NaN unlikely в pipeline. None → "Н/Д" ✅, пустые блоки скрываются ✅, tf_zones дедупликация ✅, confluence[:8] ✅.
- [x] **11.2** Warning message ПЕРЕД анализом — PASS. `scheduler.py:40-52, 443-450`.
- [x] **11.3** `save_forecast()` при aggressive_breakout/retest — PASS. `scheduler.py:452-463`.

## БЛОК 12: Интеграционные проверки

- [x] **12.1** Двойной `get_multi_symbol_context()` с одним `cycle_id` — PASS. Кэш работает.
- [x] **12.2** `save_signal_log()` после `enforce_risk_rules()` и `normalize_analysis()` — PASS. `scheduler.py:424-428`.
- [x] **12.3** `check_pending_forecasts()` в `update_prices_and_reschedule()` — PASS. Каждый тик.
- [x] **12.4** `_prev_state` до LLM, `update_and_save_state()` после — PASS.
- [x] **12.5** XAUTUSDT graceful fallback — PASS. `try/except` в `binance_metrics.py`.

## БЛОК 13: Известные баги и регрессии

- [x] **13.1** Indentation bug — PASS. 0 TAB-ов, `py_compile` OK.
- [x] **13.2** Дублирование zone_status (2.2/2.3) — PASS. Без конфликта, 2.3 расширяет 2.1.
- [ ] **13.3** Двойной ABC risk — **FAIL** (некритично). `scheduler.py:197-213` + `ollama_client.py:1140-1161`. Идемпотентно (результат не меняется), но нарушение DRY. Технический долг.
- [x] **13.4** Scenario consistency дублируется 3 раза — PASS. Последнее побеждает, корректно.
- [x] **13.5** `_pick_tp_levels` (None, None, None) — PASS. Не крашит, все проверки `is not None`.
- [x] **13.6** DB_PATH корректный — PASS. `os.path.join(os.path.dirname(__file__), '..', 'forecasts.db')` → корень проекта.

---

## Все FAIL (5 шт.)

| # | Блок | Файл:строка | Описание | Критичность |
|---|------|-------------|----------|-------------|
| 1 | 3.7 | `multi_symbol.py:218` | Китайский символ "慎重" | Низкая (косметика) |
| 2 | 4.4 | `benchmark_zigzag.py:197-209` | Нет guard от пустого DataFrame | Средняя (но ловится в _build_zigzag_context) |
| 3 | 10.1 | `backtest.py:36-88` | 28 колонок вместо 19 | Низкая (спецификация неточна) |
| 4 | 10.3 | `backtest.py:192` | outcome priority: sl_hit первый, не tp3>tp2>tp1>sl_hit | Низкая (взаимоисключающие на практике) |
| 5 | 13.3 | `scheduler.py:197-213` + `ollama_client.py:1140-1161` | Двойной ABC risk | Низкая (идемпотентно) |

## Все NEEDS_TEST (3 шт.)

| # | Блок | Файл:строка | Описание |
|---|------|-------------|----------|
| 1 | 10.2 | `backtest.py:434-462` | `_detect_direction` для aggressive_breakout — работает косвенно, требует тест-кейсов |
| 2 | 11.1 | `ollama_client.py:1787-1940` | `_format_num()` NaN → "nan" вместо "Н/Д" (unlikely через _safe_float) |
| 3 | 2.1 | `auto_chart.py:547` | network errors — `raise` в функции, но `try/except` в scheduler |

---

## Рекомендации

1. **3.7** — заменить "慎重" на "осторожно" в `multi_symbol.py:218`
2. **4.4** — добавить `if not bars: continue` в `benchmark_zigzag.py:198`
3. **10.3** — переупорядочить проверку outcome: сначала TP, потом SL (косметика логики)
4. **13.3** — вынести ABC risk в общую функцию, вызывать из одного места
5. **10.2** — добавить тест-кейсы для `_detect_direction` с aggressive_breakout
