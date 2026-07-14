# ТЗ: Top-Down Structural Analysis (SMC)

> **Рабочий документ для координации Hermes ↔ Super Z.**
> **Ветка:** `feature/top-down-structure`
> **Дата создания:** 2026-07-14
> **Связанные коммиты:** `cb7e93b` (архитектура), `dd5ceef` (оценка Z), `0057d4d` (запуск T1-T5), `641493f` (T1-T5 код), `0da1bb0` (ревью Hermes), (письмо+ТЗ)

---

## 1. Концепция (полная)

### Принципы

1. **Top-down nesting** — D1 → H4 → H1 → M15 → 5M. Каждый ТФ анализируется внутри рамки старшего. Зона младшего ТФ не может выходить за границы старшего (soft clamp).

2. **Зона = последняя суб-структура после BOS.** Зона ТФ = range от крайних swing H/L последней структурной фазы, которую BOS сломал. НЕ raw max/min всех пивотов, НЕ recent 40%.

3. **Общие границы = confluence.** Если соседние ТФ имеют совпадающие границы — это сильный уровень, НЕ раздвигать. Синтетическое расширение (expand_pct) убрано.

4. **Накопление = цена внутри зоны своего ТФ, нет обновления HH/LL.** Для каждого ТФ своё накопление. N последних пивотов не обновляют zone bounds → накопление.

5. **Цели при выходе** = границы старшего ТФ + значимые swing levels из prev_structure.

6. **Объём = фильтр.** При подходе к уровню: объём растёт = пробой, падает = отскок. (Не реализовано — T6)

7. **Трендовые линии по LH/HL** — наклонная структура. Угол = сила тренда. Пробой = ложный/истинный. (Не реализовано — T8-T9)

### Что НЕ правильно в текущем коде (main)

- Независимый анализ каждого ТФ (нет top-down nesting)
- `recent 40%` пивотов вместо последней суб-структуры
- `_enforce_zone_uniqueness` раздвигает общие границы (наоборот концепции)
- `split_structure()` берёт абсолютный max/min всех пивотов до BOS

---

## 2. Структурная картина BTC D1 (пример)

Реальные данные Binance, подтверждено пользователем:

```
2025.10.06  ATH  126199.6  ← начало нисходящего тренда
    ╲
     ╲  трендовая линия 1 (ATH→LH1): slope -0.224%/day
      ╲
2026.01.14  LH1   97924.5  ← 1-й lower high
        ╲
         ╲  трендовая линия 2 (LH1→LH2): slope -0.136%/day (замедление)
          ╲
2026.05.11  LH2   82380.0  ← последний LH перед BOS
            ↓
2026.06.05  BOS   59130.9  ← bearish BOS (сломал range LH2→BOS)
```

**BOS @ 59130.9 сломал range 82380→59130** (последняя суб-структура), а не 97924→59130.

97924.5 (LH1) — реальный pivot, но из более ранней структурной фазы. Включать его в prev_structure.high — некорректно.

### Трендовый угол (математика)

- **% per candle** — масштаб-независимый, лучший вариант
- ATH→LH1: **-0.224%/day** (крутой)
- LH1→LH2: **-0.136%/day** (замедление → возможный разворот)
- atan(slope) = ~-89.8° — **бессмысленно**, зависит от масштаба графика

### Классификация угла (предлагаемая)

| Класс | %/day | Значение |
|---|---|---|
| steep | > 0.3% | импульс, сильный тренд |
| moderate | 0.1–0.3% | нормальный тренд |
| shallow | < 0.1% | коррекция, флет, накопление |

---

## 3. Реализовано (T1-T5, коммит `641493f`)

| Фаза | Что | Статус | Файл |
|---|---|---|---|
| **T1** | `parent_zone` + soft clamp (10%) в `analyze_tf_structure()` | ✅ работает | `structure.py` |
| **T2** | `analyze_topdown()` оркестратор. `benchmark_zigzag.py` → 3 фазы | ✅ работает | `structure.py`, `benchmark_zigzag.py` |
| **T3** | `detect_accumulation()` (N пивотов без обновления zone) | ⚠️ баг: `tf` не передаётся | `structure.py` |
| **T4** | `targets` из parent boundaries + prev_structure swing | ✅ работает | `structure.py` |
| **T5** | `_enforce_zone_uniqueness` → confluence (убрано expand_pct) | ✅ код корректный | `ollama_client.py` |

### Тест ETH (результат)

```
1D:  [1503.6 - 1848.8]  parent=None
  ↓
4H:  [1712.5 - 1848.0]  parent=1d  ✅ внутри 1D
  ↓
1H:  [1748.0 - 1793.9]  parent=4h  ✅ внутри 4H
  ↓
15M: [1773.5 - 1793.9]  parent=1h  ✅ внутри 1H
```

Top-down nesting работает. Chain broken = False везде. Накопление определяется. Цели = parent boundaries + swing levels.

---

## 4. Известные баги

### БАГ-1: `detect_accumulation()` — `tf` не передаётся (БЛОКИРУЕТ MERGE)

**Файл:** `core/structure.py`, строка ~413 (в `analyze_tf_structure()`)

```python
# Сейчас:
is_acc, acc_count = detect_accumulation(swing_points, zone_high, zone_low)
#                                                          ← tf не передаётся!

# Должно быть:
is_acc, acc_count = detect_accumulation(swing_points, zone_high, zone_low, tf=tf)
```

**Эффект:** `_ACCUM_MIN_PIVOTS.get("", 3)` = 3 для всех ТФ.
- D1: min_piv=3 вместо 2 → BTC 1D (2 пивота) = False, должно быть True
- 15M: min_piv=3 вместо 4 → слишком чувствителен

**Фикс:** 1 строка. Ready.

### БАГ-2: `split_structure()` — абсолютный max vs последняя суб-структура

**Файл:** `core/structure.py`, строки 250-251

```python
prev_h = max(p["price"] for p in prev_pivots if p["type"] == "high") or current_price
prev_l = min(p["price"] for p in prev_pivots if p["type"] == "low") or current_price
```

`prev_pivots` = все пивоты от начала до BOS. `max(highs)` = абсолютный экстремум из ранней фазы.

**Пример:** BTC D1 → `prev_structure.high = 97924.5` (LH1, Jan 2026). Но BOS @ 59130.9 сломал range 82380→59130 (LH2, May 2026). 97924 — из другой фазы.

### Варианты фикса (обсуждаем)

**A. Последний swing перед BOS**
```python
prev_highs = [p for p in prev_pivots if p["type"] == "high"]
prev_lows  = [p for p in prev_pivots if p["type"] == "low"]
prev_h = prev_highs[-1]["price"] if prev_highs else current_price
prev_l = prev_lows[-1]["price"] if prev_lows else current_price
```
- ✅ Простой, даёт 82380→59130
- ⚠️ Может потерять контекст если последний high — мелкий pullback

**B. Последняя суб-структура (от prev BOS)** — взять структуру между предыдущим BOS и текущим. Но в данных обычно один BOS → не работает.

**C. Last N pivots**
```python
prev_recent = prev_pivots[-3:]  # последние 3 пивота перед BOS
prev_h = max(p["price"] for p in prev_recent if p["type"]=="high")
prev_l = min(p["price"] for p in prev_recent if p["type"]=="low")
```
- ✅ Более репрезентативно
- ⚠️ N — гиперпараметр

**D. Структурное окно для prev** — ограничить prev_pivots последними K свечами (D1=60, H4=40, H1=30, 15m=20).
- ✅ Адаптивно
- ⚠️ K — гиперпараметр

**Статус:** ждём решение Super Z.

### БАГ-3 (minor): BTC 1D `prev_structure` может содержать stale/outlier pivots

Не блокирует merge. Можно решить через sanity check (filter outliers в `_find_real_pivots`) или через фикс БАГ-2.

---

## 5. Roadmap

| Фаза | Что | Статус | Приоритет |
|---|---|---|---|
| **T1** | parent_zone + soft clamp | ✅ | — |
| **T2** | analyze_topdown() | ✅ | — |
| **T3** | detect_accumulation() | ⚠️ баг | **P0** (блокирует merge) |
| **T4** | targets | ✅ | — |
| **T5** | confluence | ✅ | — |
| **T6** | volume_at_level (1.5×ATR радиус, 5 vs 20 свечей) | не начат | P2 |
| **T7** | промпт фаза 2 (precomputed → концепция C) | не начат | P3 |
| **T8** | `detect_trend_lines()` — трендовые линии по LH/HL + угол + r² | не начат | P1 |
| **T9** | `check_trend_line_break()` + `is_false_breakout()` | не начат | P1 |
| **T10** | chart markup: ZigZag линия + BOS вертикаль + zone rect | не начат | P3 |
| **T11** | sanity check outliers в `_find_real_pivots` | не начат | P2 |

---

## 6. T8-T9: Трендовые линии (предложение Hermes)

### T8: `detect_trend_lines(pivots, direction)`

```python
def detect_trend_lines(
    swing_points: List[Dict],
    direction: str,  # "bullish" (HL series) or "bearish" (LH series)
) -> Optional[Dict]:
    """
    Строит трендовую линию по последовательности HL (bullish) или LH (bearish).

    Берёт последние N пивотов одного типа:
      - bearish: pivot["type"]=="high" (LH series)
      - bullish: pivot["type"]=="low"  (HL series)

    Линейная регрессия → slope, intercept, r².
    slope_pct = slope / first_price * 100  # % per candle

    Returns: {
        "slope_pct": float,      # % per candle
        "intercept": float,
        "r_squared": float,      # 0-1, качество линии
        "pivot_points": [...],   # пивоты на линии
        "direction": str,        # bullish/bearish
        "angle_class": str,      # steep/moderate/shallow
    }
    """
```

### T9: `check_trend_line_break(price, line, closes, lookback=5)`

```python
def check_trend_line_break(
    current_price: float,
    line: Dict,         # из detect_trend_lines
    closes: List[float],
    lookback: int = 5,  # свечей после пробоя для проверки
) -> Dict:
    """
    Проверяет пробой трендовой линии.

    - true breakout = закрытие выше линии + следующий pivot обновляет HH
    - false breakout = возврат под линию за lookback свечей
    - none = линия не пробита

    Returns: {
        "broken": bool,
        "break_type": str,    # "true" / "false" / "none"
        "break_price": float,
        "break_index": int,
    }
    """
```

### Открытые вопросы для Super Z

1. Сколько пивотов брать для линии (2? 3? все?)
2. Как считать r² (numpy.polyfit? вручную?)
3. Как определять false breakout (lookback? закрытие выше/ниже?)
4. Угол: % per candle vs log returns vs ATR-normalized?
5. Куда встраивать — `structure.py` (как T1-T5) или отдельный модуль `trend_lines.py`?

### Зачем это нужно

**Третье измерение** помимо горизонтальных зон:
- **Зоны** = горизонтальные range (где цена)
- **Трендовая линия** = наклонная (куда движется)
- **Угол** = сила/скорость (импульс vs коррекция)

Сигнал:
- Зона + BOS + пробой трендовой = сильный сигнал
- Зона + BOS но трендовая не пробита = коррекция внутри тренда

---

## 7. Концепция C (target, после стабилизации)

| Вариант | Роль LLM | Роль алгоритма |
|---|---|---|
| **A** | интерпретатор только | детерминистские уровни |
| **B** (текущая) | копирует уровни + интерпретация | min-span/uniqueness/VP fallback = костыли |
| **C** (target) | интерпретация (signal/direction/confidence/narrative). НЕ выдаёт upper/lower | BOS+pivots+VP+zones+trend lines |

Top-down pipeline = prerequisite для C. T1-T5 + T8-T9 → можно убрать min-span, `_enforce_zone_uniqueness`, VP fallback.

---

## 8. Протокол работы

1. **Ветка:** `feature/top-down-structure` (main остаётся стабильным)
2. **Коммуникация:** `exchange/outbox/` (Hermes → Z), `exchange/inbox/` (Z → Hermes)
3. **ТЗ:** этот документ — правим напрямую, коммитим в ветку
4. **Ревью:** каждое изменение → ревью + тест от Hermes перед merge в main

### История коммитов (newest→oldest)

| Коммит | Автор | Что |
|---|---|---|
| (pending) | Hermes | ТЗ + письмо (split_structure баг + T8-T9) |
| `0da1bb0` | Hermes | ревью T1-T5 |
| `641493f` | Super Z | T1-T5 код |
| `0057d4d` | Hermes | запуск T1-T5 (письмо) |
| `dd5ceef` | Super Z | оценка архитектуры + roadmap T1-T7 |
| `cb7e93b` | Hermes | архитектурный документ |
| `babb5f0` | Super Z | structural window + ZigZag fallback |
| `3f45974` | Super Z | detect_bos fix |

---

## 9. Параметры системы (контекст)

- **ТФ:** 15m, 1h, 4h, 1D (из `forecasts.db settings.timeframes`)
- **Символы:** BTCUSDT, XAUTUSDT (из `settings.symbols`)
- **Бот:** `@my_hermes_lokal_ai_bot` (cloud glm-5.2-fast-preview), Auto: OFF
- **LLM:** Alibaba GLM endpoint, `glm-5.2-fast-preview`
- **Биржа:** ccxt.binance() из KZ без VPN
- **Python:** 3.13 (.venv), `PYTHONPATH=""` перед запуском
- **Тестовая машина:** i5-10300H, GTX 1650 Ti, 16GB RAM

### Гиперпараметры (текущие)

| Параметр | Значение | Где |
|---|---|---|
| `_PIVOT_DEPTH` | {5m:4, 15m:3, 1h:3, 4h:3, 1d:3} | `benchmark_zigzag.py` |
| `_PIVOT_ATR_K` | 0.5 | `benchmark_zigzag.py` |
| `_STRUCT_WINDOW` | {5m:50, 15m:50, 1h:80, 4h:None, 1d:None} | `benchmark_zigzag.py` |
| `_ACCUM_MIN_PIVOTS` | {1d:2, 4h:3, 1h:3, 15m:4, 5m:4} | `structure.py` |
| Soft clamp | 10% | `structure.py` (T1) |
| Min-span | {1D:2.5%, 4H:2.0%, 1H:1.2%, 15M:0.8%, 5M:0.4%} | `ollama_client.py` |

---

*Документ обновляется по мере работы. Правила: правим напрямую, коммитим в `feature/top-down-structure`, каждое изменение ревьюим.*
