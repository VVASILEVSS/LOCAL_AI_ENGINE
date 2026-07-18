# Ответ Z: Variant E Phase 1 + BTC 1H fix (N=6)

## 0. Коммит: Variant E Phase 1

Три файла изменены:

### ollama_client.py — tf_zones убран из JSON schema
- Убран `tf_zones` из примеров 1 и 2 (LLM не видит пример)
- Убран `tf_zones` из JSON template
- Инструкция 8 → Variant E note: "НЕ возвращай tf_zones, только tf_zones_comment"
- Инструкции 8a/8b/8c удалены (LLM больше не формирует zones)
- Правило 26 упрощено (zones authoritative из ZigZag)
- Правило 27 удалено (verify against zigzag — не нужно, LLM не возвращает)
- Нормализация tf_zones (line ~399) ОСТАВЛЕНА — обратная совместимость

### scheduler.py — zones из zigzag_context (structure.py) напрямую
**Порядок приоритета:**
1. `zigzag_context["timeframes"][TF]["upper/lower"]` = structure.py output (Variant D v2)
2. `tf_zones` из metrics (auto_chart) — fallback если zigzag пуст

LLM-зоны (`llm_zones`) больше не перезаписывают tf_zones_clean.
`parsed["tf_zones"]` = чистый structure.py output.

### structure.py — BTC 1H fix
`_TF_SWING_N["1h"]`: 5 → 6. BTC 1H N=5 дал 0.54% (не покрыл swing 64388).
N=6 должен захватить нужный swing.

## 1. Что меняется в data flow

**До Phase 1:**
```
structure.py → zigzag_context → LLM видит → LLM возвращает tf_zones →
scheduler переписывает tf_zones_clean из LLM → POST-LLM clamps
```

**После Phase 1:**
```
structure.py → zigzag_context["timeframes"][TF]["upper/lower"] →
scheduler берёт напрямую → tf_zones_clean (POST-LLM OFF)
```

LLM видит zones через `prev_ctx.zigzag_context` (для комментариев),
но НЕ возвращает их в JSON.

## 2. 5M зоны

Теперь 5M будет работать:
- structure.py вычисляет zone для 5M (N=8 swings)
- zigzag_context содержит 5M upper/lower
- scheduler.py берёт напрямую → tf_zones_clean["5M"]
- LLM не перезаписывает → 5M zone passes through

## 3. Ожидание после перезапуска

| Сим | TF | было (v2) | ожидание (Phase 1 + N=6) | цель |
|-----|-----|---------|---------|------|
| BTC | 1H | 0.54% | **0.8-1.5%** (N=6 + zigzag direct) | 1-2% ✅ |
| BTC | 15M | 0.55% | **0.8-1.2%** (zigzag direct) | 0.5-1.5% ✅ |
| ETH | 15M | 0.99% | **~1%** (zigzag direct) | 0.5-1.5% ✅ |
| 5M | — | НЕТ | **должен появиться** | — |

Все TF теперь идут напрямую из structure.py без LLM перезаписи.

## 4. POST-LLM clamps

Остаются закомментированными (commit bdec6bb). При Phase 2 — полное удаление.

— Z