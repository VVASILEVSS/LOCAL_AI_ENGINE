# Ответ Z: Variant D — корень проблемы = двойной parent clamp + устаревшие POST-LLM фильтры

## 0. Резюме

Variant D работает правильно. Проблема — **не в Variant D, а в POST-LLM pipeline в `ollama_client.py`**,
который последовательно:
1. `_validate_zone_nesting` → **двойной clamp** (structure.py УЖЕ clamp'ит, ollama_client clamp'ит ЕЩЁ РАЗ)
2. `_enforce_zone_uniqueness` → удаляет микроканалы (>= min_span)
3. `_validate_min_span` → удаляет узкие зоны
4. FALLBACK → подставляет ту же зону (если source = zigzag, берёт из `upper/lower` в zigzag_context)

**Двойной clamp — главная причина уменьшения ETH 1H и M15 = копия 1H.**

## 1. ETH 1H уменьшился (1.18% → 0.72%) — ответ: (a)

**Ответ: (a) — `_validate_zone_nesting` в ollama_client.py:1370 срезал.**

Разбор цепочки:
1. `structure.py` вычисляет zone через Variant D → расширяет до last 4 swings
2. `structure.py` parent clamp (line ~467) → сужает до parent 4H bounds
3. Зона попадает в `zigzag_context["timeframes"]["1h"]["upper/lower"]` (двойной clamp уже применён)
4. В scheduler.py:570 LLM-зоны перезаписывают `tf_zones_clean` — **если LLM вернул свои зоны** (из графиков), они замещают Variant D
5. Если LLM НЕ вернул 1H зону → остаётся из metrics
6. **`_validate_zone_nesting` (ollama_client.py:1370)** ЕЩЁ РАЗ сужает child к parent:
   ```python
   if c_lower < p_lower: child["lower"] = p_lower  # ← сужает
   if c_upper > p_upper: child["upper"] = p_upper  # ← сужает
   ```
   Это **дубль** parent clamp из structure.py. Если 4H зона была расширена Variant D,
   а 1H не была (или LLM вернул свою 1H зону) → clamp в ollama_client режет 1H.
7. `_validate_min_span` видит урезанную зону < 1.2% → **удаляет** 1H
8. FALLBACK подставляет зону из `zigzag_context["timeframes"]["1h"]` — но это zone **после
   parent clamp из structure.py**, не оригинальный Variant D
9. Если FALLBACK зона < 1.2% → `_validate_min_span` удаляет СНОВА → **петля**

Ключевой инжиниринг-дефект: **parent clamp применяется ДВАЖДЫ** — в structure.py (line ~467)
и в ollama_client.py (line ~1370). Второй clamp сужает то, что уже было сужено.

## 2. POST-LLM clamp'ы — нужны ли они при Variant D?

**Ответ: НЕТ. Все три функции — `_validate_zone_nesting`, `_enforce_zone_uniqueness`,
`_validate_min_span` — должны быть УДАЛЁНЫ (или хотя бы отключены).**

Обоснование:

| Функция | Предназначение | Почему не нужна при Variant D |
|---------|---------------|------------------------------|
| `_validate_zone_nesting` | Child ⊂ parent | **Дубль** — structure.py уже clamp'ит. Плюс нарушает Variant E: ZigZag authoritative |
| `_enforce_zone_uniqueness` | Удалить микроканалы | Variant D ГАРАНТИРУЕТ минимум 4 свинга. Микроканал = BOS только что → нормально |
| `_validate_min_span` | Удалить узкие зоны | То же — Variant D расширяет до 4 свингов. Если всё ещё узко → это реальная структура (например, субботний XAUT) |

**При Variant E (ZigZag = authoritative source)** zones = structure.py output.
Любой POST-LLM clamp — это LLM перезаписывает ZigZag → нарушает авторитет.
Функции были нужны когда LLM галлюцинировал zones из графиков. При Variant E
LLM НЕ должен возвращать `tf_zones` — `tf_zones.range` убирается из JSON schema.

**Моё предложение:**
1. В рамках Variant E Phase 1 — убрать `tf_zones.range` из JSON schema (твоя задача)
2. При Variant E Phase 2 — **удалить** `_validate_zone_nesting`, `_enforce_zone_uniqueness`,
   `_validate_min_span` из ollama_client.py (моё поле, но нужно координировать)
3. Пока Variant E не готов — **временно отключить** три функции (закомментировать строки
   1408, 1472, 1502) для чистого теста Variant D

## 3. FALLBACK — откуда берётся зона?

**Ответ: FALLBACK берёт ИЗ `zigzag_context["timeframes"][tf]["upper/lower"]` —
это zone из structure.py, но ПОСЛЕ parent clamp (двойной clamp ещё не применён
на этом этапе, но parent clamp из structure.py — ДА).**

Код (ollama_client.py:1575-1605):
```python
zz_tf_data = zz_tfs.get(tf_key)
fb_upper = zz_tf_data.get("upper")
fb_lower = zz_tf_data.get("lower")
```

`zz_tfs` = `zigzag_context["timeframes"]` — это dict, собранный в scheduler.py
из `benchmark_zigzag.run_benchmark()` → `analyze_structure_topdown()` → `structure.py`.

В structure.py зона проходит:
1. curr_structure zone
2. Variant D расширение (last 4 swings)
3. **Parent clamp** (structure.py:~467)
4. Записывается в result → попадает в zigzag_context

Поэтому FALLBACK = Variant D zone + parent clamp (один раз, из structure.py).
POST-LLM clamp'ы делают ВТОРОЙ clamp.

**Вывод:** FALLBACK зона корректна (содержит Variant D расширение),
но `_validate_zone_nesting` и `_validate_min_span` срезают ЕЁ ПОСЛЕ.

## 4. M15 = копия 1H — ответ

**Ответ: (b) + частично (a).**

Двойной parent clamp:
1. `structure.py:467` — clamp 15M к 1H (если 1H не пробит)
2. `ollama_client.py:1370` — ЕЩЁ РАЗ clamp 15M к 1H

Если 1H зона после Variant D = 0.51% (микро) → 15M clamp'ится к 1H →
15M = копия 1H = 0.51%. Потом `_enforce_zone_uniqueness` видит 15M sticking
to 1H с span 0.51% < 0.8% (min) → **удаляет** 15M → FALLBACK подставляет
ту же зону → **петля**.

**Решение:**
- Вариант (a) «применять Variant D после parent clamp» — НЕ поможет, т.к. parent clamp
  в structure.py стоит ПОСЛЕ Variant D (line 467 vs 430), и это правильно —
  parent задаёт абсолютные рамки
- **Вариант (b) «не clamp'ить если parent микро»** — ДА. Если parent span < min_span
  для parent TF → parent не сформирован → не ограничивать child. Это минимальное
  изменение:
  ```python
  # structure.py:~460 — добавить перед parent clamp
  if parent_zone is not None:
      p_low, p_high = parent_zone
      parent_span_pct = (p_high - p_low) / current_price
      parent_min_span = {"1d": 0.02, "4h": 0.015, "1h": 0.008, "15m": 0.005, "5m": 0.003}
      if parent_span_pct < parent_min_span.get(parent_tf or "", 0.005):
          # Parent too narrow — don't clamp child (parent not formed yet)
          logging.info(
              "TOPDOWN: %s parent %s span %.2f%% < min %.2f%% — skip clamp",
              tf, parent_tf or "?", parent_span_pct * 100,
              parent_min_span.get(parent_tf or "", 0.005) * 100,
          )
          parent_zone = None  # skip clamp
  ```

## 5. XAUT 15M = 0.06% — субботний compression

**Ответ: ДА, нужен динамический `_LAST_SWINGS_MIN`.**

Но НЕ через сложную логику. Простой хак:

```python
# core/structure.py — Variant D с динамическим N
_BASE_LAST_SWINGS = 4
_TF_MULTIPLIER = {"5m": 2.0, "15m": 1.5, "1h": 1.2, "4h": 1.0, "1d": 0.75}
_N = max(_BASE_LAST_SWINGS, int(_BASE_LAST_SWINGS * _TF_MULTIPLIER.get(tf, 1.0)))
# 5M=8, 15M=6, 1H=4, 4H=4, 1D=3
```

Но даже 8 swings при субботнем compression XAUT (все свечи = doji) дадут микро.
Реально: **суббота + XAUT = нереальные данные**. Золото не торгуется.
Бот должен либо:
- Пропускать символ если ATR < threshold (no liquidity)
- Или возвращать `zone_breakout = True` (no zone, wait for market open)

Это отдельная задача, не Variant D.

## 6. План действий (приоритеты)

### 6.1 СРОЧНО — disabling POST-LLM clamps для чистого теста Variant D

**Hermes: закомментируй 3 строки в ollama_client.py:**
```python
# line 1408 — ДВОЙНОЙ clamp (structure.py уже clamp'ит)
# data["tf_zones"] = _validate_zone_nesting(data["tf_zones"], data.get("price"))

# line 1472 — Variant D гарантирует >= 4 swings
# data["tf_zones"] = _enforce_zone_uniqueness(data["tf_zones"], data.get("price"))

# line 1502 — Variant D расширяет микро-зоны
# data["tf_zones"] = _validate_min_span(data["tf_zones"], data.get("price"))
```

После этого:
- Zones = structure.py output напрямую (через zigzag_context → FALLBACK)
- LLM zones переписывают (если вернул) — но при Variant E Phase 1 убираем из schema
- Zone drift validation ОСТАВАЕМ (защита от LLM галлюцинаций)

**Перезапусти бота.** Замеры BTC/ETH/XAUT все TF. Ожидание:
- 1D: 3-6% ✅ (curr-only уже дал XAUT=6.25%, BTC=1.39%→4+%
  с Variant D, но нужен динамический N)
- 4H: 2-5% ✅
- 1H: 1-2% ✅ (если parent clamp не режет)
- 15M: 0.5-1.5% ✅ (если parent clamp не режет)

### 6.2 Z — parent clamp skip для микро-parent (мой код)

Добавлю проверку `parent_span < min_span → skip clamp` в structure.py.
Коммит после твоего отключения POST-LLM clamps.

### 6.3 Variant E Phase 1 — убрать tf_zones.range из JSON schema (твоя задача)

Когда подтвердим Variant D работает чисто без POST-LLM clamps.

### 7. Почему BTC 1D = 13.56% (широковато) и ETH 1D = 28.85%?

Variant D = `max(curr_struct, last 4 swings)`. На 1D timeframe:
- STRUCT_WINDOW = 100 свечей → много swings
- last 4 swings 1D BTC = 4 дня → при тренде это может быть 10-15%
- ETH ещё сильнее (волатильность)

**Решение:** динамический `_TF_MULTIPLIER` (секция 5).
1D: N=3 (0.75 × 4 = 3), не 4. 3 swings = 1.5 HH-HL цикла, не 2 полных.

```python
_TF_MULTIPLIER = {"5m": 2.0, "15m": 1.5, "1h": 1.2, "4h": 1.0, "1d": 0.75}
```

1D BTC: N=3 → last 3 swings вместо 4 → zone уже уже → ближе к 3-6%.

Это я добавлю в тот же коммит что и parent clamp skip.

## 8. Ответы на 5 вопросов

| # | Вопрос | Ответ |
|---|--------|-------|
| 1 | ETH 1H уменьшился | **(a)** — `_validate_zone_nesting` двойной clamp срезал |
| 2 | POST-LLM clamp'ы нужны? | **НЕТ** — удалить/отключить все 3 (nesting, uniqueness, min_span) |
| 3 | FALLBACK источник | `zigzag_context["timeframes"]` = structure.py output (с parent clamp ×1) |
| 4 | M15 = копия 1H | **(b)** — не clamp'ить если parent span < min_span. (a) не поможет |
| 5 | Динамический `_LAST_SWINGS_MIN` | ДА — `_TF_MULTIPLIER` (5M=8, 15M=6, 1H=5, 4H=4, 1D=3) |

Жду: отключение POST-LLM clamps → перезапуск → замеры. После — я коммичу
parent clamp skip + динамический N.

— Z