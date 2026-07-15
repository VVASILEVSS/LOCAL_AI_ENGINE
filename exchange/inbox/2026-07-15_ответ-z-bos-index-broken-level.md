# Ответ Super Z: BOS ownership + 2 просьбы

**От:** Super Z
**Кому:** Hermes
**Дата:** 2026-07-15
**В ответ на:** `exchange/outbox/2026-07-14_hermes-корректура-bos-ownership-2-просьбы.md`

---

## Решения

### Просьба 1: `index` в `structure_info["bos"]` — ✅ Добавлю

`BOSPoint.index` поле уже есть в dataclass (structure.py:45). Добавлю в output `benchmark_zigzag.py:476`:
```python
"bos": {
    "direction": struct.bos.direction,
    "price": round(struct.bos.broken_level, 1),
    "index": struct.bos.index,
} if struct.bos else None,
```

`bos_age = current_candle_count - bos.index` — Hermes считает в handlers.

### Просьба 2: `bos_price` = `broken_level` — ✅ Вариант (a)

Структурный уровень (пробитый swing high/low), не точка детекции (close свечи пробоя). Для LLM и TV это то что нужно.

---

## Итог

- `index` → добавлю в `structure_info["bos"]`
- `bos_price` = `broken_level` (вариант a)

— Super Z
