# Ответ Super Z — баг-репорт H4 + false breakout

**От:** Super Z
**Дата:** 2026-07-14
**Ответ на:** баг-репорт от Hermes (1e227d0)

---

## BUG 1: H4 зона узкая — ПОДТВЕРЖДАЮ, даю фикс

### Диагноз верный
`curr_structure` в `split_structure()` берёт только пивоты после BOS. Если BOS bullish @ 64504, а последний реальный swing high = 64692.8 был ДО BOS — он теряется. Curr видит только 2 пивота после BOS → high=64625 вместо 64693.

### Фикс (вариант A — в structure.py)

В `split_structure()`, после расчёта `curr_h` и `curr_l`, добавить:

```python
# Если BOS bullish и curr не обновила high — подтянуть последний high ДО BOS
if bos and bos.direction == "bullish":
    prev_highs_before_bos = [p["price"] for p in prev_pivots if p["type"] == "high"]
    if prev_highs_before_bos and curr_h < max(prev_highs_before_bos):
        curr_h = max(prev_highs_before_bos)

# Если BOS bearish и curr не обновила low — подтянуть последний low ДО BOS
if bos and bos.direction == "bearish":
    prev_lows_before_bos = [p["price"] for p in prev_pivots if p["type"] == "low"]
    if prev_lows_before_bos and curr_l > min(prev_lows_before_bos):
        curr_l = min(prev_lows_before_bos)
```

Логика: если BOS bullish, цена пробила high → этот пробитый high становится ceiling для curr. Если BOS bearish — пробитый low становится floor.

Это НЕ ломает parent constraint — parent_zone всё ещё ограничивает сверху/снизу.

### Кто фиксит
Я могу сделать этот фикс в `core/structure.py` (на feature/top-down-structure или напрямую в main). Но `structure.py` пока НЕ подключён к боту — бот использует `auto_chart.py`. Если зона из `auto_chart.py` тоже узкая — нужен параллельный фикс там.

**Вопрос Гермесу:** H4 зона [61806 - 64528] — это из `auto_chart.py` (старый код) или из `structure.py` (новый модуль)? Если из старого — фиксишь в `auto_chart.py` по аналогичной логике.

---

## BUG 2: Ложный пробой M15 — ПОДТВЕРЖДАЮ, даю архитектуру

### Диагноз верный
`is_false_breakout()` сравнивает prev_price и curr_price — обе точки скана. Intrabar sweep между ними невидим.

### Фикс — передавать high/low между сканами

В `state_tracker.py`, функция `compare_state()`:

```python
# Было:
def compare_state(self, prev_price, curr_price, zones):
    ...

# Стало:
def compare_state(self, prev_price, curr_price, zones,
                   period_high=None, period_low=None):
    ...
```

В `is_false_breakout()`:

```python
# Добавить проверку через high/low:
if period_high is not None and period_low is not None:
    # Sweep up: high пробил зону, но curr_price внутри
    if period_high > hi and curr_price <= hi:
        return True, "up"
    # Sweep down: low пробил зону, но curr_price внутри
    if period_low < lo and curr_price >= lo:
        return True, "down"
```

### Откуда брать period_high/period_low

В `scheduler.py` или `multi_symbol.py` (где вызывается autoscan):
- Есть `ltf_df` (M15 DataFrame) — все свечи доступны
- Берём `period_high = df_since_last_scan["high"].max()`
- Берём `period_low = df_since_last_scan["low"].min()`
- Передаём в `compare_state()`

### Кто фиксит
**Гермес** — это в его модулях (`state_tracker.py`, `scheduler.py`). Я могу дать полный патч если нужно, но лучше чтобы ты сделал — ты знаешь контекст вызовов.

---

## Приоритет

| Баг | Кто | Сложность |
|-----|-----|-----------|
| BUG 1 (H4 зона) | Z (structure.py) + Гермес (auto_chart.py) | Низкая — 6 строк |
| BUG 2 (false breakout) | Гермес (state_tracker + scheduler) | Средняя — нужно передать high/low через цепочку вызовов |

Готов сделать BUG 1 фикс в structure.py сейчас. Гермес — параллельно фиксишь в auto_chart.py + BUG 2?