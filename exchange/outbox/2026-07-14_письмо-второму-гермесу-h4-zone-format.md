# Письмо второму Гермесу — задание на завтра (H4 zone format)

**От:** Hermes (GLM)
**Кому:** Второй Гермес
**Дата:** 2026-07-14
**Статус:** задание, не срочное (на завтра)

---

## Контекст

Пользователь сверил H4 зоны бота со скрином TradingView. Бот показывает `curr_structure` (узкая зона после BOS: [61806-64692]), а пользователь видит **весь range** (prev_structure: ~[58000-67200]).

**Проверка:** `prev_structure` бота = high 67255 ≈ скрин 67200, low 57759 ≈ скрин 58000. Совпадает! Значит бот видит ту же структуру что TradingView — проблема в формате вывода, не в логике.

## Задание

Изменить формат `tf_zones` в Telegram-сообщении. Сейчас:

```
H4: [61806-64692]
```

Должно стать:

```
H4: range [57759-67255] (BOS 64497)
```

### Спецификация

1. **Границы ренджа** = `prev_structure` [low-high] — весь видимый range (BSL/SSL уровни)
2. **В скобках после BOS** = цена `bos.broken_level` — где произошёл слом структуры
3. Формат: `{TF}: range [{prev.low}-{prev.high}] (BOS {bos_price})`

### Где менять

- `core/ollama_client.py` — форматирование `tf_zones` для LLM prompt и/или Telegram сообщения
- `core/scheduler.py` — `_build_zigzag_context` уже копирует `structure` (коммит `7384792`), данные доступны
- В `structure` есть: `prev_structure.high/low`, `curr_structure.high/low`, `bos.broken_level`

### Данные доступны

После коммита `7384792` LLM видит `structure` в `zigzag_context.timeframes[tf].structure`:
- `prev_structure: {high, low, direction, pivot_count, candle_count}`
- `curr_structure: {high, low, direction, ...}`
- `bos: {direction, price}`
- `targets: [{level, side, tf}]` — liquidity magnet уровни (BSL=above, SSL=below)
- `is_accumulation: bool`, `accumulation_pivot_count: int`

### Не менять

- `core/structure.py` — Super Z модуль, не трогать
- Логику detect_bos / prev_structure / curr_structure — работает корректно
- Фикс BUG 1 (max last 5 swing highs) + BUG 2 (period_high/period_low) — уже в `6d4cafb`, не трогать

### Проверка

После реализации — сверить с TradingView скрином:
- H4 range [57759-67255] ≈ TradingView [58000-67200] ✅
- BOS 64497 — должен быть виден на графике
- M15 ложный пробой — должен отмечаться (BUG 2 фикс)

## Liquidity Magnet (контекст)

Файлы `lm_compass.json` + `lm_feels.json` в корне репо — TradingView индикаторы FeelsStrategy (forexobroker). Super Z скачал их 14 июля.

Логика (из описания Compass):
- Каждый swing high/low = liquidity zone
- BSL (Buy-Side Liquidity) = above highs — цель для sweep вверх
- SSL (Sell-Side Liquidity) = below lows — цель для sweep вниз
- Sweep = candle pierces untaken zone with wick, closes back through → signal

Логика пользователя:
```
Цена растёт → обновляет highs, не обновляет lows
→ STOP: перестаёт обновлять highs (накопление)
→ BOS: пробивает последний high → слом
→ prev_structure high = BSL (цель для sweep вверх)
→ внутри range: накопление
→ при пробое одной из границ → смотрим на истину
→ тот же сценарий повторяется
```

`prev_structure` границы = BSL/SSL (ликвидити magnet). `curr_structure` = где цена сейчас (после BOS). Обе структуры должны быть видны LLM — это уже сделано (`7384792`).

## Текущий статус коммитов

```
main: 1e227d0 (баг-репорт)
feature/smc-indicator-compare:
  95f702d — Super Z BUG 1 fix (регрессия)
  6d4cafb — Hermes BUG 1 (max 5 recent highs) + BUG 2 (period_high/low)
  7384792 — Hermes structure → LLM (prev+curr structure видны)
```

Ждём merge `feature/smc-indicator-compare` в main.

---

— Hermes (GLM)
