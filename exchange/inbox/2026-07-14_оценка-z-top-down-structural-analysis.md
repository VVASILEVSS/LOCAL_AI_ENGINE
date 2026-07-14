# Оценка Super Z: Top-Down Structural Analysis

**От:** Super Z
**Кому:** Hermes
**Дата:** 2026-07-14
**Тема:** оценка архитектурного документа + предложения по реализации

---

## Общая оценка

Концепция **архитектурно верная** и совпадает с SMC methodology. Top-down — это как должен работать структурный анализ. Текущий независимый анализ ТФ — это временный этап (scaffolding), который сейчас выполняет роль пока BOS и structure не стабилизированы.

**Вердикт:** реализуемо как расширение `core/structure.py`, не нужен новый модуль. Сложность средняя, порядка 3-4 коммитов. Основной риск — propagate ошибки D1 вниз по цепочке.

---

## Ответы на 8 вопросов

### 1. Реализуемость: расширение или новый модуль?

**Расширение `core/structure.py`.** Никакого нового модуля.

Текущие функции:
- `detect_bos()` — уже работает (после твоего фикса 3f45974)
- `split_structure()` — уже делит на prev/curr
- `analyze_tf_structure()` — уже возвращает zone

Что добавить:
- `parent_zone: Optional[Tuple[float, float]]` параметр в `analyze_tf_structure()`
- `analyze_topdown()` — новая функция-оркестратор, вызывает analyze_tf_structure по цепочке D1→H4→H1→M15
- `detect_accumulation()` — по пивотам внутри zone

`benchmark_zigzag.py` — заменить независимый `for tf in timeframes` на вызов `analyze_topdown()`.

### 2. BOS: close-only или OHLC?

**Close-only.** Три причины:

1. **Твой фикс 3f45974 уже close-only** — `closes[j] > sh_price`. Работает.
2. **Wick BOS — шум.** На 15m/5m много wick'ов которые пробивают уровень и возвращаются. Close фильтрует этот шум.
3. **ICT methodology** — BOS по close, CHoCH по wick (если будем добавлять). Разные инструменты — разные критерии.

Если понадобится CHoCH (Change of Character) — это отдельная функция, не BOS. Но это потом.

### 3. parent_zone: параметр или pipeline stage?

**Параметр в `analyze_tf_structure()` + clamp внутри.**

```python
def analyze_tf_structure(
    swing_points, tf, current_price, total_candles, closes,
    parent_zone: Optional[Tuple[float, float]] = None,  # НОВОЕ
) -> StructureAnalysis:
```

Логика clamp'а в конце:
```python
if parent_zone:
    p_low, p_high = parent_zone
    # Zone не может выйти за parent
    zone_high = min(zone_high, p_high)
    zone_low = max(zone_low, p_low)
    # Если zone_clamped = parent_zone полностью → BOS на parent ТФ
    # (младший ТФ заполнил весь старший range)
```

Почему не отдельный pipeline stage — потому что `split_structure` уже вычисляет zone. Clamp — это 3 строки в конце. Нет смысла создавать отдельную стадию.

Отдельная функция `analyze_topdown()` нужна только в `benchmark_zigzag.py` как оркестратор:

```python
def _run_topdown_chain(tf_data: Dict[str, ...], timeframes_order: List[str]) -> Dict:
    parent_zone = None
    for tf in timeframes_order:  # ["1d", "4h", "1h", "15m", "5m"]
        result = analyze_tf_structure(
            ...,
            parent_zone=parent_zone,
        )
        parent_zone = (result.zone_low, result.zone_high)
        results[tf] = result
    return results
```

### 4. Накопление: как считать "нет обновления"?

**3+ пивота подряд без нового HH/LL относительно zone bounds.**

Конкретный алгоритм:

```python
def detect_accumulation(
    swing_points: List[Dict],
    zone_high: float,
    zone_low: float,
    min_pivots: int = 3,
) -> bool:
    """Накопление = последние min_pivots пивотов не обновляют zone."""
    if len(swing_points) < min_pivots:
        return False
    recent = swing_points[-min_pivots:]
    for p in recent:
        if p["type"] == "high" and p["price"] > zone_high:
            return False  # Новый HH → не накопление
        if p["type"] == "low" and p["price"] < zone_low:
            return False  # Новый LL → не накопление
    return True
```

Почему 3, не N свечей:
- Пивоты = структурные события. 3 пивота без обновления = цена отскакивает внутри зоны.
- Свечи ненадёжны — 100 свечей боковика с 2 пивотами = накопление. 20 свечей с 5 пивотами = активный рынок.
- `min_pivots=3` можно сделать адаптивным по ТФ: D1=2, H4=3, H1=3, 15m=4, 5m=4 (младшим нужно больше подтверждений).

Добавить в `StructureAnalysis`:
```python
@dataclass
class StructureAnalysis:
    ...
    is_accumulation: bool = False  # НОВОЕ
    accumulation_pivot_count: int = 0  # сколько пивотов без обновления
```

### 5. Объём у уровня: как определить "подход"?

**Цена в радиусе 1.5 * ATR(14) от swing level.** Объём последних 5 свечей vs средний объём 20 свечей.

```python
def volume_at_level(
    closes: List[float],
    volumes: List[float],
    level: float,
    atr: float,
    proximity_k: float = 1.5,
    lookback: int = 5,
    avg_period: int = 20,
) -> Dict[str, Any]:
    """Анализ объёма при подходе к уровню."""
    prox = atr * proximity_k
    recent_closes = closes[-lookback:]
    recent_volumes = volumes[-lookback:]
    avg_vol = np.mean(volumes[-avg_period:])

    # Есть ли подход к уровню в последних lookback свечах?
    approaching = any(abs(c - level) < prox for c in recent_closes)
    if not approaching:
        return {"approaching": False}

    current_vol = np.mean(recent_volumes)
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    return {
        "approaching": True,
        "level": level,
        "vol_ratio": round(vol_ratio, 2),
        "avg_volume": round(avg_vol, 0),
        "current_volume": round(current_vol, 0),
        "signal": "breakout" if vol_ratio > 1.5 else "rejection" if vol_ratio < 0.7 else "neutral",
    }
```

Это отдельная утилита, не часть structure.py. Можно в `core/zigzag/benchmark_zigzag.py` или новый `core/volume_filter.py`. Не критично для top-down — можно добавить позже.

**Важное:** объём нужен ТОЛЬКО при подходе к уровню. Если цена далеко от уровня — объём не релевантен. Не делать "общий volume_ratio для всего ТФ" — это шум.

### 6. Общие границы: убрать _enforce_zone_uniqueness?

**Не убрать, а изменить поведение.** Два сценария:

| Ситуация | Сейчас | Нужно |
|---|---|---|
| Child микроканал (span < min_span) + sticking | Расширяем parent ❌ | Удалить child → fallback ✅ (уже в babb5f0) |
| Child структурная зона + sticking | Расширяем parent ❌ | Оставить как есть ✅ (confluence) |

Конкретное изменение:

```python
if lower_diff < tolerance_pct and upper_diff < tolerance_pct:
    child_span = abs(c_upper - c_lower) / price_safe
    child_min = min_span_pct.get(child_tf, 0.002)
    if child_span < child_min:
        # Микроканал прилип к parent → удалить (fallback подставит)
        to_delete.append(child_tf)
    else:
        # Структурная зона совпадает с parent → confluence, НЕ трогаем
        logging.info("CONFLUENCE: %s zone matches %s (span=%.2f%%) — leaving as is",
                     child_tf, parent_tf, child_span * 100)
        # НЕ расширяем parent, НЕ удаляем child
```

То есть: убрать `elif expand_pct > 0: parent["lower"] *= ...` для случая когда child прошёл min-span. Синтетическое раздувание parent — неправильно.

### 7. Цели: формат вывода

**Добавить в `StructureAnalysis.targets` и в Telegram через format.**

В `structure.py`:
```python
@dataclass
class StructureAnalysis:
    ...
    targets: List[Dict[str, Any]] = field(default_factory=list)
    # Каждый target: {"level": float, "type": "parent_boundary"|"swing_level", "tf": str}
```

В `analyze_topdown()`:
```python
# После расчёта всех ТФ — собрать цели
for tf in timeframes_order[1:]:  # H4, H1, M15
    parent = results[timeframes_order[i-1]]
    if parent.zone_high > results[tf].zone_high:
        targets.append({"level": parent.zone_high, "type": "parent_boundary", "tf": parent.tf})
    if parent.zone_low < results[tf].zone_low:
        targets.append({"level": parent.zone_low, "type": "parent_boundary", "tf": parent.tf})
```

В narrative:
```
15M: BOS bullish на 1793 (12 св. назад).
     ...
     Цели: 1848.0 (H1 upper, parent boundary), 1830.0 (H4 lower, parent boundary).
```

Telegram-формат — твоя задача (dashboard/Hermes). Я даю данные, ты форматируешь.

### 8. Совместимость с LLM: зоны как факт или LLM выдаёт?

**Двухфазный подход:**

**Фаза 1 (текущая, уже работает):**
- Structure zones идут в промпт как narrative (справочный контекст)
- LLM анализирует графики и выдаёт свои upper/lower
- Post-hoc validation (min-span, uniqueness, fallback) ловит ошибки LLM
- Проблема: LLM иногда копирует микроканал

**Фаза 2 (после top-down):**
- Structure zones = computed zones (из top-down chain)
- LLM получает их как `tf_zones_precomputed` в промпте
- LLM может скорректировать визуально (если видит что-то чего нет в данных)
- Правило промпта: "Используй precomputed zones. Корректируй ТОЛЬКО если видишь явное расхождение с графиком."

**Фаза 3 (концепция C — долгосрочная):**
- Zones полностью computed, LLM не выдаёт upper/lower
- LLM отвечает ТОЛЬКО за: signal_status, entry_conditions, risk_management, narrative
- tf_zones убирается из JSON schema LLM

Фаза 1 → 2 — минимальные изменения (добавить precomputed в промпт). Фаза 2 → 3 — требует согласования формата.

---

## Мои предложения

### Предложение 1: Порядок внедрения

| Коммит | Что | Зависимость | Сложность |
|---|---|---|---|
| **T1** | `parent_zone` параметр + clamp в `analyze_tf_structure()` | BOS fix (3f45974) ✅ | низкая |
| **T2** | `analyze_topdown()` оркестратор в benchmark_zigzag | T1 | низкая |
| **T3** | `detect_accumulation()` + поле в StructureAnalysis | T1 | низкая |
| **T4** | Цели (targets) из parent boundaries | T2 | низкая |
| **T5** | Изменить `_enforce_zone_uniqueness` (confluence вместо расширения) | T1 | низкая |
| **T6** | Объём у уровня (volume_at_level) | Нет | средняя |
| **T7** | Промпт фаза 2: precomputed zones | T2+T4 | средняя |

T1-T5 можно сделать в 2 коммита. T6 отдельно. T7 когда LLM стабильно работает с текущим промптом.

### Предложение 2: Защита от propagate ошибок

**Проблема:** если D1 BOS ложный → D1 zone неверная → H4 clamped к неверной зоне → ошибка каскадируется.

**Решение:** `parent_zone` как soft constraint, не hard clamp.

```python
if parent_zone:
    p_low, p_high = parent_zone
    # Soft clamp: если zone выходит за parent больше чем на 10% —
    # это可能是 parent BOS сломан. Логируем, но НЕ clamping.
    if zone_high > p_high * 1.10:
        logging.warning("%s zone_high %.1f exceeds parent %.1f by >10%% — possible parent BOS break",
                        tf, zone_high, p_high)
        # Не clamping — передаём parent_zone=None следующему ТФ
        parent_zone = None  # Цепочка прерывается
    elif zone_low < p_low * 0.90:
        logging.warning("%s zone_low %.1f below parent %.1f by >10%% — possible parent BOS break",
                        tf, zone_low, p_low)
        parent_zone = None
    else:
        zone_high = min(zone_high, p_high)
        zone_low = max(zone_low, p_low)
```

Если младший ТФ вышел за parent на >10% — это сигнал что parent BOS сломан. Цепочка прерывается, младший ТФ анализируется независимо. Это предотвращает каскадную ошибку.

### Предложение 3: recent 40% заменить на BOS-zone

Сейчас в benchmark_zigzag:
```python
cutoff_idx = int(len(df) * 0.6)
recent_h = [p["price"] for p in swing_points if p["type"] == "high" and p["index"] >= cutoff_idx]
```

После T1+T2 это не нужно — `analyze_tf_structure` уже даёт zone из BOS + curr_structure. Строки 366-378 в benchmark_zigzag (recent 40% pivots) можно удалить. Zone берётся исключительно из structure.

### Предложение 4: Убрать min-span после top-down

Когда zones = structural range после BOS (top-down) — min-span не нужен по определению. Структурный range >= 2 пивота * ATR distance. Если BOS есть — zone осмысленная.

Но min-span полезен как **safety net** для LLM-зон (фаза 1-2). Оставить, но понизить пороги после top-down:
```python
# После top-down — structure zone уже валидна
# min-span только для LLM-зон которые отличаются от structure
```

---

## Итог

- **Реализуемо** как расширение `core/structure.py`, 3-4 коммита для core
- **parent_zone** = параметр + soft clamp (10% tolerance)
- **BOS** = close-only (уже работает)
- **Накопление** = 3+ пивота без обновления zone bounds
- **_enforce_zone_uniqueness** = изменить: confluence вместо расширения
- **Цели** = parent boundaries в StructureAnalysis.targets
- **Объём у уровня** = отдельная утилита, не блокирует top-down

**Риск #1:** BOS false positive → cascade. Mitigation: soft clamp + chain break at 10%.
**Риск #2:** D1 data insufficient (200 свечей = ~200 дней для 1d). Mitigation: окно 250+ для D1 (уже есть в динамических лимитах).

Готов начать с T1 (parent_zone + clamp) сразу после подтверждения. T1-T2 — один коммит, минимальные изменения.

Жду решения.

— Super Z