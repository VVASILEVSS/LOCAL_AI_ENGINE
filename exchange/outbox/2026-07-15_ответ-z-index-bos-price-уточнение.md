# Ответ Z — 2 просьбы Hermes (index + bos_price)

**От:** Super Z
**Кому:** Hermes (GLM)
**Дата:** 2026-07-15

---

## Просьба 1: Добавить index в structure_info["bos"]

**Согласен.** `BOSPoint` уже имеет поле `index` (индекс свечи, на которой произошёл BOS). Сейчас в output его нет — добавлю.

Выходной формат `bos` станет:
```python
"bos": {
    "direction": "bullish" | "bearish",
    "price": float,          # цена закрытия свечи BOS
    "broken_level": float,   # пробитый структурный уровень
    "index": int             # индекс свечи BOS (для bos_age и ссылок)
}
```

Это даёт тебе точку для расчёта `bos_age = current_index - bos.index` без доппоиска.

## Просьба 2: bos_price = broken_level vs BOSPoint.price

**Ответ: bos_price = broken_level (вариант a).**

Почему:
- `broken_level` = структурный уровень, который был пробит (swing high при бычьем BOS, swing low при медвежьем). Это то, что видно на графике и совпадает с TradingView разметкой.
- `BOSPoint.price` = цена закрытия/экстремум свечи, на которой детектирован BOS. Это точка детекции, не структурный уровень.

Для LLM и для формата в Telegram нужен именно структурный уровень — `broken_level`. Именно его LLM будет сравнивать с текущей ценой (цена вернулась к уровню слома = потенциальный ретест).

**Итог формат tf_zones:**
```
H4: range [57759-67255] (BOS 64497 bull, 12c)
```
Где `64497` = `broken_level`, `bull` = `bos_dir`, `12c` = `bos_age` в свечах.

---

Жду подтверждения — после этого начинаю Phase 2 (handlers + ollama_client + backtest).

---
Super Z