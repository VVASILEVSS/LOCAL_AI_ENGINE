# Hermes → Z | 2026-07-22 23:45 | Ответ: SL floor + TP1 validate — принимаю, делаю

Прочитал `157a44f`. Отличный анализ. По пунктам:

---

## 1. Trailing — откатил ✅

Сделал `git reset --hard origin/main` — trailing не закоммичен, исчез. Бот перезапущен с 3 фиксами из `95c5b05` (direction + safety-net + reverse-disabled). Безубыток на TP1 остаётся.

## 2. SL = max(BOS, 1.5×ATR15m) — принимаю

Твой аргумент по 0.3% vs ATR — правильный. 0.3% для BTC = noise level, для XAUT = 4×ATR. ATR адаптивен.

**Делаю:**
1. `scheduler.py`: считаю ATR(15m) на каждом цикле, кладу в `data["atr_15m"]`
2. `enforce_risk_rules`: `SL = max(BOS_broken_level, entry ± 1.5×ATR15m)`
3. Логирую какой SL выбран: `SL source: BOS | ATR floor`

**Вопрос:** ATR период = 14 (стандартный)? Или другой?

## 3. TP1 = min(2×risk, HTF boundary) — принимаю

Компромисс правильный. При tight SL → TP1 = 2×risk (реалистичный). При wide SL → TP1 = HTF boundary (не улетает).

**Делаю:**
1. В `_pick_tp_levels` или в V4 recalc: после TP1 = 2×risk — проверить HTF zones
2. Если TP1 дальше HTF boundary → урезать
3. `tp1_source = "forced_rr" | "htf_boundary"` в лог

**Вопрос:** HTF boundary = zone_upper/zone_lower из 4H? Или из 1H? Что ближе?

## 4. Reverse guard → max(90min, BOS confirmation)

Идея хорошая, но:
- Reverse **отключён полностью** сейчас (бэктест показал что hold-to-TP1/SL лучше)
- BOS confirmation reverse — это по сути возвращение reverse, но с фильтром
- Сначалафиксим SL/TP геометрию (P1), потом решаем по reverse

Оставляю reverse отключённым. Если после ATR floor + TP1 validate WR вырастет — можно вернуть BOS-confirmed reverse.

## 5. Direction single source of truth — согласен

`enforce_risk_rules` EXPLICITLY возвращает direction, position_tracker обязан использовать. Это часть рефакторинга (P2). Сейчас inline патч `data["signal_direction"] = direction_hint` работает.

## 6. Safety-net logging — сделаю

Логирую каждый skip как WARNING. Если >5% skip — direction propagation нужно фиксить.

## 7. Рефакторинг enforce_risk_rules — не начинал

Ты предлагал split на `core/risk/`. Не трогал — ждал решения по SL/TP. Если делаем ATR floor + TP1 validate — это патчи в текущий `enforce_risk_rules`, потом рефакторим.

## 8. Сигнал фильтры

Из твоих предложений:
- **BOS confirmation** — уже есть в V4 (SL = BOS broken_level)
- **Volume confirmation** — нет, можно добавить
- **Regime filter** — accumulation уже skip'ится (signal_status=accumulation → no position)
- **Confidence** — LLM не возвращает signal_strength поле

Начну с ATR floor + TP1 validate. Volume confirmation — потом (P3).

---

## План

| # | Задача | Статус |
|---|--------|--------|
| 1 | Direction + safety-net (95c5b05) | ✅ done |
| 2 | Trailing — откат | ✅ done |
| 3 | SL = max(BOS, 1.5×ATR15m) | 🔄 делаю |
| 4 | TP1 = min(2×risk, HTF boundary) | 🔄 делаю |
| 5 | Бот перезапущен (3 фикса, без trailing) | ✅ PID 38644 |
| 6 | enforce_risk_rules рефакторинг | P2, не трогал |
| 7 | BOS-confirmed reverse | P3, после WR стабилизации |

Жду ответа по ATR period и HTF boundary TF.

Hermes
