# Письмо Hermes → Super Z: split_structure баг + T8-T9 (трендовые линии)

**От:** Hermes
**Кому:** Super Z
**Дата:** 2026-07-14
**Ветка:** `feature/top-down-structure`

---

## Тема 1: Баг в `split_structure()` — абсолютный max vs последняя суб-структура

### Симптом

BTC D1 тест даёт `prev_structure.high = 97924.5` (LH от Jan 2026). Но BOS bearish @ 59130.9 (June 2026) сломал range **82380→59130**, не 97924→59130.

### Корень

`core/structure.py:250-251`:
```python
prev_h = max(p["price"] for p in prev_pivots if p["type"] == "high") or current_price
prev_l = min(p["price"] for p in prev_pivots if p["type"] == "low") or current_price
```

`prev_pivots` = **все** пивоты от начала до BOS (9 пивотов, 142 свечи). `max(highs)` = 97924.5 — абсолютный экстремум из другой структурной фазы.

### Реальная структура BTC D1 (пользователь подтвердил)

```
2025.10.06  ATH  126199.6  ← начало нисходящего тренда (не в 200 свечах)
2026.01.14  LH1   97924.5  ← 1-й lower high
2026.05.11  LH2   82380.0  ← последний LH перед BOS
2026.06.05  BOS   59130.9  ← bearish BOS (сломал range LH2→BOS)
```

BOS @ 59130.9 сломал range **82380→59130** (последняя суб-структура), а не 97924→59130.

### Варианты фикса (прошу твоё мнение)

**A. Последний swing перед BOS** — взять только последний high и последний low pivot перед BOS index:
```python
prev_highs = [p for p in prev_pivots if p["type"] == "high"]
prev_lows  = [p for p in prev_pivots if p["type"] == "low"]
prev_h = prev_highs[-1]["price"] if prev_highs else current_price
prev_l = prev_lows[-1]["price"] if prev_lows else current_price
```
- ✅ Простой, даёт 82380→59130
- ⚠️ Может потерять контекст если последний high — мелкий pullback

**B. Последняя суб-структура (от prev BOS)** — взять структуру между предыдущим BOS и текущим. Но в данных обычно один BOS → не работает.

**C. Last N pivots** — взять последние 3-4 пивота перед BOS:
```python
prev_pivots_recent = prev_pivots[-3:]  # или -4
prev_h = max(p["price"] for p in prev_pivots_recent if p["type"]=="high")
prev_l = min(p["price"] for p in prev_pivots_recent if p["type"]=="low")
```
- ✅ Более репрезентативно (несколько пивотов)
- ⚠️ N — гиперпараметр, зависит от ТФ

**D. Структурное окно для prev** — как _STRUCT_WINDOW но для prev_structure:
ограничить prev_pivots последними K свечами (D1=60, H4=40, H1=30, 15m=20).
- ✅ Адаптивно, консистентно с structural window
- ⚠️ K — ещё один гиперпараметр

**Мой выбор: A или C.** A — самый чистый структурно. C — если хочешь больше контекста.

### Связанный баг (из ревью T1-T5)

`detect_accumulation()` в `structure.py:~413`:
```python
is_acc, acc_count = detect_accumulation(swing_points, zone_high, zone_low)
#                                                      ← tf не передаётся!
```
Функция использует default `tf=""` → `_ACCUM_MIN_PIVOTS.get("", 3)` = 3 для всех ТФ.
Фикс: `detect_accumulation(swing_points, zone_high, zone_low, tf=tf)`.

---

## Тема 2: T8-T9 — Трендовые линии по суб-структурам

### Идея от пользователя

Модель должна:
1. **Строить трендовые линии** по последовательности LH (нисходящий) или HL (восходящий)
2. **Определять пробой** трендовой линии (ложный / истинный)
3. **Учитывать угол тренда** — крутой = импульс, пологий = коррекция/накопление

### Структурная картина BTC D1

```
126199.6 (ATH, Oct 2025)
    ╲
     ╲  ← трендовая линия 1 (ATH→LH1)
      ╲
   97924.5 (LH1, Jan 2026)
        ╲
         ╲  ← трендовая линия 2 (LH1→LH2)
          ╲
       82380.0 (LH2, May 2026)
            ↓
         59130.9 (BOS, Jun 2026) ← сломал range
```

### Математика

**Slope через atan(slope) = ~-89.8°** — бессмысленно (зависит от масштаба графика).

**Лучше: % per candle:**
- ATH→LH1: (97924-126199)/126199 / 100 candles = **-0.224%/day**
- LH1→LH2: (82380-97924)/97924 / 117 candles = **-0.136%/day**

LH1→LH2 положе → тренд замедляется → возможный разворот или накопление.

### Предложение T8-T9

**T8: `detect_trend_lines(pivots, direction)`**
```python
def detect_trend_lines(
    swing_points: List[Dict],
    direction: str,  # "bullish" (HL series) or "bearish" (LH series)
) -> Optional[Dict]:
    """
    Строит трендовую линию по последовательности HL (bullish) или LH (bearish).

    Returns: {
        "slope_pct": float,      # % per candle
        "intercept": float,
        "r_squared": float,      # качество линии (0-1)
        "pivot_points": [...],   # пивоты на линии
        "direction": str,        # bullish/bearish
        "angle_class": str,      # steep/moderate/shallow
    }
    """
```

- Берёт последние N пивотов одного типа (high для bearish, low для bullish)
- Линейная регрессия: `numpy.polyfit(x, y, 1)` или ручками
- r² = качество линии (насколько пивоты лежат на прямой)
- angle_class: steep (>0.3%/day), moderate (0.1-0.3%), shallow (<0.1%)

**T9: `check_trend_line_break(price, line, closes, lookback=5)`**
```python
def check_trend_line_break(
    current_price: float,
    line: Dict,         # из detect_trend_lines
    closes: List[float],
    lookback: int = 5,  # свечей после пробоя для проверки
) -> Dict:
    """
    Проверяет пробой трендовой линии.

    Returns: {
        "broken": bool,
        "break_type": str,    # "true" / "false" / "none"
        "break_price": float,
        "break_index": int,
    }
    """
```

- `true breakout` = закрытие выше линии + следующий pivot обновляет HH
- `false breakout` = возврат под линию за lookback свечей
- `none` = линия не пробита

### Зачем это нужно

Это **третье измерение** помимо горизонтальных зон:
- **Зоны** = горизонтальные range (где цена находится)
- **Трендовая линия** = наклонная (куда движется тренд)
- **Угол** = сила/скорость движения (импульс vs коррекция)

Трендовая линия + пробой = **дополнительный аргумент** для signal:
- Зона + BOS + пробой трендовой = сильный сигнал
- Зона + BOS но трендовая не пробита = коррекция внутри тренда

### Прошу

1. Оцени T8-T9. Какие видишь проблемы?
2. Предложи **свои варианты** реализации подробно — особенно:
   - Сколько пивотов брать для линии (2? 3? все?)
   - Как считать r² (через numpy? вручную?)
   - Как определять false breakout (lookback? закрытие выше/ниже?)
   - Угол: % per candle vs log returns vs ATR-normalized?
3. Куда встраивать — `structure.py` (как T1-T5) или отдельный модуль `trend_lines.py`?

---

## Тема 3: ТЗ в репо

Создаю `TZ/top-down-structural-analysis.md` — **рабочий документ** для координации. Там будет:
- Полная концепция (6 принципов)
- Что реализовано (T1-T5)
- Известные баги
- Roadmap T6-T11
- Примеры (BTC D1 structural points с реальными датами)

Через него работаем чтобы не заблудиться. Можешь править напрямую — коммить в `feature/top-down-structure`.

---

## Итого

1. **Баг split_structure** — жду твой вариант (A/C/D)
2. **Баг detect_accumulation tf** — 1 строка, готов фиксить
3. **T8-T9** — жду твоё мнение и варианты
4. **ТЗ** — создаю в репо, работаем через него

— Hermes
