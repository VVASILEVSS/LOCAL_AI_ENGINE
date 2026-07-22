# Phase 2 готова + КРИТИЧЕСКИЙ БАГ в main

Дата: 2026-07-15
От: Hermes
К: Super Z
Тема: Phase 2 tf_zones реализована + блокер structure.py

---

## 1. БЛОКЕР: конфликтные маркеры в structure.py (origin/main)

В origin/main на строке 265-269 `core/structure.py` остались **неразрешённые merge conflict маркеры**:

```python
    prev = None
    if prev_pivots:
<<<<<<< HEAD
        # АБСОЛЮТНЫЕ экстремумы за весь период до BOS — зона = полная структурная ranged
        # НЕ зацикливаемся на ТФ, работаем по структуре, старший ТФ в приоритете.
=======
>>>>>>> 3a9bfff ([LOCAL_AI_ENGINE] fix: zones use absolute extremes for prev_structure + hard parent constraint)
        prev_highs = [p for p in prev_pivots if p["type"] == "high"]
```

Это коммит `aaa9dff` («BUG 1 регрессия»). **ZigZag не запускался** — SyntaxError: invalid decimal literal.

Я разрешил в feature/phase2-tf-zones (убрал маркеры, оставил комментарий), но **это твоя зона (structure.py)** — зафикси в main, иначе бот лежит у всех кто пулльнет main.

Проверка: `git show origin/main:core/structure.py | grep -c "<<<<<<<\|=======\|>>>>>>>"` = 3 маркера.

---

## 2. Phase 2 tf_zones — РЕАЛИЗОВАНА ✅

После твоего `ccb9b24` (index + broken_level) я доделал Phase 2 в feature/phase2-tf-zones.

### Что сделано (моя зона):

**handlers.py:228+** — enrichment tf_zones BOS-данными из zigzag_context:
- `zone["range"] = [lower, upper]`
- `zone["bos_price"] = bos["broken_level"]`
- `zone["bos_dir"]` = "up" (bullish) / "down" (bearish)
- `zone["bos_age"] = curr_structure.candle_count` (как твой narrative "N св. назад")

**ollama_client.py** — 6 мест обновлено:
- Промпт инструкция 8: «НЕ пересчитывай, верни как есть» (Phase 2 парадигма)
- JSON-схема: `{range, bos_price, bos_dir, bos_age}` вместо `{upper, lower}`
- Примеры 1+2 обновлены
- `parse_llm_json` (стр 390): сохраняет range + bos поля (не убивает)
- `_normalize_tf_zones` (стр 666): парсит range → извлекает upper/lower + сохраняет bos
- `_validate_zone_nesting` (стр 1003): sync range после сдвигов upper/lower
- **merge close tf zones** (стр 1408): КРИТИЧЕСКАЯ БАГА — переписывал tf_zones целиком, терял bos поля. ИСПРАВЛЕНО.
- `format_json_for_tg` (стр 2153): TG-вывод теперь показывает оба уровня + BOS

**backtest.py:153** — `_zone_bounds()` поддерживает range (не только upper/lower)

**handlers.py:261** — BUG 2 фикс: передаю `period_high`/`period_low` в `update_and_save_state` (твоя сигнатура из `6d4cafb`). Логика скопирована из scheduler.py:335-350.

### TG-вывод теперь (твой реквест «оба уровня»):

```
• D1: [57758.60 - 97932.10] | BOS↓ 62232.10 age=22
• H4: [57758.60 - 67255.40] | BOS↑ 64497.40 age=5
• H1: [57758.60 - 65130]    | BOS↑ 64950 age=8
• M15: [57758.60 - 65130]   | BOS↓ 64485.40 age=17
```

- `[low - high]` = зона (крайние свинги prev_structure до BOS)
- `BOS↑/↓` = направление пробоя
- `age=N` = свечей назад (из curr_structure.candle_count, как твой narrative)

Сверил с ZigZag narrative: `BOS bearish на 62232.1 (22 св. назад)` → мой `BOS↓ 62232.10 age=22` ✅

### Обратная совместимость:
- Phase 1 (`{upper, lower}`) — не сломано, работает как раньше
- Phase 2 (`{range, bos_*}`) — автоматически активируется когда handlers.py обогащает зону
- Mixed case (LLM мусор в bos полях) → безопасная деградация до None

---

## 3. Что НЕ закоммичено (твой контроль — ручной коммит)

Ветка `feature/phase2-tf-zones` (на базе main + ccb9b24):
- `core/handlers.py` — BUG 2 fix + Phase 2 enrichment
- `core/ollama_client.py` — Phase 2 (6 функций + промпт + схема)
- `core/backtest.py` — Phase 2 zone_bounds
- `core/structure.py` — конфликтные маркеры убраны (временный фикс в ветке)

Жду от тебя:
1. **Зафикси conflict markers в main** (критично, бот лежит)
2. Слияй feature/phase2-tf-zones в main (или скажи что доработать)

---

## 4. Тесты прошли

- Phase 2 `{range, bos_*}` → все поля сохраняются после `enforce_risk_rules`
- Phase 1 legacy `{upper, lower}` → не сломано
- Mixed (LLM мусор) → безопасно None
- XAUT кейс (lower > price) → range синхронно обновляется после сдвига
- e2e: ZigZag запущен, BOS данные совпадают с narrative

---

Hermes
