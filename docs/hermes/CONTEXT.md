# LOCAL_AI_ENGINE — Контекст для Hermes Agent

Этот файл содержит все нюансы, обходы багов и текущий статус проекта.
Прочитай его первым делом, если продолжаешь работу на новой машине.

## Окружение

- **Python**: `.venv/` (Python 3.13). Активация: `source .venv/Scripts/activate && PYTHONPATH="" python ...`
- **PYTHONPATH BUG**: Hermes venv site-packages попадает в PYTHONPATH → всегда `PYTHONPATH=""` перед запуском
- **TZ**: `tzdata` установлен в .venv. Если нет — `pip install tzdata`
- **Рабочий язык**: русский
- **Git identity**: `VVASILEVSS`

## .env (НЕ в git — создать вручную)

Файл в корне репо `.env`:

```env
LLM_API_KEY=***          # из Hermes config.yaml, секция providers.qwen (sk-..., len=35)
LLM_BASE_URL=https://ws-yreibc51vw8gp9za.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
MODEL_NAME=glm-5.2-fast-preview
```

Без `MODEL_NAME` → 404. `config.py`: `LLM_MODE="cloud" if LLM_API_KEY present`.

## Binance API

- `ccxt.binance()` из KZ **БЕЗ VPN**
- Публичные данные (ticker/OHLCV/funding/OI/orderbook) **БЕЗ API-ключа**
- BTC ~$64K (на момент работы)

## Commit stack (новый → старый)

| Commit | Что |
|--------|-----|
| `5a818e0` | **P3-2 ZigZag compact context + confluence fix** (Hermes) |
| `28afeca` | Super Z коммит (статус P3-1 неизвестен — проверить) |
| `a1e1588` | **Zone TTL fix** — ensure_ohlcv() теперь проверяет возраст кэша |
| `72dc23c` | **P2-3 self-consistency** — double-run + voting на signal_status |
| `7d08807` | Верификация P1+P2 (3/3 PASS) |
| `613d061` | P2-4 SL/TP validation (Super Z) |
| `3358159` | P1-2/P1-3/P1-4/P2-2 реализация (38 полей) |
| `ecc7a32` | P2 infrastructure (34/34 PASS) |
| `7798e96` | P0+P1 fixes (Super Z) |
| `c6b4c2e` | План P0/P1/P2/P3 |
| `c261bc8` | Полный цикл прогноза (cloud LLM) |
| `0d13bd6` | Валидация цен |

## P3 Status (актуальный на 2026-07-12)

| Task | Кто | Статус | Зависимости |
|------|-----|--------|-------------|
| **P3-2 ZigZag** | Hermes | ✅ `5a818e0` DONE | Независимо |
| **P3-1 Backtest** | Super Z | `28afeca` — проверить статус | Независимо |
| **P3-3 Multi-symbol** | Super Z | Заблокирован P3-1 | Нужна таблица прогнозов |
| **P3-4 A/B промпты** | Hermes | Заблокирован P3-1 | Нужен backtest для сравнения |

## Что сделано в P3-2

1. **Фикс confluence** (`core/zigzag/benchmark_zigzag.py:74`): `_extract_levels` искал `"zones"`, а `compact_result` хранит в `"levels"` → confluence был пустой. Фикс: `tf_result.get("zones") or tf_result.get("levels") or {}`. Результат: 6 уровней вместо 0.
2. **Компактный сериализатор** (`core/ollama_client.py:1903`): `_format_zigzag_context_compact(ctx)` — 4169→551 chars (87% экономии токенов).
3. **price_position** (`core/scheduler.py:82`): добавлен в compact_timeframes.
4. **max_tokens** 1200→2000, **timeout** 30→45 (`core/ollama_client.py:2025-2026`).

## Верификация P3-2

- **Ad-hoc: 15/15 PASS** — compact format, confluence non-empty, error handling
- **Полный цикл LLM**: 2/2 agreed (`no_signal` + `no_signal`), LLM вывел `tf_span_map` + `confluence_levels` + ZigZag-терминологию в reasoning
- Отчёт: `exchange/outbox/2026-07-12_p3-2-zigzag-контекст-компакт.md`

## Known bugs / обходы

### Zone TTL (fixed `a1e1588`)
`OhlcvDataProvider.ensure_ohlcv()` читал CSV без TTL → устаревший кэш (1H price ~72K) отдаётся вместо live ~64K → зона 72-77K оторвана от цены. Теперь TTL per TF: 15m=15мин, 1h=1час, 4h=4часа, 1d=24часа.

Проверка зон: M15⊆H1⊆H4⊆D1, цена внутри всех зон, ширина монотонно растёт.

### OI = N/A
`core/binance_metrics.py:28` — парсинг `oi.get('openInterest') or oi.get('amount')` не извлекает значение. **Не фиксировано.**

### Volume 0.07x / 0.92x
Аномально низкий volume — нужна проверка rolling mean. **Не фиксировано.**

### PYTHONPATH contamination
Hermes venv site-packages в PYTHONPATH. Обход: `PYTHONPATH=""` перед каждым запуском в LOCAL_AI_ENGINE/.venv.

## exchange/ протокол

- `exchange/inbox/` — задачи (от пользователя/модели)
- `exchange/outbox/` — отчёты (от Hermes/Super Z)
- `exchange/archive/` — старше 14 дней
- **ПРОВЕРЯЙ В ОБИХ РЕПО** (LOCAL_AI_ENGINE + AI-Analyzer-Plan)

## Ключевые файлы

### core/ollama_client.py
- `PRO_TA_SYSTEM_PROMPT` (line 32) — few-shot примеры
- `PRO_TA_USER_PROMPT` (line 162) — `{zigzag_context}` плейсхолдер
- `_format_zigzag_context_compact` (line 1903) — компактный ZigZag сериализатор
- `analyze_multi_images` (line 1946) — P2-3 double-run+voting: 2 LLM runs (temp 0.15/0.25), `Counter(signals).most_common(1)`, timeout guard (run 1 >40с → skip run 2), `try/except` inside loop
- `enforce_risk_rules` (~line 1463) — P2-4 SL/TP validation (Super Z)
- `llm_generate` — cloud LLM вызов
- `max_tokens=2000, timeout=45` (line 2025-2026)

### core/auto_chart.py
- `fetch_and_plot` (line 547) — генерация графиков + metrics
- `get_technical_metrics` (line 485) — вызывает `get_structural_extremums`
- `get_structural_extremums` (line 192) — `current_price = float(closes[-1])` (line 231, цена из последней закрытой свечи)
- `_select_last_significant` (line 95) — nearest cluster_high above / cluster_low below price
- `_expand_range_to_h1_if_needed` (line 415) — ensures M15⊆H1

### core/data_provider.py
- `OhlcvDataProvider` (line 34)
- `ensure_ohlcv` (~line 203) — TTL кэш по таймфрейму (fixed `a1e1588`)
- Cache dir: `data/ohlcv/current/` (в .gitignore, НЕ в git)

### core/scheduler.py
- `_build_zigzag_context` (~line 60) — строит ZigZag контекст для промпта
- `compact_timeframes` (line 74) — включает price_position (fixed P3-2)
- `fetch_binance_metrics()` (~line 294) — P1-3
- `load_state + compare_state + build_state_context` (~line 338) — P1-2
- `analyze_multi_images` call (line 376) — с `chart_bytes_list` + `prev_ctx`
- Default TF: `["15m","1h","4h","1D"]` (line 24)

### core/state_tracker.py
- `_normalize_tf_key` (line 28): `15m`→`M15`, `1h`→`H1`, `4h`→`H4`, `1D`→`D1`
- `update_and_save_state(symbol, timeframe, current)` (line 414) — 3 args, no state_diff

### core/zigzag/benchmark_zigzag.py
- `_extract_levels` (line 73) — ищет `"zones" OR "levels"` (fixed P3-2)
- `_build_level_confluence` (line 81) — кластеризация уровней, tolerance 0.0035
- `run_benchmark` (line 182) — main entry point
- `output_mode="compact"` — `compact_result` хранит zones в `"levels"` (line 275), `full_result` в `"zones"` (line 299)

## Два репо

1. **LOCAL_AI_ENGINE** — `github.com/VVASILEVSS/LOCAL_AI_ENGINE` — основной, Python/tkinter/SQLite/crypto analysis
2. **AI-Analyzer-Plan** — `github.com/VVASILEVSS/AI-Analyzer-Plan` — FastAPI+Next.js, облачный LLM анализ

## Токен GitHub

Работает на оба репо. В Hermes config: `providers.qwen` секция.

## Как продолжить P3-4

1. Проверить статус P3-1 (Super Z backtest) — `git log --oneline | grep -i backtest`
2. Если P3-1 готов — прочитать его схему SQLite (таблица прогнозов)
3. Создать A/B тест: два варианта промпта (current PRO_TA vs simplified), запустить на тех же данных, сравнить accuracy% через backtest
4. ZigZag-промпт уже готов для A/B теста (compact format в `_format_zigzag_context_compact`)
