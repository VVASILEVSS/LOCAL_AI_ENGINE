# Задание: fallback из ZigZag + исправить матрёшку

## Контекст

Предыдущее задание `2026-07-13_все-зоны-подтянуты-из-grafika.md` было частично выполнено в `a112f19` (все картинки в LLM ✅). Но fallback всё равно берёт сырые экстремумы. Super Z ошибся в задании — `prev_ctx["tf_zones"]` = `all_metrics[tf]["zone"]` = `get_structural_extremums()` = тот же мусор.

## Два бага для исправления

### Баг 1: Fallback = сырые экстремумы из get_structural_extremums()

**Цепочка**:
```
web_dashboard.py:408  tf_zones = {tf: all_metrics[tf]["zone"] for tf in timeframes}
                         ↑ all_metrics[tf]["zone"] = get_structural_extremums()
                         = max/min pivot за 120 свечей

web_dashboard.py:470  prev_ctx["tf_zones"] = tf_zones  ← ТЕ ЖЕ СЫРЫЕ

web_dashboard.py:499  _fill_missing_tf_zones(parsed_result, tf_zones, timeframes)
                         ↑ tf_zones = СЫРЫЕ ЭКСТРЕМУМЫ
```

**Доказательство**: BTC 4H финал [57758.60 - 67255.40] = точно ZigZag benchmark `upper=67255.40, lower=57758.60`. LLM вернул [61544.56 - 64692.83], но fallback ПЕРЕЗАПИСАЛ сырыми экстремумами.

**Решение**: Fallback должен брать зоны из **ZigZag benchmark**, не из `all_metrics[tf]["zone"]`.

ZigZag benchmark уже вызывается в `_do_full_scan()` (строки ~404-418) и результат в `zigzag_context["timeframes"]`. Формат:
```python
zigzag_context = {
    "timeframes": {
        "4h": {"upper": 67255.40, "lower": 57758.60, "current_price": ..., "levels": {...}},
        "1h": {...},
        "15m": {...},
        "1d": {...},
    }
}
```

**Что сделать в `_fill_missing_tf_zones()`**:
1. Добавить параметр `zigzag_timeframes: dict` (из `zigzag_context["timeframes"]`)
2. Для missing ТФ: взять `zigzag_timeframes[tf]["upper"]` и `zigzag_timeframes[tf]["lower"]`
3. Если в ZigZag нет зоны для ТФ — только тогда N/A (не подставлять `all_metrics`)

### Баг 2: Матрёшка расширяет parent вместо сужения child

**Код** `ollama_client.py:918-923`:
```python
# Если child lower < parent lower → расширяем parent lower
if c_lower is not None and p_lower is not None and c_lower < p_lower:
    parent["lower"] = c_lower  # ← НЕПРАВИЛЬНО
# Если child upper > parent upper → расширяем parent upper
if c_upper is not None and p_upper is not None and c_upper > parent_upper:
    parent["upper"] = c_upper  # ← НЕПРАВИЛЬНО
```

**Проблема**: Если LLM вернул H1=[1712-1848] шире чем D1=[1740-1800], матрёшка расширяет D1 до [1712-1848] = D1 сливается с H1.

**Доказательство**: ETH — LLM вернул 4h=[1713.44-1830], но матрёшка расширила его до [1712.50-1848] (= 1h зона).

**Решение**: Поменять логику на ОБРАТНУЮ:
```python
# Если child выходит за parent → СУЗИТЬ child до parent
if c_lower is not None and p_lower is not None and c_lower < p_lower:
    child["lower"] = p_lower  # сузить child
if c_upper is not None and p_upper is not None and c_upper > p_upper:
    child["upper"] = p_upper  # сузить child
```

Логика: старший ТФ (parent) должен быть АВТОРИТЕТНЫМ. Если child выходит за parent — child неправ, сужаем child.

## Файлы

1. **`web_dashboard.py`** — `_fill_missing_tf_zones()`:
   - Добавить параметр `zigzag_timeframes`
   - Брать missing зоны из ZigZag, не из `all_metrics`
   - Перед вызовом достать `zigzag_context["timeframes"]`

2. **`core/ollama_client.py`** — `_validate_zone_nesting()` (строки 900-924):
   - Поменять: вместо расширения parent → сужение child
   - Не трогать D1 cap (строки 890-898) — он работает правильно

3. **`web_dashboard.py`** — вызов `_fill_missing_tf_zones()` (строка ~499):
   - Передать `zigzag_context.get("timeframes", {})`

## Порядок важности

1. **Баг 2 (матрёшка)** — приоритет выше, потому что ломает даже те зоны что LLM вернул правильно
2. **Баг 1 (fallback)** — после бага 2, потому что без правильной матрёшки даже ZigZag зоны будут слиты

## Ожидаемый результат после фикса

| ТФ | BTC | ETH | XAUT |
|----|-----|-----|------|
| D1 | ZigZag fallback (если LLM не вернул) | ZigZag fallback | ZigZag fallback |
| H4 | LLM или ZigZag | LLM, НЕ расширен матрёшкой | LLM ✅ |
| H1 | LLM ✅ | LLM ✅ | LLM ✅ |
| M15 | LLM ✅ | LLM ✅ | LLM ✅ |

Ключевое: **никаких D1=H4 или D1=H4=H1**.