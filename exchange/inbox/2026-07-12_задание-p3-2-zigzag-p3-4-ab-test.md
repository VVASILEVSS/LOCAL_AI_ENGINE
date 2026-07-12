# Задание: ZigZag контекст + A/B тест промптов (P3-2 + P3-4)

**От:** Super Z
**Дата:** 2026-07-12
**Приоритет:** Низкий (P3, roadmap)
**Зависимости:** Требует актуального main (после a1e1588)

---

## ЧАСТЬ 1: P3-2 — ZigZag контекст в промпт

### Проблема
`zigzag_context = "{}"` — LLM не получает волновую разметку. `_build_zigzag_context()` в scheduler.py возвращает данные, но они недостаточно структурированы для LLM.

### Что сделать

1. В `core/zigzag/` — реализовать/улучшить `calculate_zigzag(df, depth=5, deviation=5)`:
   - Detect pivots (higher-highs, lower-lows)
   - Определять текущую волну (импульс/коррекция, направление)
   - Определять позицию в волне (начало/середина/завершение)
   
2. В `scheduler.py` — улучшить `_build_zigzag_context()`:
   - Добавить текстовое описание волны для LLM: `"ZigZag: 3-я волна вверх (импульс), позиция 60%. Последний pivot: 63200 (HH)."`
   - Форматировать для PRO_TA_USER_PROMPT, а не только как структурированный dict

3. В `core/ollama_client.py` — `PRO_TA_USER_PROMPT`:
   - Убедиться что `{zigzag_context}` в промпте используется эффективно
   - Если нужно — переформатировать вывод `_build_zigzag_context`

### Не трогать
- `core/backtest.py` — Super Z P3-1
- `core/binance_metrics.py` — Super Z OI multi-source
- `enforce_risk_rules` — Super Z P2-4

---

## ЧАСТЬ 2: P3-4 — A/B тест промптов

### Проблема
Нет механизма сравнения двух версий промпта. Мы меняем промпты, но не измеряем влияние на точность.

### Что сделать

1. В `core/config.py` — добавить настройку:
   ```python
   PROMPT_VARIANT = os.getenv("PROMPT_VARIANT", "A")  # "A" или "B"
   ```

2. В `core/ollama_client.py` — если `PROMPT_VARIANT == "B"`, использовать альтернативную версию `PRO_TA_SYSTEM_PROMPT` (можно начать с добавления/удаления few-shot, изменения порядка секций).

3. В `core/backtest.py` — `save_signal_log()` уже сохраняет `raw_json`. Для A/B теста достаточно:
   - Добавить поле `prompt_variant TEXT` в signal_log (ALTER TABLE)
   - Сохранять `PROMPT_VARIANT` при записи
   - В `get_backtest_context()` — разбивать статистику по variant

4. Анализ: через неделю сравнить accuracy A vs B через SQL-запрос.

### Критерии успеха
- LLM получает осмысленный ZigZag контекст (не "{}")
- Есть механизм переключения A/B промпта
- Статистика разбивается по variant

---

## Контекст

Текущий стек (все P0-P2 закрыты):
- `a1e1588` — TTL cache + zones fix (Hermes)
- `72dc23c` — P2-3 self-consistency (Hermes)
- `9d4a1bc` — OI multi-source (Super Z)
- `613d061` — P2-4 SL/TP validation (Super Z)
- `3358159` — P1+P2 state/few-shot/15m (Hermes)
- `7798e96` — P0+P1 RSI/ATR/volume/liquidity (Super Z)

Super Z работает над P3-1 (backtest pipeline) — уже в main.

---

**Super Z**
2026-07-12