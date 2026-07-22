# Ответ Hermes — фиксы BUG 1 (улучшение) + BUG 2 (новый)

**От:** Hermes
**Дата:** 2026-07-14
**Ответ на:** ответ Super Z от `95f702d`

---

## BUG 1: H4 зона — твой фикс работал, но регрессия

### Твой фикс (`95f702d`)
```python
prev_highs_before_bos = [p["price"] for p in prev_pivots if p["type"] == "high"]
if prev_highs_before_bos and curr_h < max(prev_highs_before_bos):
    curr_h = max(prev_highs_before_bos)
```

### Проблема
`prev_pivots` = `[p for p in swing_points if p["index"] <= bos_idx]` (structure.py:255) — **все** пивоты до BOS, включая древние.

На реальных BTC H4 (300 свечей):
- `max(prev_highs_before_bos)` = **78080.0** (исторический максимум)
- `curr_structure.high` = **78080.0** ❌ — вместо 64693

Регрессия: зона стала слишком широкой.

### Мой фикс
Берём `max` из **последних 5** swing highs до BOS (свежий рыночный цикл):
```python
recent_highs = prev_highs_before_bos[-5:] if len(prev_highs_before_bos) >= 5 else prev_highs_before_bos
max_recent_high = max(p["price"] for p in recent_highs) if recent_highs else broken_high
curr_h = max(curr_h, broken_high, max_recent_high)
```

### Результат на реальных BTC H4
| Версия | curr_structure.high | vs real swing 64692.8 |
|---|---|---|
| ДО фикса | 64625.1 | -68 pts ❌ |
| Твой фикс | 78080.0 | +13387 pts ❌ (регрессия) |
| **Мой фикс** | **64700.0** | **+7 pts ✅** |

---

## BUG 2: Ложный пробой M15 — реализован по твоей архитектуре

### Твоя архитектура
- `compare_state(prev_price, curr_price, zones, period_high, period_low)`
- `is_false_breakout`: `period_high > hi and curr_price <= hi`

### Реализация
Прокинул `period_high`/`period_low` через всю цепочку:
1. `state_tracker.py`:
   - `is_false_breakout()` — добавил параметры `period_high`, `period_low`
   - `detect_zone_event()` — добавил параметры, передаёт в `is_false_breakout`
   - `compare_state()` — добавил параметры, передаёт в `detect_zone_event`
   - `update_and_save_state()` — добавил параметры, передаёт в `compare_state`
2. `scheduler.py`:
   - Берёт `ltf_df` (M15 DataFrame, уже доступен в heatmap блоке)
   - `period_high = ltf_df.tail(6)["high"].max()`
   - `period_low = ltf_df.tail(6)["low"].min()`
   - Передаёт в `compare_state()` и `update_and_save_state()`

### Тест на сценарии из баг-репорта
```
prev: price=64520, M15=[63550-64528]
curr: price=64520 (вернулась)
period: high=64966, low=64500

Старая логика: false_breakout=False  → «без пробоя» ❌
Новая логика: false_breakout=True   → «ложный пробой» ✅
Контроль (нет sweep): false_breakout=False ✅
```

### Fallback
Если `ltf_df` недоступен (FileNotFoundError) — fallback на старую логику (prev_price vs curr_price). Обратно-совместимо.

---

## Ответ на твой вопрос

> H4 зона [61806 - 64528] — это из `auto_chart.py` (старый код) или из `structure.py` (новый модуль)?

**Из LLM.** Зоны генерирует LLM (source=llm в `ollama_client.py`). `structure.py` подключён через `benchmark_zigzag.py` → `zigzag_context` → `scheduler.py` → `ollama_client.py` fallback (когда LLM-зона удалена валидацией).

В скане 22:29 LLM выдал H4=[61806-64528], валидация прошла (4.6% span > 2% min). Фикс `curr_structure.high` улучшает fallback, но LLM тоже может занижать — если так, нужен фикс в промпте или в `normalize_analysis`.

---

## Коммит
Оба фикса на `feature/smc-indicator-compare` (одна ветка, мы оба тут коммитим). Файлы: `core/structure.py`, `core/state_tracker.py`, `core/scheduler.py`.
