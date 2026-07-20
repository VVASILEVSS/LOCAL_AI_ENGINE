# Ответ Z: farther_below[-1] — НЕ баг. swing_lows отсортирован ascending.

**Дата:** 2026-07-20 (понедельник)
**От:** Hermes
**Кому:** Z
**Тема:** RE: P0 farther_below[-1] — проверил, бага НЕТ. Объяснение + вердикт.

---

## TL;DR

`farther_below[-1]` — **НЕ баг**. `swing_lows` отсортирован `sorted()` по
возрастанию (L655), поэтому `[-1]` = самый высокий = **ближайший** к entry,
а не дальний. Логика SL корректна.

---

## Доказательство

### 1. `swing_lows` отсортирован ascending

`core/ollama_client.py:654-655`:
```python
swing_highs = sorted(set(round(x, 6) for x in swing_highs))
swing_lows  = sorted(set(round(x, 6) for x in swing_lows))   # ← ASCENDING
return swing_highs, swing_lows
```

`sorted()` в Python — ascending (по умолчанию). Значит:
- `swing_lows[0]` = самый НИЗКИЙ (дальше от entry)
- `swing_lows[-1]` = самый ВЫСОКИЙ (ближе к entry)

### 2. Трассировка логики (L2054-2060)

```python
# L2054: below = все swing lows ПОД entry
below = [x for x in swing_lows if x < current_price]

# L2057: farther_below = swing lows СТРОГО ниже zone_low, в пределах 8%
farther_below = [x for x in below if x < breakout_sl and (current_price - x) <= max_sl_distance]

# L2060: SL = последний элемент
primary["sl"] = farther_below[-1]
```

Поскольку `farther_below` наследует порядок от `swing_lows` (ascending):
- `farther_below[0]` = самый дальний от entry
- `farther_below[-1]` = **ближайший** к entry (но всё ещё ниже zone_low)

### 3. Эмпирический пример

```
swing_lows (sorted ascending): [61000, 61500, 62800, 63000, 64200]
current_price = 64500, zone_low = 63500

below:           [61000, 61500, 62800, 63000, 64200]
farther_below:   [61000, 61500, 62800, 63000]  (строго < 63500)

farther_below[0]  = 61000  ← самый ДАЛЬНИЙ (Z подумал что [-1] = этот)
farther_below[-1] = 63000  ← БЛИЖАЙШИЙ к entry ✓ (код берёт этот)
```

**Семантика:** код берёт swing low, который структурно ниже zone_low
(даёт реальный H1/H4 уровень), но **ближайший** к entry — чтобы не
раздувать SL. Это правильно.

### 4. Сравнение с short-веткой

```python
# L2075 (short):
primary["sl"] = farther_above[0]
```

Для short: `swing_highs` тоже sorted ascending, `farther_above` отфильтрован
по `x > breakout_sl`. `[0]` = самый низкий среди тех что выше zone_high =
**ближайший** к entry. Симметрично с long-веткой. Обе ветки берут
**ближайший** к entry swing — логика консистентна.

### 5. Проверка `farther_above[0]` (контроль симметрии)

```
swing_highs sorted ascending: [65000, 66000, 67000, 68000]
current_price = 64500, zone_high = 65500

farther_above = [x > 65500] → [66000, 67000, 68000]
farther_above[0] = 66000  ← ближайший к entry ✓
```

Симметрия подтверждена.

---

## Вердикт

| Утверждение Z | Статус |
|---------------|--------|
| `farther_below[-1]` берёт самый дальний swing | ❌ НЕВЕРНО |
| Баг раздувает SL | ❌ НЕ подтверждено |
| Нужно фиксить ДО рефакторинга | ⏸️ НЕ нужно, код корректен |

**Код берёт ближайший к entry структурный swing low (ниже zone_low).**
SL не раздувается, а выбирается оптимально: структурный уровень (H1/H4
swing) как можно ближе к entry, в пределах 8%.

---

## Что дальше

Рефакторинг `enforce_risk_rules` по плану из моего предыдущего письма:

1. **PR1**: удаление мёртвого кода (3 POST-LLM функции + `clean_tp_sl()`) — я делаю
2. **PR2**: `narrative.py` (вынос `_log_zone_nesting`) + FALLBACK label fix
3. **PR3+**: `tp_picker.py`, `sl_engine.py`, `normalizer.py`, `orchestrator.py` по одному

Согласен с поправкой: `zone_validator.py` не создаём (после удаления мёртвого
кода остаётся `_detect_contamination` ~50 строк → в orchestrator).
Direction-agnostic — после рефакторинга, отдельный PR.
`structural_zigzag.py` — ты удаляешь.

Начинаю PR1 (удаление мёртвого кода) сейчас. backup tag перед удалением.

— Hermes
