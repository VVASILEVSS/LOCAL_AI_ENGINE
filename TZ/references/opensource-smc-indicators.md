# Референс: open-source SMC/structure индикаторы

> Найдено 14.07.2026 при поиске готовых решений для SMC top-down концепции.
> Цель — изучить чужие подходы, взять лучшее, не копировать вслепую.

---

## Топ-3 кандидата для изучения

### 1. 🥇 `joshyattridge/smart-money-concepts` (1855⭐, Python)
- **URL:** https://github.com/joshyattridge/smart-money-concepts
- **PyPI:** `pip install smartmoneyconcepts`
- **Язык:** Python (pandas/numpy, без ML)
- **Что реализовано:**
  - `swing_highs_lows(ohlc, swing_length=50)` — rolling max/min window
  - `bos_choch(ohlc, swing_highs_lows, close_break=True)` — BOS + CHoCH через pattern matching 4 pivots
  - `ob(ohlc, swing_highs_lows)` — Order Blocks + volume
  - `liquidity(ohlc, swing_highs_lows, range_percent=0.01)` — уровни ликвидности
  - `previous_high_low(ohlc, time_frame)` — предыдущий H/L по ТФ
  - `retracements(ohlc, swing_highs_lows)` — % retracement от swing
  - `fvg(ohlc)` — Fair Value Gap
  - `sessions(ohlc, session)` — торговые сессии
- **Подход к BOS:** паттерн из 4 pivots `[-1,1,-1,1]` (bullish) или `[1,-1,1,-1]` (bearish) + проверка уровней. Простой и эффективный.
- **Подход к swing:** `swing_length` = окно lookback+forward. Удаляет consecutive same-type swings (оставляет крайний).
- **Дельность для нас:** алгоритм BOS через 4-pivot pattern — проще и надёжнее нашего. Стоит изучить.

### 2. 🥈 `fortunato/pymarket-structure` (9⭐, Python, но серьёзный)
- **URL:** https://github.com/fortunato/pymarket-structure
- **PyPI:** `pip install market-structure`
- **Язык:** Python (pandas/numpy, Ruff, pre-commit, CI)
- **Что реализовано:**
  - **Multi-timeframe!** `mtf.py` — `attach_market_structure_mtf(df, htf, ltf)` — HTF→LTF projection с **lookahead prevention** (shift на 1 HTF период)
  - Support/resistance zones (body-anchored + wick extrema)
  - Structure breaks
  - Wave detection (TSI histogram sign-flip → wave boundaries)
  - 67 `ms_*` колонок проецируются на DataFrame
  - Freqtrade integration
- **Backtest:** TSI crossover + MS filter = +39.85% vs +22.59% без фильтра. Profit factor 1.50 vs 1.21.
- **Подход к MTF:** resample LTF→HTF, run structure on HTF, shift forward 1 HTF, forward-fill merge.
- **Дельность для нас:** их MTF подход = наш top-down nesting, но с правильным lookahead prevention. Стоит изучить `mtf.py`.

### 3. 🥉 `Prasad1612/smart-money-concept` (36⭐, Python)
- **URL:** https://github.com/Prasad1612/smart-money-concept
- **PyPI:** `pip install smart-money-concept`
- **Что реализовано:** BOS/CHoCH, Order Blocks, FVG, EQH/EQL, Premium/Discount zones, matplotlib viz
- **Подход:** использует yfinance (stocks, не crypto). Полный pipeline с CLI.
- **Дельность для нас:** Premium/Discount zones — концепция которую мы не реализовали. Можно позаимствовать.

---

## Что НЕ нашли (но искали)

- **Трендовые линии (trendline) с открытым кодом** — GitHub search по "trendline indicator python trading" дал 0 результатов. Трендовые линии — ручная работа везде.
- **Top-down nesting D1→H4→H1→M15** — только pymarket-structure делает MTF, но через resample+projection, а не через настоящий nested analysis как у нас.

---

## Дельность для LOCAL_AI_ENGINE

| Что изучить | Откуда | Зачем |
|---|---|---|
| **BOS через 4-pivot pattern** | `joshyattridge/smart-money-concepts` | Возможно проще/надёжнее нашего `detect_bos` |
| **MTF lookahead prevention** | `fortunato/pymarket-structure/mtf.py` | shift HTF на 1 период — prevents lookahead bias |
| **Premium/Discount zones** | `Prasad1612/smart-money-concept` | новые зоны, которых нет у нас |
| **Order Blocks + volume** | `joshyattridge/smart-money-concepts` | T6 volume_at_level может использовать OB logic |
| **Wave detection via TSI sign-flip** | `fortunato/pymarket-structure/hydrate.py` | альтернатива ZigZag для wave boundaries |

---

## Трендовые линии: что все используют

Поскольку open-source trendline индикаторов для Python практически нет, наш T10-T11 (`detect_trend_lines` + `check_line_break`) — **новая territory**. Спека согласована Hermes ↔ Super Z:
- min 3 pivots LH/HL series
- numpy.polyfit + r²
- log returns normalized to daily
- potential/true/false breakout тройка

Это уникальная реализация. Возный делает это руками, мы — алгоритмически.

---

*Документ создан 2026-07-14. Референс для изучения при реализации T10-T11.*
