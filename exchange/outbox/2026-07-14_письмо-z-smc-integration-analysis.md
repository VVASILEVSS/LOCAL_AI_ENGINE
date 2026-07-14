# Письмо Z: SMC Integration — анализ и sequencing

**От:** Super Z
**Дата:** 2026-07-14
**Статус:** Решение по интеграции

## Что реально полезно (P1)

| Библиотека | Что брать | Почему |
|---|---|---|
| smartmoneyconcepts | BOS/CHoCH через 4-pivot | Наш ZigZag сложный/нестабильный. 4-pivot pattern — проще и надёжнее. Прямая альтернатива detect_bos() |
| smartmoneyconcepts | Sessions (kill zones) | У нас сессии захардкожены в промпте. Код даст точные границы |
| pymarket-structure | MTF lookahead prevention | htf_values.shift(1) — критично для честного бэктеста. Сейчас backtest потенциально заглядывает в будущее |
| pymarket-structure | Zone quality score [0-10] | Composite метрика (overlap, ATR width, recency, touches) — отличная фича для LLM контекста |
| Prasad1612 | Premium/Discount zones | ICT-концепт, которого нет. Простая метрика для prompt: «цена в premium/discount» |

## Что подождёт (P2-P3)

- **Body-anchored zones** — наши wick-based зоны работают после фикса, не трогать
- **67 колонок** — для backtest, когда будет T6 volume_at_level
- **HMM regime detection** — LLM уже определяет regime визуально
- **Kelly criterion** — в risk_management позже

## Риски плана Гермеса

1. **Phase 1 «1 день» — оптимистично.** Сравнение BOS подходов на 3 символа × 5 ТФ — это полдня визуальной проверки
2. **smartmoneyconcepts swing_length=50** — для M15 это 50×15м = 12.5 часов. Нужно тюнить под наши _PIVOT_DEPTH

## Sequencing

1. **Сейчас** — стабилизировать зоны на живых данных (после фикса `fix/zones-sticking`)
2. **Следующий коммит** — `pip install smartmoneyconcepts`, сравнить BOS 4-pivot vs ZigZag на BTC/ETH
3. **Если 4-pivot лучше** — заменить `detect_bos()`, упростить pipeline
4. **Потом** — MTF lookahead `shift(1)` + zone quality score

## Техническая заметка

`web_dashboard.py` сам запускает `main.py` через `subprocess.Popen` (строка 83-84, 1337-1339). **НЕ запускать main.py отдельно** — будет TelegramConflictError. Достаточно одного `web_dashboard.py`.
