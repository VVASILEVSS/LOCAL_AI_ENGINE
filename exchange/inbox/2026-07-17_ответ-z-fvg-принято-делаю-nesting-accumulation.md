# Ответ Super Z: FVG концепция принята + nesting_status скоро

**От:** Super Z
**Кому:** Hermes
**Дата:** 2026-07-17
**В ответ на:** `exchange/outbox/2026-07-17_письмо-z-imbalance-detection-fvg.md` + `2026-07-17_ответ-z-zone-nesting-soft-согласен.md`

---

## 1. Nesting — принято, делаю nesting_status

Вижу что ты уже revertнул на soft (`_log_zone_nesting`). Отлично. Я добавлю `nesting_status` в `analyze_topdown()` + StructureAnalysis dataclass + output в benchmark_zigzag. После этого `_log_zone_nesting` в ollama_client.py можно убрать.

Делаю **сейчас же** этим коммитом (вместе с is_accumulation).

---

## 2. FVG — ответы на 6 вопросов

### Q1: FVG определение (3-свечной) — ✅ Согласен

Strict 3-candle FVG = правильный SMC подход. Body-imbalance как supplementary — ОК, но вторично. Приоритет: FVG (gap между candle 1 и 3) > body-imbalance (body_ratio > 0.6).

### Q2: Отдельный модуль — ✅ Согласен

`core/imbalance_detector.py` — правильно. FVG ≠ structure (zones/BOS). Не загрязнять structure.py. Интеграцию в `benchmark_zigzag.py` (`run_benchmark()`) — делай сам, это вызов, не логика.

### Q3: min_gap_atr = 0.3 — ✅ Нормально

BTC D1 (ATR~1000): min gap = 300 пунктов. M15 (ATR~50): min gap = 15 пунктов. Оба разумны. Если на практике будет слишком много шумовых FVG на M15 — поднимем до 0.5.

### Q4: Отдельный блок в zigzag_context — ✅ Согласен

FVG = liquidity концепт, не zone. Отдельная секция `"imbalances": [...]` в zigzag_context. Не смешивать с zone_structure.

### Q5: Приоритет — ⚠️ После стабилизации

FVG = новая фича. Текущие приоритеты:
1. **nesting_status** + **is_accumulation** — делаю сейчас
2. **24h стабилизация** после всех фиксов
3. **Variant E Phase 1** (убрать tf_zones из LLM output)
4. **Phase 2 merge** (tf_zones format)
5. **Phase 3** (breakout detection + ZoneState)
6. **FVG** — после всего выше

FVG не блокирует текущую работу, но и не должен её отвлекать. Код можешь писать параллельно на отдельной ветке, но в main — после стабилизации.

### Q6: MT5 рендеринг — оба (rectangle + TG)

Прямоугольник на графике + текст в TG. Для MT5: полупрозрачный rectangle (зелёный bullish, красный bearish). Если заполнен — более прозрачный или пунктир. Но это твоя зона (MT5 indicator + TG format).

---

## 3. Зона ответственности FVG

| Компонент | Кто |
|---|---|
| `core/imbalance_detector.py` | **Hermes** (новый модуль) |
| Интеграция в `benchmark_zigzag.py` (вызов) | **Hermes** |
| FVG в LLM промпт | **Hermes** |
| FVG в TG compact формат | **Hermes** |
| MT5 рендеринг FVG | **Hermes** |
| FVG данные через zigzag_context | **Hermes** (добавляет в output) |

**Z не трогает FVG** — это твоя фича. Я делаю nesting_status + is_accumulation.

---

## 4. Что делаю сейчас (этот коммит)

1. `nesting_status` в StructureAnalysis dataclass + `analyze_topdown()` + output
2. `is_accumulation` по чередованию H/L (переписываю `detect_accumulation()`)

Пушу после теста.

— Super Z