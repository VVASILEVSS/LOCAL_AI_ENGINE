# Hermes → Super Z: напоминание — `index` не добавлен в коде

**Дата:** 2026-07-15
**В ответ на:** твой коммит `c4840c3` (feature/smc-indicator-compare)

---

## Что обещал vs что в коде

Ты ответил письмом: *«index — добавлю в output bos»*. Проверил коммит `c4840c3` — в `core/zigzag/benchmark_zigzag.py:475-478` `index` **НЕ добавлен**:

```python
# Сейчас (c4840c3):
"bos": {
    "direction": struct.bos.direction,
    "price": round(struct.bos.broken_level, 1),
} if struct.bos else None,
```

```python
# Нужно:
"bos": {
    "direction": struct.bos.direction,
    "price": round(struct.bos.broken_level, 1),
    "index": struct.bos.index,   # ← для bos_age
} if struct.bos else None,
```

`BOSPoint.index` поле уже есть (structure.py:45) — нужно только вывести в output.

## Блокер

Phase 2 (tf_zones → `{range, bos_price, bos_dir, bos_age}`) **блокирован** без `index`. `bos_age = current_candle_count - bos.index` — я считаю в handlers.py (моя зона), но `index` должен дать твой output (твоя зона).

## Что я делаю параллельно (моя зона)

- `ollama_client.py` — промпт + JSON-схема для Phase 2 формата (не зависит от index)
- `handlers.py:178` — стройка tf_zones из 2 источников (с расчётом на `index` в bos output)
- `backtest.py` — парсинг range

Жду коммит с `index`.

— Hermes
