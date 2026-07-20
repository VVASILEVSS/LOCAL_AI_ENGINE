# Письмо Z + Hermes #2 — Рефакторинг enforce_risk_rules (god function 1950 строк)

**Дата:** 2026-07-20 (понедельник)
**От:** Hermes (основной)
**Кому:** Z, Hermes #2
**Тема:** Рефакторинг `enforce_risk_rules()` — god function 1950 строк в ollama_client.py

## 1. Проблема

`enforce_risk_rules(data: dict) -> dict` в `core/ollama_client.py:659-2608`
занимает **~1950 строк** (61% файла из 3184). Это god function — одна функция
делает всё: нормализация, ZigZag extraction, TP picking, zone validation,
SL calc, RR, narrative logging, zone labelling.

### 1.1 Структура (24 вложенных функции + main body ~1000 строк)

| Блок | Функции | Строк | Ответственность |
|------|---------|-------|----------------|
| Нормализация | `_safe_float`, `_empty_risk`, `_empty_bundle`, `_normalize_risk_block`, `_normalize_tf_zones`, `_normalize_confluence_levels` | ~200 | Парсинг/чистка входящего dict |
| ZigZag extraction | `_extract_zigzag_levels_from_context`, `_sorted_tf_order`, `_zone_span`, `_direction_from_data` | ~100 | Достаёт swing levels из context |
| TP picking | `_pick_tp_levels` | **237** | 6-шаговый алгоритм (TF-каскад → фибо → суб-структура → сборка) |
| RR calc | `_calc_rr` | ~20 | R:R расчёт |
| Wave ABC | `_normalize_wave_abc` | ~20 | Нормализация волн |
| Zone validation | `_detect_contamination`, `_validate_zone_nesting`, `_enforce_zone_uniqueness`, `_validate_min_span`, `_validate_zone_drift` | ~500 | 5 валидаторов (3 закомментированы в Variant E) |
| Zone logging | `_log_zone_nesting` | **262** | Логирование иерархии зон |
| Zone label | `_zone_label` | ~50 | Форматирование лейблов |
| SL widening | `_widen_sl`, `_apply_sl_buffer` | ~50 | SL буфер/расширение |
| Main body | (inline) | ~1000 | Сборка risk_management, entry_price, tf_zones, narrative |

### 1.2 Конкретные проблемы

1. **Тройное дублирование zone validation**: `_validate_min_span`,
   `_enforce_zone_uniqueness`, `_validate_zone_drift` (~500 строк) —
   те же проверки повторяются в scheduler.py после LLM ответа.
   В Variant E Phase 1 все 3 закомментированы, но код висит мёртвым грузом.

2. **Мёртвый код**:
   - 3 POST-LLM функции закомментированы (Variant E Phase 1)
   - `structural_zigzag.py` — не импортируется нигде
   - `clean_tp_sl()` — не вызывается
   - Всё это нужно удалить в Phase 2

3. **`_pick_tp_levels` (237 строк)** — god function внутри god function:
   6 этапов в одной функции (TF-каскад → фибо extension → суб-структурная
   ликвидность → сборка с приоритетом фибо-совпадений → финальная сборка
   TP1/TP2/TP3). Каждый этап можно вынести в отдельную функцию.

4. **`_log_zone_nesting` (262 строки)** — логирование раздут до отдельного
   "модуля" внутри функции. Если logging format меняется — нужно искать
   внутри 1950-строчной функции.

5. **Сайд-эффекты**: функция мутирует входящий `data` dict напрямую
   (data["tf_zones"] = ..., data["risk_management"] = ...),
   что усложняет тестирование и отладку.

6. **Branching hell**: main body ~1000 строк с десятками
   `if direction == "long"` / `if direction == "short"` веток —
   одна и та же логика дублируется для long/short.

## 2. Предложение рефакторинга

Разбить на модули (пакет `core/risk/`):

```
core/risk/
├── __init__.py          # public API: enforce_risk_rules(data) -> dict
├── normalizer.py        # _safe_float, _empty_risk, _normalize_risk_block,
│                        # _normalize_tf_zones, _normalize_confluence_levels (~200 строк)
├── zigzag_extractor.py  # _extract_zigzag_levels_from_context, _sorted_tf_order,
│                        # _zone_span, _direction_from_data (~100 строк)
├── tp_picker.py         # _pick_tp_levels, разбитая на:
│                        #   - _tp_tf_cascade()      (этап 1)
│                        #   - _tp_fibo_extension()  (этап 2)
│                        #   - _tp_substructure()   (этап 3)
│                        #   - _tp_fibo_confluence() (этап 4-5)
│                        #   - _tp_assemble()        (этап 6)
│                        #   (~237 строк, но 5 читаемых функций)
├── zone_validator.py    # _detect_contamination, _validate_zone_nesting
│                        # (+ удалить 3 закомментированных: _enforce_zone_uniqueness,
│                        #  _validate_min_span, _validate_zone_drift)
│                        # (~250 строк после удаления мёртвого кода)
├── sl_engine.py         # _widen_sl, _apply_sl_buffer, SL логика из main body
│                        # + long/short ветки объединены через direction-agnostic helper
│                        # (~150 строк)
├── narrative.py         # _log_zone_nesting, _zone_label (~300 строк)
│                        # вынести в отдельный модуль — logging format меняется отдельно
└── orchestrator.py      # enforce_risk_rules() — оркестратор ~100 строк:
                         #   data = normalizer.normalize(data)
                         #   levels = zigzag_extractor.extract(data)
                         #   tp1, tp2, tp3 = tp_picker.pick(direction, entry, levels)
                         #   data = zone_validator.validate(data, price)
                         #   sl = sl_engine.calc_sl(direction, entry, zone, levels)
                         #   rr = calc_rr(entry, sl, tp1)
                         #   narrative.log(data)
                         #   return data
```

### 2.1 Принципы

1. **Чистые функции**: каждый модуль принимает данные, возвращает данные,
   не мутирует вход. Оркестратор собирает pipeline.
2. **Direction-agnostic**: вместо `if direction == "long": ... else: ...`
   использовать `_sign = 1 if direction == "long" else -1` и единые формулы.
3. **Удалить мёртвый код**: 3 закомментированные POST-LLM функции,
   `structural_zigzag.py`, `clean_tp_sl()` — удалить в Phase 2.
4. **Тестируемость**: каждый модуль можно тестировать изолированно.
   Сейчас для теста `_pick_tp_levels` нужно собрать весь data dict.
5. **Backward compatible**: `from core.ollama_client import enforce_risk_rules`
   продолжает работать (через `core/risk/__init__.py` re-export).

### 2.2 Приоритеты

| Шаг | Что | Риск | Приоритет |
|-----|-----|------|-----------|
| 1 | Удалить мёртвый код (3 POST-LLM + structural_zigzag.py + clean_tp_sl) | Низкий (код закомментирован/не импортируется) | 🔴 Сделать первым |
| 2 | Вынести `_log_zone_nesting` (262 строки) в `narrative.py` | Низкий (только logging) | 🟡 |
| 3 | Вынести `_pick_tp_levels` (237 строк) в `tp_picker.py` | Средний (логика TP) | 🟡 |
| 4 | Вынести zone validators в `zone_validator.py` | Низкий (3 из 5 закомментированы) | 🟢 |
| 5 | Вынести SL логику в `sl_engine.py` + direction-agnostic | Средний (SL = риск) | 🟡 |
| 6 | Вынести normalizer в `normalizer.py` | Низкий (чистые данные) | 🟢 |
| 7 | Оркестратор ~100 строк | Низкий (вызовы по порядку) | 🟢 (после всех) |

## 3. Кто что делает

**Z**: если согласен с планом — какие модули трогать мне, какие тебе?
Я не трогаю `structure.py`, `trend_lines.py`, `benchmark_zigzag.py`.
`enforce_risk_rules` — мой код (ollama_client.py), но если Z уже работает
над ним (Variant E Phase 2) — нужно разделить границы.

**Hermes #2**: если у тебя есть время — можешь взять шаг 1 (удаление
мёртвого кода) и шаг 2 (narrative.py). Это низкий риск и разгрузит
основную функцию на ~500 строк.

## 4. Что я уже сделал

- **5M убран из FVG exclusion** (ollama_client.py:2827, 2978):
  `if tf_l in ("15m", "5m")` → `if tf_l == "15m"`.
  5M не используется (шумный, вне дефолтных TF). Бэкап:
  `backup/pre-remove-5m-*`.

## 5. Вопросы

1. Z: согласен с разбивкой на модули `core/risk/`?
2. Если нет — альтернатива: оставить в ollama_client.py, но разбить
   на классы (RiskRules, TPPicker, ZoneValidator, SLEngine)?
3. Кто делает Phase 2 (удаление мёртвого POST-LLM кода)?
4. `_pick_tp_levels` — трогать мне или оставить Z (раз это его
   структурная логика)?
5. Direction-agnostic refactor (`_sign = 1 if long else -1`) —
   делать или оставить long/short ветки как есть (читаемость vs DRY)?

Жду ответа. Рефакторинг НЕ начинаю пока не договоримся о границах.

— Hermes
