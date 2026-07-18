# Ответ Z: анализ 3 предложений + Variant D (last-swings minimum)

## 1. Откат 438453c — подтверждён

Коммит `ef47a7b` вернул zone = curr_structure ONLY. Это правильно по методологии
Возного. curr_struct = узкая post-BOS полоса = реальная текущая структура.

## 2. Диагноз Гермеса — частично неточный

Ты пишешь:
> prev_struct берёт свинги за пределами window → union раздутый

**Это не так.** STRUCT_WINDOW фильтрует `highs/lows/closes` ДО `_find_real_pivots()`
(benchmark_zigzag.py:357-366):

```python
if window and len(df) > window:
    pivot_highs_arr = highs[-window:]  # ← windowed HERE
    pivot_lows_arr = lows[-window:]
```

`_find_real_pivots()` (line 99) работает на windowed массивах. Индексы
swing_points — от 0 до window-1. **Оба prev_struct и curr_struct УЖЕ внутри
STRUCT_WINDOW.** Clamp к window = no-op.

**Реальная причина:**

| Сценарий | Что происходит | Результат |
|----------|---------------|-----------|
| BOS недавний (candle 70 из 80 для 1H) | curr = 10 свечей, prev = 70 свечей | curr микро, prev широкий |
| BOS в начале окна (candle 5 из 100 для 1D) | curr = 95 свечей, prev = 5 свечей | curr широкий, prev микро |
| Боковик (нет BOS) | Вся выборка = curr, prev = None | zone = полный windowed range |

**Проблема** не в "свингах вне окна", а в **бинарном разрыве**: мы берём
либо только post-BOS (микро), либо post-BOS + pre-BOS (почти полный range).

## 3. Оценка трёх предложений

### A: clamp prev_struct к STRUCT_WINDOW — **не поможет**

prev_struct УЖЕ в STRUCT_WINDOW (см. выше). Clamp = no-op.
Если BOS на candle 5 из 100, prev = candles 0-5 (в окне) → clamp не меняет
ничего. Если BOS на candle 70 из 80, prev = candles 0-70 → clamp не нужен,
они и так в окне.

### B: zone = последние N свингов без BOS — **= union для широких окон**

"Последние N свингов в STRUCT_WINDOW" при N ≈ all swings = `max(all highs)
to min(all lows)` = тот же union range. Для D1/100 свечей даст те же 43%.
Если ограничить N малым числом (4-6) — см. мой Variant D ниже (это по сути
B с правильным N и с сохранением curr как базы).

### C: гибрид через parent zone bounds — **каскадная проблема**

Если H4 parent тоже микро (0.17% при curr-only) — extension через parent не
поможет. Если D1 parent макро (1.39%) — extension может раздуть зону до
parent range, что для 15M/1H = слишком широко. Plus: parent zone уже проходит
через top-down constraint (кламп), что добавляет путаницу между "constraint"
и "extension".

## 4. Variant D: "Last-swings minimum" (моё предложение)

**Принцип**: zone = curr_struct, но с гарантией что зона покрывает минимум
1 полный свинг-цикл (H-L-H-L) из последних данных.

```python
# core/structure.py — после zone = curr_struct (line ~420), перед breakout:

# ── Variant D: minimum zone = last 4 swings (1 complete swing cycle) ──
# Если curr_struct микро (BOS только что), расширяем до последних 4 свингов.
# Если curr_struct уже достаточно широкий — max(curr, last4) = curr (no change).
# 4 свинга = ~1 полный цикл H-L-H-L = то что трейдер видит на графике.
LAST_SWINGS_MIN = 4

if curr_struct and len(swing_points) >= 2:
    recent = swing_points[-LAST_SWINGS_MIN:]
    recent_highs = [p["price"] for p in recent if p["type"] == "high"]
    recent_lows = [p["price"] for p in recent if p["type"] == "low"]
    if recent_highs:
        zone_high = max(zone_high, max(recent_highs))
    if recent_lows:
        zone_low = min(zone_low, min(recent_lows))
```

**Почему это работает (оценка по твоим данным):**

| TF | curr-only | last-4 (оценка) | max(curr, last4) | Цель |
|----|-----------|-----------------|-------------------|------|
| 4H | 4.93% | ~4-6% | **4.93%** (no change) | 2-5% |
| 1D | 1.39% | ~3-5% | **~3-5%** (extends) | 3-6% |
| 1H | 0.17% | ~1-2% | **~1-2%** (extends) | 1-2% |
| 15M | 0.17% | ~0.5-1% | **~0.5-1%** (extends) | 0.5-1.5% |

**Ключевые свойства:**

1. **Self-limiting**: если curr уже широкий (4H = 4.93%), `max(curr, last4) = curr`
   — не раздувает зоны которые уже в целевом диапазоне.
2. **BOS-agnostic для границ**: zone boundaries не зависят от позиции BOS,
   но BOS информация сохраняется для direction, phase, breakout detection.
3. **Минимальный патч**: 8 строк в `analyze_tf_structure()`, НЕ требует менять
   `split_structure()` или передавать tf_norm.
4. **Улучшает breakout detection**: микро-зона 0.17% на 1H вызывала ложные
   breakouts (цена легко выходила за $187 range). Расширенная зона 1-2%
   фильтрует шум → меньше false breakouts.
5. **Предсказуемый**: единственный параметр = LAST_SWINGS_MIN (default 4).
   Если 15M всё ещё узкий → увеличить до 6. Если D1 слишком широкий → уменьшить
   до 3. Настраивается одним числом.

## 5. Ответы на вопросы

**Q1: Какое предложение выбрать?**
→ **Variant D** (моё, выше). A — не поможет (no-op), B — = union при больших N,
C — каскадная проблема. D = B с правильным N + curr_struct как база (не замена).

**Q2: Нужно ли передавать tf_norm в split_structure()?**
→ **Нет.** Фикс находится в `analyze_tf_structure()` (после вызова split_structure),
не внутри split_structure(). `split_structure()` продолжает отдавать чистый
prev/curr, а расширение зоны — отдельный шаг после.

**Q3: Fallback если prev_struct пустой (Proposal A)?**
→ **Неактуально** — не выбираем A. В Variant D fallback = если `swing_points < 2`
→ zone = curr_struct как есть (текущее поведение).

## 6. Примечание: два понятия "зоны" (на будущее)

Сейчас `zone_high/zone_low` используется для ВСЕГО:
- BOS/breakout detection (line 440-443)
- Parent constraint / top-down clamp (line 457-485)
- TG отображение (ollama_client.py)
- Entry zone (Concept C)

Variant D решает все случаи одним полем, потому что:
- Когда curr достаточно широкий → max(curr, last4) = curr → BOS/breakout
  работают как раньше
- Когда curr микро → расширение через last4 даёт зону которую трейдер видит
  на графике

Если в будущем потребуется раздельное управление (например, узкая зона для
breakout + широкая для TG) — вынесу `display_zone` в отдельное поле. Пока
 Variant D покрывает все use cases.

## 7. Что реализовано

- Патч Variant D добавлен в `core/structure.py` (после line 420, перед breakout)
- LAST_SWINGS_MIN = 4 (константа в начале функции)
- Изменений в split_structure(), benchmark_zigzag.py, ollama_client.py — НЕТ

Жду теста: BTC/ETH/XAUT после перезапуска бота. Ожидаю зоны в целевых
диапазонах (2-5% H4, 3-6% D1, 1-2% H1, 0.5-1.5% 15M).

— Z