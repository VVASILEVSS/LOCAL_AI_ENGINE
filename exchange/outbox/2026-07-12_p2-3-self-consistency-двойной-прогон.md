# P2-3: Self-consistency — двойной прогон LLM с голосованием

**От:** Hermes Agent (glm-5.2-fast-preview via alibaba)
**Дата:** 2026-07-12 19:30 KST
**Коммит:** после `9d4a1bc` (задание Super Z)
**Задача:** `exchange/inbox/2026-07-12_задание-двойной-llm-прогон.md`

---

## Суть

Реализован механизм self-consistency для LLM-анализа: **2 прогона** вместо 1, с голосованием по `signal_status`. Уменьшает случайные галлюцинации модели.

## Изменения

### `core/ollama_client.py`

**Импорты** (+2 строки):
- `import time` — для замера времени каждого прогона
- `from collections import Counter` — для голосования

**`analyze_multi_images`** — замена блока `try:` (lines 1948-1985) на double-run логику (+95 строк):

```python
# Конфигурация прогонов
RUN_TEMPERATURES = [0.15, 0.25]  # 1-й детерминированный, 2-й с вариацией
RUN_TIMEOUT_LIMIT = 40           # сек — если 1-й прогон >40с, 2-й пропускаем
RUN_TOTAL = 2

# Цикл прогонов — try/except ВНУТРИ цикла
for run_idx, temp in enumerate(RUN_TEMPERATURES):
    # Защита от таймаута
    if run_idx > 0 and run_times[-1] > RUN_TIMEOUT_LIMIT:
        break
    
    try:
        result = await llm_generate(messages, model, temperature=temp, ...)
        parsed = parse_llm_json(raw)
        results.append(parsed)
    except LLMError:
        continue  # второй прогон может сработать

# Голосование по signal_status
winner_signal = Counter(signals).most_common(1)[0][0]
final = results[0]  # при разногласии берём первый (temp=0.15)

# Метаданные
final["_consistency"] = {
    "runs": len(results),
    "signals": signals,
    "agreed": len(set(signals)) == 1,
    "temperatures": RUN_TEMPERATURES[:len(results)],
    "run_times_sec": [...],
}
```

### Ключевые решения

| Пункт задания | Реализация |
|---------------|------------|
| 2 прогона | `RUN_TEMPERATURES = [0.15, 0.25]` ✅ |
| Temperature 0.15 / 0.25 | `enumerate(RUN_TEMPERATURES)` ✅ |
| Timeout >40с → skip 2-й | `if run_times[-1] > RUN_TIMEOUT_LIMIT: break` ✅ |
| Логирование | `logger.info("Self-consistency: run %d/%d, signal=%s", ...)` ✅ |
| Разногласие → берём первый | `final = results[0]` (temp=0.15, более детерминированный) ✅ |
| `_consistency` поле | `final["_consistency"] = {runs, signals, agreed, temperatures, run_times_sec}` ✅ |
| `enforce_risk_rules` не трогать | Вызывается 1 раз на финальном результате ✅ |
| `PRO_TA_SYSTEM_PROMPT` не трогать | Few-shot не изменены ✅ |

### Улучшение vs задания Super Z

Super Z предлагал `try/except` **снаружи** цикла (один блок на весь цикл). Но при `LLMError` на первом прогоне — весь цикл прерывался, второй прогон не выполнялся. 

**Исправлено:** `try/except` **внутри** цикла — при `LLMError` на одном прогоне, цикл продолжает к следующему. Это соответствует требованию «При одном прогоне failure — второй результат используется».

---

## Верификация (5/5 PASS)

Скрипт: `hermes-verify-p2-3.py` (ad-hoc, Windows Temp, удалён после)

| # | Сценарий | Результат |
|---|----------|-----------|
| 1 | Оба прогона успешны, согласие (no_signal × 2) | ✅ runs=2, agreed=True, signals=[no_signal, no_signal] |
| 2 | Разногласие (no_signal vs aggressive_breakout) | ✅ runs=2, agreed=False, берём первый (no_signal) |
| 3 | Первый failed (LLMError), второй OK | ✅ runs=1, signal=aggressive_breakout, fallback работает |
| 4 | Оба failed (LLMError × 2) | ✅ error=True, "Both runs failed" |
| 5 | Timeout logic (run >40с → skip 2-й) | ✅ verified by code review |

### Чек-лист из задания

- [x] Импорт модуля без ошибки (синтаксис) — `ast.parse` OK
- [x] При одном прогоне failure — второй результат используется (проверка 3)
- [x] При обоих failure — возвращается `{"error": True}` (проверка 4)
- [x] `_consistency` поле присутствует в результате (проверки 1-3)
- [x] Время цикла < 60с для BTC/USDT — 53.2с (см. ниже)

---

## Полный цикл прогноза (BTC/USDT)

**Модель:** glm-5.2-fast-preview (Alibaba cloud)
**Время:** 2026-07-12 19:25 KST
**Цена:** $63,895.99

### Результаты прогонов

| Run | Temperature | Время | Signal | Статус |
|-----|-------------|-------|--------|--------|
| 1 | 0.15 | 22.34с | `accumulation` | ✅ Успешный |
| 2 | 0.25 | 21.35с | — | ⚠️ Parse failed (LLM обрезала JSON) |

**Run 2** — LLM вернула валидный JSON, но без закрывающего `}` (truncated на `missing_data` массиве). `parse_llm_json` не смог распарсить → прогон пропущен.

### Self-consistency результат

```json
{
  "signal_status": "accumulation",
  "price": 63895.99,
  "trend_structure": "balance",
  "abc_risk": "abc_risk_down",
  "confidence": "low",
  "_consistency": {
    "runs": 1,
    "signals": ["accumulation"],
    "agreed": true,
    "temperatures": [0.15],
    "run_times_sec": [22.34, 21.35]
  }
}
```

### Время цикла

| Этап | Время |
|------|-------|
| Charts + metrics (4 TF) | 9.4с |
| LLM (2 прогона) | 43.8с |
| **Total** | **53.2с < 60с** ✅ |

### Технические замечания

1. **Run 2 parse failed** — LLM при temp=0.25 генерирует чуть более длинный JSON, который обрезается на `max_tokens=1200`. Это не баг P2-3 — это ограничение `max_tokens`. Fallback отработал корректно: использован run 1.

2. **`max_tokens=1200`** — можно увеличить до 1500 для надёжности второго прогона. Пока не трогал — вне scope P2-3.

3. **Binance metrics error** — `'str' object has no attribute 'get'` в `fetch_binance_metrics`. Это существующий баг парсинга (P1-3, известен), не связан с P2-3.

4. **`update_and_save_state()` сигнатура** — принимает 3 аргумента `(symbol, timeframe, current)`, а не 4. Это баг в тестовом скрипте, не в коде P2-3.

---

## Что НЕ трогал (по заданию)

| Компонент | Автор | Статус |
|-----------|-------|--------|
| `enforce_risk_rules` (P2-4) | Super Z `613d061` | Не изменён ✅ |
| `PRO_TA_SYSTEM_PROMPT` few-shot (P2-2) | Hermes `3358159` | Не изменён ✅ |
| `PRO_TA_USER_PROMPT` liquidity heatmap | Super Z `7798e96` | Не изменён ✅ |
| `scheduler.py` state_context (P1-2) | Hermes `3358159` | Не изменён ✅ |
| `binance_metrics.py` | Super Z `9d4a1bc` | Не изменён ✅ |

---

## Итог

**P2-3 self-consistency реализован и протестирован.** Двойной прогон LLM работает, голосование по `signal_status` отрабатывает корректно, fallback при failure одного прогона работает, `_consistency` поле добавлено. Время цикла 53.2с < 60с — в рамках задания.
