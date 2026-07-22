# Письмо Z: Исследование 3 SMC индикаторов — ключевой инсайт от YeohJooYam

## TL;DR

Из 3 предложенных индикаторов только один (YeohJooYam) содержит реальный MQL5-код.
**Главный инсайт:** YeohJooYam хранит **последние 2 swing** в state machine, и зона
`prev_swing..curr_swing` = ровно 2-5% без всякого parent clamp.

| Индикатор | Реальный SMC код? | Вердикт |
|-----------|------------------|---------|
| YeohJooYam — Market Structure SMC | ✅ ДА (284 строки MQL5) | ⚠️ Концепт fractal+state machine |
| Abdul Qadir — Market Structure Mapper | ❌ .ex5 (закрытый бинарь) | ⚠️ Только визуальный референс |
| VelmoPk — Smart Money Concepts | ❌ C# WinForms с захардкоженными ценами | ❌ Мусор |

## 1. YeohJooYam — единственный с реальным кодом

**URL:** https://www.mql5.com/en/code/74575
**Файл:** `LW_MarketStructure_SMC.mq5` (284 строки, v1.10, 2026-07-02)
**Лицензия:** MQL5 CodeBase (не MIT, но свободное использование)

Полный исходник сохранён: `C:\Users\User\smc_research\LW_MarketStructure_SMC.mq5`

### 1.1 Swing detection — N-bar fractal (проще чем ZigZag)

```mql5
input int InpSwingN = 5;  // Fractal bars each side

// Pass 1: mark fractal swing highs/lows
for(int i = start; i < rates_total - InpSwingN; i++) {
   bool isHigh = true, isLow = true;
   for(int k = 1; k <= InpSwingN; k++) {
      if(high[i] <= high[i - k] || high[i] <= high[i + k]) isHigh = false;
      if(low[i]  >= low[i - k]  || low[i]  >= low[i + k]) isLow  = false;
   }
   if(isHigh) SwingHighBuf[i] = high[i];
   if(isLow)  SwingLowBuf[i]  = low[i];
}
```

`InpSwingN=5` = типичный 5-барный фрактал. Больше N = меньше swing, но крупнее.
**Без STRUCT_WINDOW, без ZigZag** — простая и стабильная логика.

### 1.2 BOS/CHoCH — state machine (КЛЮЧЕВОЕ)

```mql5
int    trend      = 0;     //  1 = up, -1 = down, 0 = neutral
double refHigh    = 0.0;   // last swing-high to break (bullish target)
double refLow     = 0.0;   // last swing-low  to break (bearish target)
double prevHigh   = 0.0;   // ⭐ ПРЕДШЕСТВУЮЩИЙ swing-high
double prevLow    = 0.0;   // ⭐ ПРЕДШЕСТВУЮЩИЙ swing-low

for(int i = start + InpSwingN; i < rates_total; i++) {
   int j = i - InpSwingN;
   if(SwingHighBuf[j] > 0.0) {
      prevHigh = refHigh;          // ⭐ сдвиг: текущий → предыдущий
      refHigh  = high[j];          // новый текущий
      haveHigh = true;
   }
   if(SwingLowBuf[j] > 0.0) {
      prevLow = refLow;
      refLow  = low[j];
      haveLow  = true;
   }

   // Break of structure (close-confirmed)
   if(haveHigh && close[i] > refHigh) {
      bool isBOS = (trend != -1);   // BOS if trend was up/neutral; CHoCH if was down
      DrawBreak(...);
      trend    = 1;
      haveHigh = false;              // level consumed
   }
}
```

**Ключевой момент:** state machine хранит **2 последних swing** (`prevHigh` + `refHigh`).
После BOS он обнуляет `haveHigh`, но **значения prevHigh/refHigh остаются**.

### 1.3 Зона `prevHigh..refHigh` = 2-5% (КЛЮЧЕВОЙ ИНСАЙТ)

Если взять `prevHigh..refHigh` (или `prevLow..refLow`) как зону — это диапазон
2 swing-ов. На H1 это ~2-5% (зависит от волатильности). **Это ровно наша цель.**

- Не нужен parent clamp (каждый TF считает свою 2-swing зону)
- Не нужен _STRUCT_WINDOW (fractal сам ограничивает lookback)
- Не нужен Variant D `max(curr, last 4)` — 2 swing достаточно

### 1.4 Что НЕ подходит у YeohJooYam

- ❌ Order Block = одна противоположная свеча перед пробоем (микро ~0.17%)
- ❌ FVG = 3-свечной imbalance (микро ~0.1-0.3%)
- ❌ Premium/Discount **не реализован** (упомянут в комментариях, кода нет)
- ❌ Нет MTF — анализирует только текущий ТФ

## 2. Abdul Qadir Memon — закрытый код

**URL:** https://www.mql5.com/en/market/product/183752
**Файл:** только `.ex5` (бинарь), исходников нет

В описании есть:
- Premium/Discount/Equilibrium (потенциально наша зона)
- MTF: M15/H1/H4/D1/W1 + dashboard
- "Strong break" scoring: `InpStrongBreakBodyMultiplier` + `MinScore`

**Проверить код нельзя.** Можно только установить в MT5 как визуальный референс
(бесплатно) и сравнить его Premium/Discount с нашими зонами.

## 3. VelmoPk — фейк

**URL:** https://github.com/VelmoPk/Smart-Money-Concepts-indicator-MT5

README обещает SMC, но внутри:
- Папка MQL5 содержит **C++ реверс-инжиниринг** (Capstone disassembler, ReadProcessMemory)
- Form1.cs = WinForms UI с **захардкоженными ценами** (1.08310, 1.08580)
- Нет ни одного SMC-алгоритма

MIT-лицензия бесполезна — нет кода для портирования.

## 4. Сравнение подходов

| Мы (Variant D) | YeohJooYam |
|----------------|------------|
| ZigZag + BOS, zone = max(curr, last 4 swings) | N-bar fractal, zone = prevHigh..refHigh (2 swing) |
| parent clamp 1H→15M (режет зону обратно в micro) | **нет parent clamp** — каждый TF сам |
| 4 swing lookback фиксирован | 2 swing (последние), всегда актуальны |
| _STRUCT_WINDOW 80-150 candles | InpSwingN=5 fractal, без window |
| POST-LLM clamps (отключены в Variant E Phase 1) | не нужны — зона сразу правильная |

## 5. Рекомендация

На основе исследования предлагаю:

### 5.1 Заменить ZigZag на N-bar fractal

```python
# structure.py — fractal swing detection
def find_fractal_swings(highs, lows, n=5):
    swings_h, swings_l = [], []
    for i in range(n, len(highs)-n):
        if all(highs[i] > highs[i-k] and highs[i] > highs[i+k] for k in range(1, n+1)):
            swings_h.append((i, highs[i]))
        if all(lows[i] < lows[i-k] and lows[i] < lows[i+k] for k in range(1, n+1)):
            swings_l.append((i, lows[i]))
    return swings_h, swings_l
```

Параметр N адаптивный по TF: 15M=3, 1H=5, 4H=7, 1D=10.

### 5.2 Зона = prev_swing..curr_swing (вместо max(curr, last 4))

```python
# Структурная зона = диапазон 2 последних swing
# bull: prevLow..currLow (от FL до FL)
# bear: prevHigh..currHigh
def structural_zone(swings, direction):
    if len(swings) < 2:
        return None
    prev, curr = swings[-2], swings[-1]
    return (min(prev[1], curr[1]), max(prev[1], curr[1]))
```

### 5.3 Убрать parent clamp

YeohJooYam доказывает что каждый TF считает свою зону независимо.
Parent clamp (`structure.py:~467`) — корень проблемы "15M = копия 1H".

### 5.4 BOS state machine с подтверждением по close

```python
# Close-confirmed break (не wick)
if close[i] > refHigh and trend != -1:
    bos = True   # continuation
elif close[i] > refHigh and trend == -1:
    choch = True  # reversal
```

## 6. Итог

- ✅ YeohJooYam даёт паттерн **«последние 2 swing как зона»** — можно адаптировать
- ✅ N-bar fractal проще и стабильнее ZigZag
- ✅ State machine с `prevHigh/refHigh` решает проблему micro/macro
- 🔧 Нужен собственный алгоритм, опираясь на:
  - N-bar fractal (YeohJooYam)
  - BOS/CHoCH state machine (YeohJooYam)
  - Зона = prev_swing..curr_swing (2 swing)
  - **Без parent clamp**

Файлы:
- `C:\Users\User\smc_research\LW_MarketStructure_SMC.mq5` — полный исходник (284 строки)
- `C:\Users\User\smc_research\SMC_INDICATORS_RESEARCH.md` — детальный отчёт (408 строк)

Жду твоего решения. Бот работает на Variant D + POST-LLM OFF (pid 16396, 15min).

— Hermes
