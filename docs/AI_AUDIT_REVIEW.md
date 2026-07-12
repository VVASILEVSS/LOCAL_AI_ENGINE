# LOCAL_AI_ENGINE — полный аудит проекта

**Дата аудита:** 2026-07-12
**Аудитор:** Hermes Agent (модель `glm-5.2-fast-preview`, провайдер `alibaba`)
**Ветка аудита:** `ai-review/agent-audit`
**Базовый коммит:** `79a0d52` (main)

---

## 1. Общая характеристика проекта

**LOCAL_AI_ENGINE** — Telegram-бот (aiogram 3.x) для технического анализа криптовалют, который работает поверх **локальной LLM** (LM Studio, модель `qwen2.5-vl-7b-instruct`). Бот собирает рыночные данные с Binance через ccxt, строит графики OHLCV, формирует многослойный контекст (ZigZag, A/D-объём, liquidity-магнит, state-tracker) и отправляет его LLM для генерации прогноза в структурированном JSON.

### Технологический стек
| Компонент | Технология |
|---|---|
| Telegram-бот | aiogram 3.x, AsyncIOScheduler (apscheduler) |
| LLM-бэкенд | LM Studio локально, OpenAI-совместимый endpoint `http://localhost:1234/v1/chat/completions` |
| Рыночные данные | ccxt 4.5.54 (Binance spot + futures) |
| Графики | matplotlib (Agg backend) |
| Хранилище | SQLite (`forecasts.db`) + JSON state-файлы в `data/state/` |
| Зависимости | 26 пакетов (aiogram, ccxt, pandas, numpy, Pillow, httpx, apscheduler, ...) |

### Архитектура (68 .py файлов, 5658 строк в `core/`)
```
main.py                          — точка входа, polling
core/
├── config.py                    — TOKEN, endpoint, system prompts (37 строк)
├── handlers.py (498)            — Telegram-команды, inline-клавиатуры, callback-и
├── scheduler.py (434)          — APScheduler, автоанализ по таймеру
├── ollama_client.py (1753)      — LLM-клиент, JSON-парсинг, нормализация, risk-rules
├── auto_chart.py (656)         — OHLCV-графики, Fib, structural levels, session phase
├── liquidity_magnet.py (610)   — liquidity pools, equal highs/lows, магнит
├── liquidity_magnet/           — ДУБЛЬ того же модуля (bug!)
│   └── liquidity_magnet.py (610) — идентичен файлу выше
├── state_tracker.py (455)      — история уровней, ретесты, слом/перестройка
├── volume_filters.py (383)    — A/D-контекст, bullish/bearish bias, divergence
├── data_provider.py (208)     — единый поставщик OHLCV + CSV-архив
├── db.py (104)                — SQLite, forecasts, settings, backtest stats
├── utils.py (106)              — markets cache, symbol validation, tf sorting
├── binance_metrics.py (48)     — дополнительные метрики Binance
├── zigzag/                      — изолированный ZigZag-модуль
│   ├── structural_zigzag.py (6462 bytes)  — core logic
│   ├── benchmark_zigzag.py (14045 bytes) — multi-TF runner
│   ├── compare_zigzag.py (8993 bytes)    — сравнение с TV
│   ├── example_call.py (1177 bytes)      — пример вызова
│   └── structure_levels_test.py (1004 bytes) — runner
└── backups/                    — старые версии модулей (можно удалить)
```

### Пары и таймфреймы
- Пары: BTCUSDT, ETHUSDT, XAUTUSDT (spot), XAGUSDT (forced futures)
- ТФ: 15m, 1h, 4h, 1D (сортировка от старшего к младшему)
- Интервал автоанализа: 60 минут (по умолчанию)

---

## 2. Функционал

### ✅ Что реализовано и работает

| Фича | Статус | Качество |
|---|---|---|
| Telegram-команды | ✅ | 9 inline-кнопок (scan, screenshots, instruments, timer, timeframes, export, settings, about) |
| Много-ТФ анализ через LLM | ✅ | Серия графиков → series prompt → JSON-ответ |
| Локальный LLM через LM Studio | ✅ | httpx-клиент, async lock для очереди |
| ZigZag multi-TF | ✅ | 4 режима (lux_channel, hybrid_atr, ...), benchmark runner |
| A/D объёмный фильтр | ✅ | bullish/bearish bias, divergence detection, strength score |
| Liquidity magnet | ✅ | equal highs/lows, clustering, probability scoring |
| State-tracker | ✅ | JSON-файлы, зоны (saved/broken/rebuilt/false_breakout/retest) |
| SQLite прогнозы + backtest | ✅ | `forecasts.db`, win_rate, MAE, export CSV |
| Настройки через БД | ✅ | symbols, timeframes, interval_minutes, filter_mode |
| Динамика между анализами | ✅ | `USER_ANALYSIS_CACHE` — diff прошлого анализа |
| Risk-rules enforcement | ✅ | `enforce_risk_rules` в ollama_client |
| Auto-Chart с Fib + structural levels | ✅ | 50-барный lookback, pivot detection |

### 🟡 Реализовано частично / требует доработки

| Фича | Проблема |
|---|---|
| Wyckoff-фазы (ТЗ P0) | В промпте есть (`accumulation_state`, `wave_phase`), но **логика неиерархична** — модель может выдать `false_breakout` + `accumulation` + `impulse` одновременно. ТЗ явно требует иерархии. |
| Liquidity heatmap (ТЗ P2) | Есть `liquidity_heatmap.py` (366 строк), но не интегрирован в scheduler. `liquidity_magnet.py` дублируется в `liquidity_magnet/liquidity_magnet.py` — незвестно какой используется. |
| Confluence levels | В JSON-схеме есть, нормализация есть, но _pick_tp_levels берёт уровни только из ZigZag-context, игнорируя liquidity-magnet. |
| State-tracker persistence | Файлы в `data/state/`, но нет GC — накапливаются без очистки |
| Backtest | Считает win_rate по `actual_price_1h` (через час), но `is_correct` упрощён: Long correct если price > pred_price. Нет учёта TP/SL, нет partial fills. |

### ❌ Не реализовано (из ТЗ и roadmap)

| Фича | Приоритет |
|---|---|
| Иерархическая нормализация сигналов (P0.1) | 🔴 Критично — модель противоречива |
| Разделение сущностей: market_phase vs signal_status vs volume_confirmation (P0.2) | 🔴 |
| Liquidity heatmap → heatmap визуализация | P2 |
| Тесты (нет pytest-сьюта, только ad-hoc скрипты) | — |
| CI/CD (нет GitHub Actions) | — |
| Типизация (нет mypy, py.typed, только `from __future__ import annotations`) | — |
| Логирование в файл (только console `logging.basicConfig(level=INFO)`) | — |
| Rate limiting для Binance API | 🟡 (enableRateLimit=True в ccxt, но без явных retry) |
| Обработка недоступности LM Studio (fallback) | 🔴 Если LLM вниз — бот молча падает |
| Graceful shutdown | 🟡 KeyboardInterrupt есть, но scheduler не shutdown явно |

---

## 3. Оценка реализации

### 🟢 Сильные стороны

1. **Чистая модульная архитектура** — каждый модуль имеет header-комментарий (Назначение / Отвечает за / Связан с). Это редкость в prototype-проектах.

2. **Промышленный JSON-схема промпт** (`PRO_TA_USER_PROMPT`) — 40+ полей с строгой типизацией, иерархией risk_management (primary/alternative), confluence_levels, tf_zones. Это level senior-разработки для LLM-инженерии.

3. **Надёжный JSON-парсинг LLM-ответа** — `ollama_client.py` содержит многоуровневую очистку (unicode quotes, missing commas, trailing commas), regex-фиксы для распространённых LLM-ошибок. Это решает реальную боль — локальные LLM часто ломают JSON.

4. **Изолированный ZigZag-модуль** — отдельный пакет `core/zigzag/` с README, example_call, benchmark. Хорошая практика — можно тестировать независимо.

5. **State-tracker с domain-семантикой** — `ZONE_STATUS_VALUES` (saved/broken/rebuilt/false_breakout/retest) отражают реальные рыночные фазы, не абстрактные статусы.

6. **Data provider с архивированием** — `OhlcvDataProvider` хранит current CSV + архивирует старые + ограничивает archive files per symbol/tf. Сильный data-pipeline.

7. **Series-prompt с динамикой** — `USER_ANALYSIS_CACHE` позволяет LLM сравнивать текущий анализ с прошлым (diff уровней, изменения тренда). Редкая фича для TG-ботов.

### 🔴 Слабые стороны и риски

#### 🔴 1. Дублирование `liquidity_magnet`
```
core/liquidity_magnet.py          (610 строк)
core/liquidity_magnet/__init__.py
core/liquidity_magnet/liquidity_magnet.py  (610 строк — ИДЕНТИЧЕН)
core/liquidity_magnet/test_liquidity_magnet.py
```
`diff` показывает 0 различий. Python-импорт `from core.liquidity_magnet import ...` может разрешиться в **пакет** (директория), а не в **модуль** (файл). Это:
- источник багов при правках (правишь один — второй сталеет)
- путаница в import-resolution

**Решение:** удалить `core/liquidity_magnet.py` (файл), оставить пакет `core/liquidity_magnet/` с `liquidity_magnet.py` внутри, или наоборот — оставить файл, удалить пакет.

#### 🔴 2. `requirements.txt` в UTF-16
```
$ file requirements.txt
Unicode text, UTF-16, little-endian text, with CRLF line terminators
```
`pip install -r requirements.txt` может сбоить на Linux-контейнерах и CI. Должен быть UTF-8 + LF.

**Решение:** `iconv -f UTF-16 -t UTF-8 requirements.txt > tmp && mv tmp requirements.txt && dos2unix requirements.txt`

#### 🔴 3. Хардкод в `core/config.py`
```python
LOCAL_AI_ENDPOINT = "http://localhost:1234/v1/chat/completions"
MODEL_NAME = "qwen_qwen2.5-vl-7b-instruct"
```
Нет env-var fallback. Если LM Studio на другом порту — нужно править код.

**Решение:** `LOCAL_AI_ENDPOINT = os.getenv("LOCAL_AI_ENDPOINT", "http://localhost:1234/v1/chat/completions")`

#### 🔴 4. `main.py` хардкодит symbols
```python
from core.db import set_setting
set_setting('symbols', ["BTCUSDT", "XAUTUSDT"])  # ← при каждом старте перетирает БД
```
Это перетирает любые настройки пользователя из TG-меню при каждом запуске.

**Решение:** убрать `set_setting('symbols', ...)` из `main.py`, либо обернуть в `if not get_setting('symbols'):`

#### 🟡 5. Нет обработки недоступности LM Studio
В `ollama_client.py` httpx-запрос к `localhost:1234` без retry/fallback. Если LM Studio не запущен — бот молча падает в scheduler-задаче, пользователь не получает уведомления.

**Решение:** retry с exponential backoff (3 попытки), fallback-сообщение в TG "⚠️ Локальная LLM недоступна".

#### 🟡 6. `forecasts.db` в истории Git (был убран, но не стёрт)
В коммитах до `9f3cf17` файл `forecasts.db` отслеживался. `git rm --cached` убрал его из tracking, но **история Git всё ещё содержит файл**. Любой может извлечь его через `git log --all -- forecasts.db`.

**Решение (опционально):** `git filter-repo --path forecasts.db --invert-paths` (переписывание истории — force push). Если тестовые данные не секреты — можно оставить.

#### 🟡 7. Нет тестов
`tests/` содержит ad-hoc скрипты (AD-дивергенция, ZigZag comparison), но нет pytest-сьюта с assert-ами. `liquidity_magnet/test_liquidity_magnet.py` существует, но это единичный случай.

**Решение:** добавить `pytest` в requirements, создать `tests/test_json_parser.py` (для `ollama_client._parse_json_response` — критичный путь), `tests/test_risk_rules.py`, `tests/test_zigzag.py`.

#### 🟡 8. `start_bot.bat` жёстко привязан к пути
```
D:\telega\LOCAL_AI_ENGINE
```
Непортабельно.

**Решение:** `cd /d "%~dp0"` (перейти в папку bat-файла).

#### 🟡 9. Нет type-hints в `db.py`
```python
def save_forecast(asset: str, pred_trend: str, ...):  # нет return type
def get_backtest_stats() -> dict:  # ok, но внутренние без типов
```

#### 🟡 10. SQL-инъекции — низкий риск, но есть
В `db.py` используются параметризованные запросы (`?` placeholders) — это хорошо. Но `get_history_df` делает `SELECT *` без фильтра — если таблица растёт, экспорт станет тяжёлым.

#### 🟢 11. Очистка `archive/` — много подпапок
```
archive/archivezigzag_v1/
archive/zigzag_backup_20260511_202610/
archive/zigzag_backup_before_patch/
archive/zigzag_v1/
core/backups/
tests/AD_backup_old/
tests/зиг заг/        # кириллица + пробел в имени папки
tests/ликвидации/      # кириллица в имени папки
```
Кириллица в путях может ломать Windows-скрипты и CI.

---

## 4. Предложения по улучшению

### 🚀 Приоритет P0 (критично, сделать первым)

1. **Удалить дубль `liquidity_magnet.py`** — оставить пакет `core/liquidity_magnet/`, удалить файл-дубль.

2. **Конвертировать `requirements.txt` в UTF-8** — `iconv -f UTF-16 -t UTF-8 && dos2unix`.

3. **Вынести `LOCAL_AI_ENDPOINT` и `MODEL_NAME` в `.env`** — `os.getenv()` с default.

4. **Убрать `set_setting('symbols', ...)` из `main.py`** — не перетирать БД при старте.

5. **Иерархическая нормализация сигналов (из ТЗ)** — в `enforce_risk_rules` добавить priority:
   ```
   signal_priority = ["aggressive_breakout", "false_breakout", "retest", "reversal", "accumulation", "no_signal"]
   ```
   Если модель выдала несколько — оставлять только highest-priority.

### 📈 Приоритет P1 (важно)

6. **Retry/fallback для LM Studio** — 3 попытки с backoff, fallback-сообщение в TG.

7. **Интегрировать `liquidity_heatmap.py` в scheduler** — сейчас отдельный модуль, не вызывается.

8. **`_pick_tp_levels` должен учитывать liquidity-magnet pools** — не только ZigZag-levels.

9. **GC для `data/state/`** — удалять state-файлы старше N дней (настраиваемо).

10. **pytest-минимум** — `tests/test_json_parser.py`, `tests/test_risk_rules.py`, `tests/test_db.py`.

11. **GitHub Actions CI** — `python -m py_compile` на все .py + pytest на минимальный сьют.

### 🎯 Приоритет P2 (nice-to-have)

12. **Логирование в файл** — `logging.FileHandler('logs/bot.log')` + rotation.

13. **Graceful shutdown** — `scheduler.shutdown()` в `finally:` блоке.

14. **Type hints везде** — `mypy --strict core/` как цель.

15. **Переименовать кириллические папки** — `tests/зиг заг/` → `tests/zigzag/`, `tests/ликвидации/` → `tests/liquidity/`.

16. **Докеризация** — `Dockerfile` + `docker-compose.yml` для воспроизводимости.

17. **README проекта** — сейчас README только в `core/zigzag/`. Нужен корневой `README.md` с описанием, install, usage, architecture.

18. **Backtest v2** — учитывать TP/SL, не только `price > pred_price` через час.

---

## 5. Оценка по шкале

| Критерий | Оценка | Комментарий |
|---|---|---|
| **Архитектура** | 8/10 | Чистые модули, header-комментарии, изоляция ZigZag. Минус: дубль liquidity_magnet. |
| **Функционал** | 7/10 | Wyckoff+ZigZag+A/D+Liquidity — мощный стек. Минус: иерархия сигналов не реализована (P0 ТЗ). |
| **Качество кода** | 6/10 | Хорошо для prototype, но: UTF-16 requirements, хардкоды, нет тестов. |
| **Безопасность** | 7/10 | .env не коммитится, параметризованные SQL. Минус: forecasts.db был в истории. |
| **Надёжность** | 5/10 | Нет retry для LLM, нет graceful shutdown, нет тестов, нет fallback. |
| **Документация** | 6/10 | ТЗ есть, README в zigzag/. Минус: нет корневого README, нет API-док. |
| **DevOps** | 3/10 | Нет CI, нет Docker, bat-файл с хардкод-путём. |
| **Общая** | **6/10** | Сильный prototype с серьёзным функционалом, но нуждается в hardening. |

---

## 6. Что сделано в этой ветке (`ai-review/agent-audit`)

| Действие | Файл |
|---|---|
| Полный аудит | `docs/AI_AUDIT_REVIEW.md` (этот файл) |
| Skill для повторной работы | `docs/AI_AGENT_SKILL.md` |
| Prompt для ветки | `docs/AI_AGENT_PROMPT.md` |
| История и прогресс | `docs/AI_AUDIT_PROGRESS.md` |

---

**Аудитор:** Hermes Agent (GLM 5.2 Fast Preview)
**Контакт:** через платформу Hermes (Nous Research)
