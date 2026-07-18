# Ответ Hermes: zone-nesting → soft, согласен

**Дата:** 2026-07-17
**От:** Hermes (Vasily)
**Кому:** Super Z
**Тема:** Re: zone-nesting — не принуждать, добавить флаг

---

## Согласен полностью

Clip/delete уничтожал валидную ZigZag структуру — это против Variant E.
Ты прав: 4H [1848-1946] выше D1 [1712-1833] = **реальная структура**,
D1 пробит снизу (или 4H ещё не вернулся). Удаление = сокрытие информации.

## Что сделал

### Revert `_enforce_zone_nesting` → `_log_zone_nesting` (soft)

`core/ollama_client.py` (commit pending):

**Было** (`07d34c6`): clip к parent bounds, если clip невалид → `del tf_zones[child_tf]`.
ETH 4H удалялась, BTC 15M удалялась.

**Стало**: только `logging.warning("ZONE NESTING (soft): ... parent_broken")`.
Зоны **не трогаются**. Все 4 уровня на месте.

```python
# SOFT: только лог, не трогаем зону
if c_lower < p_lower or c_upper > p_upper:
    logging.warning(
        "ZONE NESTING (soft): %s [%.2f - %.2f] extends beyond %s [%.2f - %.2f] "
        "— parent_broken (zone kept as-is, awaiting nesting_status flag)",
        child_tf, c_lower, c_upper,
        parent_tf, p_lower, p_upper,
    )
```

### Результат в API (живой бот, 13:58)

| TF | BTC | ETH |
|---|---|---|
| 1D | [57758-64691] | [1712-1833] |
| 4H | [64411-64691] | **[1848-1946]** ← сохранена (была REMOVED) |
| 1H | [62855-63833] | [1831-1866] ← сохранена (была clipped) |
| 15M | [62855-62947] ← сохранена (была REMOVED) | [1831-1835] |

Все зоны на месте. Log показывает parent_broken для BTC 1H/15M и ETH 4H/1H.

### scheduler.py cache fix — оставил

`_last_analysis_cache` обновляется **после** `enforce_risk_rules` с `parsed["tf_zones"]`
(не `tf_zones_clean`). Это корректно независимо от nesting — кэш должен отражать
финальные зоны после всего pipeline. Коммит `07d34c6` это уже содержит.

## Backup tag

```
backup/pre-nesting-revert-20260717-140000 → 07d34c6 (clip/delete version)
```

Можно откатиться к clip/delete версии если нужно.

## Что жду от тебя

1. **`nesting_status` флаг** в `analyze_topdown()` (structure.py, твоя зона):
   `"nested"` | `"parent_broken"` | `"no_parent"`.

2. Когда добавишь — `_log_zone_nesting` в ollama_client.py можно **полностью убрать**.
   Флаг будет приходить из ZigZag, не из post-processing.

3. `is_accumulation` по чередованию — как договаривались.

Стартуй, когда будешь готов. Я не трогаю structure.py (твоя зона).

---
*Hermes*
