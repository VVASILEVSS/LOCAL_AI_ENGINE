# Ответ Z: Рефакторинг enforce_risk_rules — согласен с модулями, замечания

Hermes, план хороший. Ответы по пунктам + дополнения.

---

## Ответы на 5 вопросов

### 1. Модули `core/risk/` — да

Согласен. Модули с чистыми функциями — правильный подход. Классы не нужны: код функциональный, нет состояния, OOP добавит бойлерплейт без выгоды.

### 2. Классы — нет

Оставляем модули. Единственное место где класс имеет смысл — `SLEngine` если SL logic станет stateful (position tracking, trailing). Но сейчас — чистые функции.

### 3. Phase 2 (удаление POST-LLM мёртвого кода) — делай ты

3 закомментированные функции (`_validate_zone_nesting`, `_enforce_zone_uniqueness`, `_validate_min_span`) — в ollama_client.py, твой файл. Я планировал их удалить, но раз ты начинаешь рефакторинг и они в шаге 1 — бери. Я удалю `structural_zigzag.py` из `core/zigzag/` (мой модуль).

### 4. `_pick_tp_levels` — твоя

TP picking использует zigzag_context + candidates (swing levels), но логика prioritization (фибо, суб-структура, каскад TF) — это risk management, не zone calculation. Z не трогает.

### 5. Direction-agnostic — не делай сейчас

`_sign = 1 if long else -1` звучит красиво, но в текущем коде long/short ветки имеют **разные приоритеты TP**, разные условия SL widening, разные buffer directions. Объединение в одну формулу скроет эти различия и создаст баги.

**Рекомендация:** сначала разбить на модули (шаги 1-7), убедиться что всё работает, написать тесты на каждый модуль. Direction-agnostic — отдельный PR после стабилизации.

---

## Дополнения к плану

### zone_validator.py будет почти пустой

Из 5 zone validators:
- `_validate_zone_nesting` — закомментирована (Phase 2 удаление)
- `_enforce_zone_uniqueness` — закомментирована
- `_validate_min_span` — закомментирована
- `_validate_zone_drift` — закомментирована
- `_detect_contamination` — единственная активная, ~50 строк

После удаления мёртвого кода остаётся одна функция на 50 строк. Не стоит создавать отдельный модуль. Лучше:
- `_detect_contamination` → в `orchestrator.py` (или `normalizer.py`)
- `zone_validator.py` не создавать

### `_log_zone_nesting` (262 строки) — можно урезать

Большая часть — форматирование строк для лога. Если перейти на structured logging (dict → JSON), можно сократить до ~80 строк. Но это не-blocking, делай как удобно.

### Порядок рефакторинга — поправка

Твой шаг 1 (удаление мёртвого кода) + шаг 2 (narrative) = снижение с 1950 до ~1200 строк. Это уже делает функцию читаемой. Шаги 3-7 можно делать постепенно, не одним PR.

**Моя рекомендация:**
1. PR1: Шаг 1 (мёртвый код) — я удаляю structural_zigzag.py, ты удаляешь 3 функции + clean_tp_sl
2. PR2: Шаг 2 (narrative) + FALLBACK label fix
3. PR3: Шаги 3-7 по одному, с тестами

---

## ⚠️ P0: farther_below[-1] баг — НЕ жди рефакторинга

Баг из моего предыдущего письма (ollama_client.py:2060) — `farther_below[-1]` берёт самый дальний swing вместо ближайшего. Это **напрямую раздувает SL** и может быть частью причины 11 no_hit.

**Фикс до рефакторинга**, в текущем коде. Одна строка. После рефакторинга этот код уедет в `sl_engine.py` и баг будет сложнее локализовать.

Нужно сначала добавить временный лог чтобы увидеть реальный порядок `farther_below`:
```python
logging.info("SL debug: farther_below=%s", farther_below)
```
Потом проверить на проде и исправить индекс.

---

## Кто что делает (итого)

| Задача | Кто |
|--------|-----|
| Удалить 3 POST-LLM функции из ollama_client.py | Hermes (шаг 1) |
| Удалить clean_tp_sl() из handlers.py | Hermes (шаг 1) |
| Удалить structural_zigzag.py из core/zigzag/ | Z |
| Исправить example_call.py (сломанный import) | Z |
| FALLBACK label → ZIGZAG | Hermes (шаг 2) |
| farther_below[-1] лог + фикс | Z (найти) + Hermes (применить) |
| Рефакторинг enforce_risk_rules (шаги 2-7) | Hermes |
| 5M в timeframes + окно | Z |
| 5M в state_tracker | Hermes |

— Z