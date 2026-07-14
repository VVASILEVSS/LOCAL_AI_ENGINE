# Concept Proposals: Open-Source SMC/Market Structure Libraries

**Дата:** 2026-07-14 (обновлён)
**Проект:** LOCAL_AI_ENGINE
**Ветка:** fix/zones-sticking (b26c43d)

## Сводка находок

Поиск по GitHub API + анализ README/кода. 9 запросов, 15+ репозиториев.

---

## 1. fortunato/pymarket-structure ⭐ ПРИОРИТЕТ 1

| | |
|---|---|
| Stars | 9 |
| Forks | 2 |
| Lang | Python |
| PyPI | `pip install market-structure` |
| Import | `market_structure` |
| Updated | 2026-07-11 |

### Что есть

- **67 колонок `ms_*`** для DataFrame — wave identity, trend structure, pullback metrics, divergence, support/resistance zones, zone lifecycle
- **Body-anchored zones** (close как settlement price, не wick) — академически правильнее нашего (мы берём high/low)
- **MTF lookahead prevention** — shift HTF на 1 период: `htf_values.shift(1)`. В live режиме стратегия видит структуру от последней *закрытой* HTF свечи, а не формирующейся
- **Zone quality score [0, 10]** — composite: overlap count, double-bottom/top, ATR-relative width, recency decay, touch count
- **TSI histogram** (True Strength Index) — осциллятор для определения волн (не ZigZag)
- **Double top/bottom detection** — ATR-based tolerance + body overlap check
- **Bearish/bullish divergence** — price vs histogram momentum
- **Three-push exhaustion pattern** — diminishing amplitude, convergent spacing
- **Wave metrics** — amplitude, slope, volume ratio, amplitude ratio
- **Freqtrade integration** — `attach_market_structure(df)` → 67 колонок одним вызовом
- **Live demo** — интерактивный chart viewer на GitHub Pages

### Backtest результат

| Metric | With MS filter | Without MS |
|---|---|---|
| Total profit | **+39.85%** | +22.59% |
| Profit factor | **1.50** | 1.21 |
| Sharpe | **0.94** | 0.58 |
| Max drawdown | **17.76%** | 21.75% |
| Trades | 164 | 208 |
| Win rate | 36.0% | 31.7% |

В нисходящем рынке (-14.91%). Фильтр блокировал 44 низкокачественных входа.

### Что почерпнуть

1. **MTF lookahead prevention pattern** — shift HTF на 1 период. КРИТИЧНО для честного backtest.
2. **Body-anchored zones** — min(anchor.open, anchor.close) вместо wick low/high
3. **Zone quality score** — composite метрика, можно добавить в prompt context для LLM
4. **Wave registry** — вместо ZigZag определять волны по TSI histogram sign-flip
5. **67 колонок** — готовая фича-инжиниринг база для LLM контекста или backtest

### Риски

- Мало звёзд (9) — но качественный код (ruff, pyright strict, pre-commit, CI)
- TSI-based wave detection может отличаться от ZigZag — нужно сравнить визуально
- Freqtrade-specific API — нужно адаптировать под наш pipeline

---

## 2. joshyattridge/smart-money-concepts ⭐ ПРИОРИТЕТ 2

| | |
|---|---|
| Stars | 1855 |
| Forks | 803 |
| Lang | Python |
| PyPI | `pip install smartmoneyconcepts` |
| Import | `smartmoneyconcepts` |
| Updated | 2026-07-13 |

### Что есть (pip install — готовая библиотека)

- **FVG** (Fair Value Gap) — join_consecutive для merge
- **Swing Highs/Lows** — swing_length lookback/forward
- **BOS/CHoCH** — Break of Structure / Change of Character, close_break option
- **Order Blocks** — OBVolume, Percentage (strength), close_mitigation
- **Liquidity** — equal highs/lows within range_percent, swept index
- **Previous High/Low** — по ТФ (15m, 1H, 4H, 1D, 1W, 1M), broken flags
- **Sessions** — Sydney, Tokyo, London, New York, kill zones, custom
- **Retracements** — Direction, CurrentRetracement%, DeepestRetracement%

### Что почерпнуть

1. **BOS/CHoCH через 4-pivot swing pattern** — проще/надёжнее нашего ZigZag-based подхода. `swing_highs_lows(ohlc, swing_length=50)` → `bos_choch(ohlc, swing_highs_lows, close_break=True)`. Сравнить с нашим подходом
2. **Liquidity** — equal highs/lows detection с swept index. Можно добавить в prompt context
3. **Sessions** — готовые kill zones (Asian, London open, NY kill zone). У нас сессии определены вручную в промпте, можно заменить на код
4. **Order Blocks** с volume strength — можно использовать как зоны ликвидности
5. **Previous High/Low** — готовый fallback для zone определения по ТФ

### Риски

- `join_consecutive`, `close_break`, `close_mitigation` — параметры нужно тюнить
- swing_length=50 по умолчанию — может быть слишком медленный для M15
- Нет MTF из коробки — нужно вызывать для каждого ТФ отдельно

---

## 3. Prasad1612/smart-money-concept ⭐ ПРИОРИТЕТ 3

| | |
|---|---|
| Stars | 36 |
| Forks | 7 |
| Lang | Python |
| PyPI | `pip install smart-money-concept` |
| Updated | 2026-07-09 |

### Что есть

- BOS/CHoCH, Order Blocks, FVG, EQH/EQL
- **Premium & Discount Zones** — Dynamic equilibrium mapping (НЕТ у нас)
- CLI support — batch run multiple stocks
- Matplotlib visualization
- Yahoo Finance integration (можно адаптировать под Binance)

### Что почерпнуть

1. **Premium/Discount zones** — можно добавить как контекст в LLM prompt. ICT-концепт: цена выше 50% = premium (дорого), ниже 50% = discount (дешево)
2. **EQH/EQL** (Equal Highs/Lows) — liquidity points, можно использовать для confluence

---

## 4. GifariKemal/xaubot-ai ⭐ РЕФЕРЕНС (MT5, не наш стек)

| | |
|---|---|
| Stars | 58 |
| Forks | 27 |
| Lang | Python |
| Updated | 2026-07-13 |

### Что есть

- XGBoost ML (37 фичей) + SMC + HMM regime detection
- Smart risk management: ATR SL, Kelly position sizing, daily loss limits
- Session awareness: Sydney/London/NY
- Auto-retrain при смене рыночных условий
- Telegram notifications + Next.js dashboard

### Что почерпнуть (идеи, не код)

1. **HMM regime detection** — Hidden Markov Model 3-state (trending/ranging/volatile). У нас тренд определяется LLM визуально, HMM может быть объективнее
2. **Kelly criterion position sizing** — можно добавить в risk_management
3. **14 entry filters** — checklist фильтров перед входом. У нас валидация в пост-обработке, можно формализовать
4. **Auto-retrain trigger** — когда условия рынка меняются. Можно адаптировать: при смене тренда пересматривать baseline

---

## 5. Дополнительные находки

### manuelinfosec/profittown-sniper-smc (68⭐)
ICT SMC Sniper Bot — мало документации, стоит мониторить.

### rabichawila/smart-money-py (18⭐)
Smart money concept in Python — чистая реализация, можно изучить код для идей.

### Louisjzhao/smc-toolkit (8⭐)
BOS, CHoCH, FVG, OB, swing s. Мало звёзд, но чистый код.

### VanHes1ng/Cryptocurrencies-volume-profile (7⭐)
Volume Profile web app — можно изучить VP approach.

---

## План внедрения

### Phase 1: Установка и сравнение (1 день)

```bash
pip install market-structure smartmoneyconcepts
```

- Установить обе библиотеки в venv
- Прогнать на исторических данных BTCUSDT/ETHUSDT/XAUTUSDT
- Сравнить зоны: наши vs smc vs pymarket-structure
- Визуально сопоставить с графиками Binance

### Phase 2: Интеграция в pipeline (2-3 дня)

**Приоритет A — pymarket-structure:**
- MTF lookahead prevention pattern → адаптировать для нашего `_fill_missing_tf_zones`
- Body-anchored zones → опционально заменить wick-based
- Zone quality score → добавить в LLM prompt context
- 67 ms_* колонок → использовать как feature engineering для backtest

**Приоритет B — smartmoneyconcepts:**
- BOS/CHoCH → сравнить с нашим ZigZag, заменить если лучше
- Liquidity → добавить в prompt context
- Sessions → заменить ручное определение в промпте
- Order Blocks → как доп. зоны

### Phase 3: Бэктест и валидация (1-2 дня)

- Прогнать backtest с новыми зонами
- Сравнить win rate, profit factor, drawdown
- Если улучшение — мержить в main

---

## Ссылки

- https://github.com/fortunato/pymarket-structure
- https://github.com/joshyattridge/smart-money-concepts
- https://github.com/Prasad1612/smart-money-concept
- https://github.com/GifariKemal/xaubot-ai
- https://fortunato.github.io/pymarket-structure/ (live demo)
- https://github.com/fortunato/pymarket-structure/blob/main/docs/freqtrade-columns.md (67 колонок)

---

*Hermes, 2026-07-14*
