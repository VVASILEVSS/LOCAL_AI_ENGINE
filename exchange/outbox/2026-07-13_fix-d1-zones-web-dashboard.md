# Задание: исправить D1 зоны в web_dashboard.py

## Проблема

Super Z добавил `_fill_missing_tf_zones()` в `web_dashboard.py` (коммит 46a0012) — fallback для ТФ зон, которые LLM не возвращает. Функция берёт зоны из `all_metrics[tf]["zone"]` (расчётные данные графика) и вставляет в результат ПОСЛЕ `enforce_risk_rules()`.

Это вызвало три бага:

### Баг 1: D1 cap ±10% НЕ применяется к fallback-зонам

`_validate_zone_nesting()` в `enforce_risk_rules()` (ollama_client.py:876-924) ограничивает D1 зону ±10% от цены. Но `_fill_missing_tf_zones()` вставляет зоны ПОСЛЕ того как enforce_risk_rules уже отработал.

**Факты из скана 14:34:**
- BTC цена 63119, D1 upper = **97924** (+55%, должно быть макс ~69431)
- ETH цена 1786, D1 upper = **2148** (+20%, должно быть макс ~1965)
- XAUT цена 4069, D1 lower = **4367** (+7.3% ВЫШЕ цены, вся зона выше цены)

### Баг 2: D1 в конце списка вместо начала

`_fill_missing_tf_zones()` итерирует `timeframes` (порядок: 15m, 1h, 4h, 1D) и добавляет в dict. В Python 3.7+ dict сохраняет порядок вставки → D1 оказывается последним.

**Ожидаемый порядок:** D1 → H4 → H1 → M15 (от старшего к младшему)

### Баг 3: Группировка ТФ через '/' убрана — ОК

`format_json_for_tg()` в ollama_client.py теперь показывает каждый ТФ отдельно — это ПРАВИЛЬНО, оставлять как есть.

## Что нужно сделать

### Вариант A (рекомендуемый): удалить `_fill_missing_tf_zones()` и исправить корень

1. **Удалить** `_fill_missing_tf_zones()` из `web_dashboard.py` и вызов на строке ~418
2. **Исправить причину**: LLM (GLM) не возвращает D1 зону. Варианты:
   - Добавить в промпт (`PRO_TA_SYSTEM_PROMPT` или `PRO_TA_USER_PROMPT`) жёсткое требование: "ОБЯЗАТЕЛЬНО верни зону для КАЖДОГО таймфрейма из запроса в tf_zones"
   - Или в `analyze_multi_images()` после `enforce_risk_rules()` — если запрошенного ТФ нет в tf_zones, а есть в prev_analysis["tf_zones"] — подставить оттуда (тогда cap и nesting применятся)

### Вариант B: если оставить fallback — исправить все три бага

1. В `_fill_missing_tf_zones()` — после вставки зоны из chart data, применить D1 cap ±10% вручную
2. В `_fill_missing_tf_zones()` — пересобрать tf_zones dict в правильном порядке (D1 → H4 → H1 → M15)
3. Пересчитать tf_span_map

## Файлы

- `web_dashboard.py` — функция `_fill_missing_tf_zones()` (строки ~315-365) и вызов (строка ~418)
- `core/ollama_client.py` — `enforce_risk_rules()` (строка 601), `_validate_zone_nesting()` (строка 876), `_normalize_tf_zones()` (строка 645), `format_json_for_tg()` (строка 1896 — уже исправлена, не трогать)
- `core/ollama_client.py` — промпты `PRO_TA_SYSTEM_PROMPT` (строка ~19) и `PRO_TA_USER_PROMPT` (строка ~300)

## Приоритет

Высокий — D1 зоны сейчас показывают некорректные данные, превышающие ±10% от цены.