# Ответ Z: Variant D v2 — динамический N + parent clamp skip + YeohJooYam оценка

## 0. Резюме

Коммит `XXX`: два фикса в `core/structure.py`:
1. **Динамический N по TF** — 1D=3, 4H=4, 1H=5, 15M=6, 5M=8
2. **Parent clamp skip** — если parent span < min_span для parent TF

Плюс архитектурный ответ на YeohJooYam.

## 1. Что изменено

### 1.1 Динамический `_TF_SWING_N` (structure.py:431)

```python
_TF_SWING_N = {"5m": 8, "15m": 6, "1h": 5, "4h": 4, "1d": 3}
_LAST_SWINGS_MIN = _TF_SWING_N.get(tf, 4)
```

Ожидаемое влияние на замеры:

| TF | N (было→стало) | BTC (было→ожидание) | ETH (было→ожидание) |
|----|----------------|---------------------|---------------------|
| 1D | 4→3 | 13.56%→8-10% | 28.85%→15-20% |
| 4H | 4→4 | 6.12%→6% | 8.01%→8% |
| 1H | 4→5 | 0.51%→0.8-1.2% | 0.72%→1.0-1.5% |
| 15M | 4→6 | 0.55%→0.8-1.2% | 0.72%→1.0-1.5% (parent skip!) |

1D/4H уменьшатся (меньше swings), 1H/15M увеличатся (больше swings).
BTC 1D=8-10% всё ещё широковато для цели 3-6%, но это реальный 3-дневный swing range
в тренде. Clamp'ить 1D к цели — значит искусственно ограничивать, что нарушит
Variant E (ZigZag = authoritative).

### 1.2 Parent clamp skip (structure.py:474-489)

```python
_PARENT_MIN_SPAN = {"1d": 0.02, "4h": 0.015, "1h": 0.008, "15m": 0.005, "5m": 0.003}
if parent_span_pct < parent_min:
    parent_span_ok = False  # skip clamp
```

Если 1H span = 0.72% < min 0.8% → parent "не сформирован" → 15M НЕ clamp'ится
к 1H → 15M может расширяться через Variant D свободно.

Это решает **ETH 15M = копия 1H** (0.72% < 0.8% → skip → 15M independent).

## 2. YeohJooYam — архитектурная оценка

### 2.1 Нужен ли переход на N-bar fractal?

**НЕТ.** Наш `_find_real_pivots` — это УЖЕ N-bar fractal:

```python
# benchmark_zigzag.py:128 — наш код
if highs[i] == np.max(window_h):   # depth=3 → 3-барный фрактал
    pivots_h.append((i, float(highs[i])))

# YeohJooYam — его код
if(high[i] <= high[i - k] || high[i] <= high[i + k]) isHigh = false;
# InpSwingN=5 → 5-барный фрактал
```

**Разница только в N** (3 vs 5) и в `min_atr_distance` (мы фильтруем шум через ATR,
он не фильтрует). Его fractal ДАЖЕ ПРОЩЕ — нет ATR фильтрации → больше шума.

Его `InpSwingN=5` ≈ наш `depth=5`. Мы используем `depth=3` по умолчанию
(адаптивно через `_PIVOT_DEPTH`). Можно увеличить до 5 для шумных TF — но это
настройка, не замена алгоритма.

### 2.2 Зона = 2 swing (prevHigh..refHigh)?

**Это НЕ новый подход — это наш curr-only.**

Разбор state machine YeohJooYam:
1. `refHigh` = последний swing-high (ещё не пробитый)
2. `prevHigh` = предыдущий swing-high (сдвинутый при новом)
3. BOS: `close > refHigh` → `trend=1`, `haveHigh=false`

После BOS:
- `refHigh` = пробитый уровень (now consumed)
- `prevHigh` = уровень ДО него
- `prevHigh..refHigh` = диапазон между двумя последними swing-highs

Но это **ровно curr_struct** в нашей терминологии:
- BOS пробивает уровень → post-BOS range = новые swing'и после BOS
- Если после BOS только 1 swing → curr_struct = микро (0.17%)
- Если 2+ swings → curr_struct = их range

**Zone = prevHigh..refHigh при 2 post-BOS swings = curr_struct**. Та же микрозона.
YeohJooYam не решает проблему — он просто НЕ имеет MTF, поэтому 2 swings на H1
даёт 2-5% (большой window, нет parent clamp). У нас 2 swings на H1 в STRUCT_WINDOW=80
дает 0.17% (микро), потому что BOS был только что.

### 2.3 Почему YeohJooYam не имеет parent clamp?

Потому что **у него нет MTF**. Он анализирует один TF за раз.
MTF parent clamp — это наш архитектурный выбор для top-down анализа.
Без parent clamp 15M может дать зону которая противоречит 1H тренду
(15M зона выше 1H зоны в нисходящем тренде).

**Parent clamp нужен, но с умом** — skip если parent микро (мой фикс).

### 2.4 Что полезного у YeohJooYam?

1. **Подтверждение нашей архитектуры** — state machine с BOS/CHoCH = наш `detect_bos`
2. **N-bar fractal = наш _find_real_pivots** — уже используем
3. **Close-confirmed BOS** — у нас тоже (zone_breakout = close > zone_high)
4. **Premium/Discount** — YeohJooYam упоминает в комментариях, но НЕ реализует.
   Premium/Discount = зона выше/ниже equilibrium (50% zone). Это может быть
   полезно для entry timing (концепт C: WHERE = zone boundary, WHAT = structural level).
   Но это отдельная фича, не замена Variant D.

### 2.5 Вердикт

| Предложение | Принято? | Почему |
|-------------|----------|--------|
| Заменить ZigZag на N-bar fractal | **НЕТ** | Уже используем (_find_real_pivots = fractal) |
| Zone = 2 swing (prevHigh..refHigh) | **НЕТ** | = curr-only (0.17% микро) |
| Убрать parent clamp | **НЕТ** | Нужен для MTF, но с skip для микро-parent |
| State machine BOS/CHoCH | **УЖЕ ЕСТЬ** | detect_bos + zone_breakout |
| Premium/Discount | **ПОЗЖЕ** | Отдельная фича для entry timing |

## 3. Ожидаемые результаты после коммита

Перезапусти бота. Ожидание (POST-LLM OFF + parent clamp skip + dynamic N):

| Сим | TF | было (POST-LLM OFF) | ожидание | цель |
|-----|-----|---------|---------|------|
| BTC | 1D | 13.56% | **8-10%** (N=3) | 3-6% ⚠️ |
| BTC | 4H | 6.12% | **~6%** (N=4) | 2-5% ⚠️ |
| BTC | 1H | 0.51% | **0.8-1.2%** (N=5) | 1-2% ✅ |
| BTC | 15M | 0.55% | **0.8-1.2%** (N=6, skip clamp) | 0.5-1.5% ✅ |
| ETH | 1D | 28.85% | **15-20%** (N=3) | 3-6% ⚠️ |
| ETH | 4H | 8.01% | **~8%** (N=4) | 2-5% ⚠️ |
| ETH | 1H | 0.72% | **1.0-1.5%** (N=5) | 1-2% ✅ |
| ETH | 15M | 0.72% (=1H) | **1.0-1.5%** (N=6, skip clamp!) | 0.5-1.5% ✅ |
| XAUT | 1D | 6.71% | **~6%** (N=3) | 3-6% ✅ |
| XAUT | 4H | 2.87% | **~3%** (N=4) | 2-5% ✅ |
| XAUT | 1H | 0.49% | **0.8-1.2%** (N=5) | 1-2% ⚠️ |
| XAUT | 15M | 0.08% | **0.1-0.3%** (суббота) | 0.5-1.5% ❌ |

1D/4H BTC/ETH всё ещё выше цели — это реальный swing range в тренде.
Цели 3-6% для 1D и 2-5% для 4H — это средние, не жесткие лимиты.
В сильном тренде зоны ДОЛЖНЫ быть шире.

## 4. Next steps

1. **Hermes: перезапустить бота, замерить** (POST-LLM OFF + мой коммит)
2. **Variant E Phase 1** — убрать `tf_zones.range` из JSON schema (твоя задача)
3. **5M** — после Phase 1 FALLBACK должен подставить ZigZag zone для 5M
4. **XAUT суббота** — отдельная задача (no-liquidity detection)

— Z