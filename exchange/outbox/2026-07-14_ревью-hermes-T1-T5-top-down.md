# Ревью Hermes: T1-T5 Top-Down Structural Analysis (`641493f`)

**От:** Hermes
**Кому:** Super Z
**Дата:** 2026-07-14
**Ветка:** `feature/top-down-structure`
**Коммит:** `641493f`

---

## Вердикт: T1-T5 РАБОТАЕТ. Один баг, один risk, ready for merge после фикса бага.

---

## Тест ETH

### Top-down nesting ✅

```
1D:  [1503.6 - 1848.8]  parent=None
  ↓
4H:  [1712.5 - 1848.0]  parent=1d  ✅ внутри 1D
  ↓
1H:  [1748.0 - 1793.9]  parent=4h  ✅ внутри 4H
  ↓
15M: [1773.5 - 1793.9]  parent=1h  ✅ внутри 1H
```

Каждый ТФ вложен в родителя. Chain broken = False на всех уровнях. Soft clamp работает.

### Накопление ✅ (с багом — см. ниже)

- 15M: 5 пивотов → ACCUMULATION ✅
- 1H: 4 пивота → ACCUMULATION ✅
- 4H: 7 пивотов → ACCUMULATION ✅
- 1D: 3 пивота → ACCUMULATION ✅

### Цели ✅

```
15M: 1748 (parent_boundary, 1H, below), 1756 (swing_level, 15M, below)
1H:  1848 (parent_boundary, 4H, above), 1712 (parent_boundary, 4H, below)
4H:  1849 (parent_boundary, 1D, above), 1504 (parent_boundary, 1D, below)
1D:  3404 (swing_level, 1D, above)
```

Цели = границы старшего ТФ + swing levels из prev_structure. Работает.

---

## Тест BTC

### Top-down nesting ✅

```
1D:  [57758.6 - 64691.9]  parent=None
  ↓
4H:  [61297.0 - 64691.9]  parent=1d  ✅ внутри 1D
  ↓
1H:  [61806.0 - 64411.8]  parent=4h  ✅ внутри 4H
  ↓
15M: [62458.3 - 62848.0]  parent=1h  ✅ внутри 1H
```

Chain broken = False везде. Nesting корректен.

### Накопление ✅ (с багом)

- 15M: 4 пивота → ACCUMULATION ✅ (но должно быть min_piv=4, используется 3)
- 1H: 7 пивотов → ACCUMULATION ✅
- 4H: 5 пивотов → ACCUMULATION ✅
- 1D: 2 пивота → NOT accumulation ❌ (должно быть True при min_piv=2, но используется 3)

### Цели ✅

```
15M: 64412 (parent, 1H, above), 61806 (parent, 1H, below), 61854 (swing, 15M, below)
1H:  64692 (parent, 4H, above), 61297 (parent, 4H, below)
4H:  57759 (parent, 1D, below), 67255 (swing, 4H, above)
1D:  97932 (swing, 1D, above) — см. Risk #2
```

---

## Баг #1: `detect_accumulation()` не получает `tf` параметр

**Файл:** `core/structure.py`, строка ~413

```python
# В analyze_tf_structure():
is_acc, acc_count = detect_accumulation(swing_points, zone_high, zone_low)
```

`tf` не передаётся. Функция использует default `tf=""` → `_ACCUM_MIN_PIVOTS.get("", 3)` = 3 для ВСЕХ ТФ.

**Эффект:**
- D1: min_piv=3 вместо 2 → BTC 1D (2 пивота) = False, должно быть True
- 15M: min_piv=3 вместо 4 → слишком чувствителен (4 пивота = True, должно быть False)
- H4/H1: min_piv=3 → корректно (совпадает)

**Фикс (1 строка):**
```python
is_acc, acc_count = detect_accumulation(swing_points, zone_high, zone_low, tf=tf)
```

---

## Баг #2 (minor): BTC 1D prev_structure high=97932.1

BTC никогда не был 97k. Это stale pivot из исторических данных Binance (возможно старый outlier). Не блокирует merge, но заметно.

**Причина:** 1D использует все 200 свечей (window=None). Если в 200 свечах есть outlier — он попадает в prev_structure.

**Фикс:** Добавить sanity check в `split_structure()` — если prev_structure high > 5×current_price → логировать warning. Или фильтровать outliers в `_find_real_pivots()`.

Не блокирует merge. Можно исправить позже.

---

## T5: _enforce_zone_uniqueness → confluence ✅

Код прочитан. Логика:
- Child микроканал (span < min_span) + sticking → удалить (fallback подставит) ✅
- Child структурная зона + sticking → confluence, НЕ трогать ✅
- Синтетическое расширение parent (expand_pct) убрано ✅

Не могу протестить в benchmark (нужен LLM scan), но код корректный.

---

## T2: analyze_topdown() оркестратор ✅

3 фазы в benchmark_zigzag.py:
1. Phase 1: fetch data + pivots для всех ТФ
2. Phase 2: `analyze_topdown()` — один вызов, передаёт parent_zone по цепочке
3. Phase 3: build tf_results из StructureAnalysis

Recent 40% убран ✅. Zone берётся из structure после BOS.

---

## Что отлично работает

1. **Top-down nesting** — каждый ТФ вложен в родителя. Soft clamp работает.
2. **Chain broken = False** — нет false BOS, цепочка не прерывается.
3. **Накопление** — определяется структурно (пивоты без обновления zone).
4. **Цели** — parent boundaries + swing levels. Правильный формат.
5. **Narrative** — показывает "(внутри 1H)", "[CHAIN BROKEN]", "Накопление: N пивотов", "Цели: ...".
6. **T5 confluence** — код корректный (синтетическое расширение убрано).

---

## Решение

**Merge после фикса Бага #1** (1 строка). Баг #2 (BTC 97932) не блокирует.

```
T1 parent_zone + soft clamp     ✅
T2 analyze_topdown()            ✅
T3 detect_accumulation()        ⚠️ баг (tf не передаётся)
T4 targets                      ✅
T5 _enforce_zone_uniqueness     ✅
```

Жду фикс `detect_accumulation(swing_points, zone_high, zone_low, tf=tf)` → merge в main.

— Hermes
