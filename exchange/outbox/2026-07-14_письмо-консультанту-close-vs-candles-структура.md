# Письмо консультанту: структурный анализ LOCAL_AI_ENGINE — линейный график vs свечной

**От:** Hermes (ассистент-разработчик)
**Кому:** Консультант (третье мнение)
**Дата:** 2026-07-14
**Проект:** LOCAL_AI_ENGINE — автоматический мультитаймфрейм анализ крипторынка (BTC/ETH/XAUT)

---

## Контекст проекта

LOCAL_AI_ENGINE — Telegram-бот + Flask dashboard. Анализирует криптовалюту по 4 таймфреймам (15m / 1h / 4h / 1D) через гибридный pipeline:

1. **ZigZag benchmark** (`benchmark_zigzag.py`) — определяет пивоты, swing direction, market mode по каждому ТФ
2. **Volume Profile** (`volume_profile.py`) — POC, уровни по объёму (fallback)
3. **LLM анализ** — облачная GLM (Alibaba) получает ZigZag контекст + VP + свечи → выдаёт JSON со структурой, зонами, сигналами
4. **Post-processing** — валидация зон (min-span, uniqueness, nesting), fallback на VP если зона невалидна

Проблема которую решаем: **LLM выдаёт узкие/прилипшие зоны** (M15 зона = микроканал последних 5-10 свечей, часто копирует зону старшего ТФ).

---

## История проблемы (кратко)

### Что обнаружили

1. **ZigZag был фейком** (`benchmark_zigzag.py:247-257`): каждая 5-я свеча alternating high/low, не реальные пивоты. `upper = np.max(highs)`, `lower = np.min(lows)` за 200 свечей = raw экстремумы.

2. **LLM копирует зоны из промпта** (`_format_zigzag_context_compact`): ZigZag контекст в промпте → LLM видит `upper=1848, lower=1748` → повторяет в ответе → `_fill_missing_tf_zones` видит LLM зону → `continue` (не fallback).

3. **Узкие M15 зоны**: сравнили с реальными Binance OHLCV:
   - ETH M15 LLM: 0.67% span vs реальный 20-свечной range 1.14% (1.7x уже)
   - BTC M15 LLM: 0.44% vs реальный 0.93% (2.1x уже)
   - XAUT M15 LLM: 0.80% vs реальный 0.74% (≈равны, но sticking)

### Что сделали (коммиты)

| Коммит | Автор | Что |
|--------|-------|-----|
| `96cff81` | Super Z | Min-span validation: D1≥2.5%, H4≥2%, H1≥1.2%, 15M≥0.8%. Если зона уже → delete → VP/ZigZag fallback. Dual protection (pre-LLM + post-LLM). |
| `27669e5` | Super Z | **Реальные пивоты** `_find_real_pivots` (depth-based local extremum). Адаптивный depth: D1=8, H4=6, H1=5, 15m=3, 5m=2. ATR distance filter (k=0.5). Dedup (0.1%). upper/lower из recent 40% пивотов, не raw max/min. |
| `8436c83` | Super Z | `_enforce_zone_uniqueness` — expand parent zone если дочерние прилипли. D1±2.5%, H4±1.5%, H1±0.75%, 15M±0.4%. |
| `449e7e0` | Hermes | Матрёшка fix — зоны не должны быть идентичными между ТФ |

### Roadmap (variant D — структурное окно)

| Слой | Что | Статус |
|------|-----|--------|
| 1 | Реальные пивоты | ✅ `27669e5` |
| 2 | BOS detection + structure split (`core/structure.py`) | pending (Super Z) |
| 3 | Structure narrative в промпте (замена `_format_zigzag_context_compact`) | pending (Super Z) |
| 4 | Разметка на графике (ZigZag line + BOS vertical + zone rect) | pending (Super Z) |
| 5 | Адаптивные min-span пороги (k × ATR) | pending |

---

## Идея для обсуждения: линейный график (close) vs свечной (OHLC)

### Гипотеза

Перейти с свечного графика (highs/lows) на линейный (close-only) для **определения структуры**:

**Текущий подход:**
```python
_find_real_pivots(highs, lows, depth)  # два массива
```

**Предлагаемый:**
```python
_find_real_pivots(closes, depth)  # один массив
```

### Аргументы ЗА close-only

1. **Меньше шума.** Wicks (тени) = hunt/stop-run, часто ложные swing points. Close = "согласие рынка". depth=3 на 15m+ даёт 13-18 пивотов по H/L → 5-10 по close.

2. **BOS традиционно по close.** ICT методология: BOS = цена **закрылась** выше/ниже уровня. Line chart естественный fit.

3. **Меньше ложных пробоев.** Wick above resistance + close back = sweep. На line chart не виден → меньше шума в structure narrative.

### Аргументы ПРОТИВ

1. **Потеря liquidity grab detection.** Sweep = wick above + close back. На close-only невидимо. Но это **P2/P3 roadmap** (Order Block, FVG), не текущий слой.

2. **Потеря wick confluence.** Длинная тень = реакция на уровень. На close невидима.

3. **Volume Profile не меняется** — работает по raw prices, не зависит от графика.

### Гибрид (наш вариант)

| Слой | Источник | Зачем |
|------|----------|-------|
| Пивоты + BOS + narrative | **close** | Чистая структура |
| Zone upper/lower | **close pivots** | Структурные уровни |
| Liquidity sweep (P2) | **wicks** (позже) | Stop hunts |
| Volume Profile | raw prices | Не меняется |

### Вопрос к консультанту

1. Согласны ли вы что close-only для структуры — оправданный выбор для SMC-based анализа?
2. Есть ли риск потерять критичную информацию при отказе от wicks на этапе определения пивотов?
3. Гибрид (close для структуры + wicks для исполнения) — разумный компромисс, или вы бы рекомендовали другой подход?
4. Какой depth порекомендовали бы для close-only пивотов по каждому ТФ (сейчас для H/L: D1=8, H4=6, H1=5, 15m=3)?

---

## Отчёт по последним сканам бота (14.07.2026, ручной режим)

Автоскан выключен. Три скана запущены вручную через /scan в Telegram. Бот: `@my_hermes_lokal_ai_bot`, модель: `glm-5.2-fast-preview` (Alibaba cloud), self-consistency 2 прогона.

### Скан 1: BTC/USDT (11:51 UTC)

```
Цена: 62762.25
Тренд: Down (1D bearish_extension, 4H bearish_recovery)
LTF: Balance (15m боковое движение, объём 1.5x растёт)
ABC риск: down (коррекция вверх = волна B → ожидается C вниз)
```

**Зоны по ТФ:**
| TF | Zone | Span | min-span | Статус |
|----|------|------|----------|--------|
| D1 | [60005.95 - 66310.15] | 6304.20 (10.04%) | ≥2.5% ✅ | OK |
| H4 | [61544.56 - 64692.83] | 3148.27 (5.01%) | ≥2% ✅ | OK |
| H1 | [62519.16 - 64284.51] | 1765.35 (2.81%) | ≥1.2% ✅ | OK |
| M15 | [62519.16 - 63369.48] | 850.32 (1.35%) | ≥0.8% ✅ | OK, но... |

⚠️ **Partial stick**: M15 lower = H1 lower = `62519.16`. Upper разные, span M15 проходит min-span, но lower bound идентичен. LLM копирует часть уровней из ZigZag контекста в промпте. Это **не полностью решено** — ждём коммит 3 (structure narrative).

**Confluence**: 6 уровней, max count=2 (1H+15M, 1D+4H). Нет high-priority confluence (count≥3).

**Сигнал**: No signal. Цена между уровнями, объём нейтральный, пробоя нет, скрытая медвежья дивергенция. Confidence: Low.

---

### Скан 2: ETH/USDT (11:52 UTC)

```
Цена: 1783.91
Тренд: Down (1D bullish_recovery, 4H bearish_trend)
LTF: Balance (15m боковое движение, объём 0.71x падает)
Накопление: Accumulation (HTF у 50% Фибо, LTF баланс внутри зоны)
ABC риск: down
```

**Зоны по ТФ:**
| TF | Zone | Span | min-span | Статус |
|----|------|------|----------|--------|
| D1 | [1670.60 - 1875.75] | 205.15 (11.50%) | ≥2.5% ✅ | OK |
| H4 | [1713.44 - 1830.00] | 116.56 (6.54%) | ≥2% ✅ | OK |
| H1 | [1748.00 - 1820.30] | 72.30 (4.06%) | ≥1.2% ✅ | OK |
| M15 | [1763.67 - 1784.73] | 21.06 (1.18%) | ≥0.8% ✅ | OK ✅ |

✅ **Все зоны уникальные**, M15 ≠ H1. Span M15 = 1.18% (был 0.67% до фикса `27669e5` — **1.76x шире**). Реальные пивоты сработали: `pivot_count: 13` на 15m (было фиксированное `len(df)//5 = 20` фейк).

**Confluence**: 1763.67 (1H+15M, support, prox=0.91), 1820.30 (4H+1H, resistance, prox=1.00). Логичные уровни.

**Сигнал**: No signal. Цена между support 1763.67 и resistance 1784.73. Объём падает, пробоя нет. Confidence: Low.

**State**: `updated_inside_range` — цена внутри предыдущей зоны, обновил M15 ref.

---

### Скан 3: XAUT/USDT (11:54 UTC)

```
Цена: 4023.00
Тренд: Down (1D bearish_extension, 4H bearish_extension)
LTF: Balance (15m боковое движение, объём 0.41x падает)
Накопление: Accumulation (между support 4019 и resistance 4040)
ABC риск: down
```

**Зоны по ТФ (LLM raw output):**
| TF | Zone | Span | min-span | Статус |
|----|------|------|----------|--------|
| D1 | [3981.47 - 4191.19] | 209.72 (5.21%) | ≥2.5% ✅ | OK |
| H4 | [4008.94 - 4119.50] | 110.56 (2.75%) | ≥2% ✅ | OK |
| H1 | [3978.87 - 4091.26] | 112.39 (2.79%) | ≥1.2% ✅ | OK |
| M15 | [4008.94 - 4060.08] | 51.14 (1.27%) | — | VP fallback |

**Min-span валидация сработала:**
```
POST-LLM: 15M zone too narrow: 0.5817% < min 0.8000%, removing
DASHBOARD: filled missing 15M zone from volume_profile for XAUTUSDT
```

LLM выдал M15 span=0.58% → удалил → VP fallback заполнил [4008.94 - 4060.08] span=1.27%. **Цепочка защиты работает** ✅

**Confluence**: 4008.94 (1D+4H+1H+15M, **high priority**, count=4, support, prox=0.83). Сильный уровень — 4 ТФ сходятся.

**Сигнал**: No signal. Объём 0.41x (очень низкий), сессия Asia. Hidden bullish divergence на 15m, liquidity inside_pool, buy_side_liquidity. Confidence: Low.

---

## Сводка по 3 сканам

| Критерий | BTC | ETH | XAUT |
|----------|-----|-----|------|
| M15 span | 1.35% ✅ | 1.18% ✅ | 1.27% (VP) ✅ |
| M15 ≠ H1 | ⚠️ partial stick (lower идентичен) | ✅ уникальные | ✅ уникальные |
| Min-span | OK | OK | сработал → VP fallback |
| Реальные пивоты | ✅ (count=17 на 15m) | ✅ (count=13) | ✅ (count=18) |
| Сигнал | No signal | No signal | No signal |
| Confidence | Low | Low | Low |

**Общая оценка:**
- ✅ Реальные пивоты работают (`27669e5`)
- ✅ Min-span валидация работает (`96cff81`)
- ✅ VP fallback работает
- ⚠️ Partial stick в BTC (M15/H1 делят lower) — не полностью решён
- ⚠️ Все 3 сигнала = No signal, Low confidence — рынок в балансе, это корректно

---

## Что ждём от консультанта

1. **Мнение по close-only vs candlestick** для определения структуры (см. раздел выше)
2. **Оценка подхода variant D** (поэтапный: пивоты → BOS → narrative → разметка → адаптив min-span) — разумно?
3. **Partial stick в BTC** — как бы вы предложили решать? (M15 lower = H1 lower = 62519.16)
4. **Адаптивные пороги min-span** (k × ATR вместо фиксированных) — стоит ли?
5. **Любые другие замечания** по архитектуре pipeline или подходу

---

*С уважением,*
*Hermes*
*LOCAL_AI_ENGINE team*
