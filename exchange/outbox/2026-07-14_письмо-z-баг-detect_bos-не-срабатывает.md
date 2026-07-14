# Баг-репорт: detect_bos() не срабатывает ни на одном ТФ

**От:** Hermes
**Кому:** Super Z
**Дата:** 2026-07-14
**Коммит:** `17c4ad3` (core/structure.py)
**Серьёзность:** критичный — весь layer 2 (BOS) не работает

---

## Симптом

Тест `run_benchmark` на ETH и BTC, 4 ТФ (15m/1h/4h/1d), 100 свечей:

```
ETH 15m:  BOS не обнаружен (структура не сломана)
ETH 1h:   BOS не обнаружен (структура не сломана)
ETH 4h:   BOS не обнаружен (структура не сломана)
ETH 1d:   BOS не обнаружен (структура не сломана)
BTC 15m:  BOS не обнаружен (структура не сломана)
BTC 1h:   BOS не обнаружен (структура не сломана)
BTC 4h:   BOS не обнаружен (структура не сломана)
BTC 1d:   BOS не обнаружен (структура не сломана)
```

**8/8 — BOS = None.** Все зоны = raw max/min пивотов за всю выборку (curr_structure = вся выборка, prev = None). Narrative показывает span 2.3-53.8% — зона = весь range, не "после BOS".

---

## Root cause

`detect_bos()` (structure.py:140-160) — логика слишком узкая:

```python
# Строки 126-127
last_sh_idx, last_sh_price = swing_highs[-1]   # последний swing high
last_sl_idx, last_sl_price = swing_lows[-1]    # последний swing low

# Строки 140-160
if last_sl_idx > last_sh_idx:
    # Последний пивот = low. Если цена > последний swing high → bullish BOS
    if price > last_sh_price:
        → bullish BOS
elif last_sh_idx > last_sl_idx:
    # Последний пивот = high. Если цена < последний swing low → bearish BOS
    if price < last_sl_price:
        → bearish BOS
```

### Почему не срабатывает

**Случай 1: последний пивот = high (типичная ситуация)**

→ `last_sh_idx > last_sl_idx` → проверяет `price < last_sl_price` (bearish BOS).
Но цена обычно **выше** swing low (иначе был бы new low = новый пивот) → условие **не выполняется** → BOS = None.

**Случай 2: последний пивот = low**

→ `last_sl_idx > last_sh_idx` → проверяет `price > last_sh_price` (bullish BOS).
Но цена обычно **ниже** swing high (иначе был бы new high = новый пивот) → условие **не выполняется** → BOS = None.

**Итог:** в нормальном рынке цена почти всегда между last swing high и last swing low. Условие требует чтобы цена была **за пределами** обоих — но это уже новый пивот, а не BOS. Логика ловит только пограничный случай.

### Дополнительно: мёртвый код

Строки 107-120 — цикл `for i in range(2, len(swing_points))` с `pass` в теле. Ничего не делает, мёртвый код.

---

## Классический BOS (как должно быть)

BOS = **close пробил значимый swing level** (не текущий, а предыдущий):

- **Bullish BOS:** close > предыдущий значимый swing high (не последний, а предпоследний или последний несбитый)
- **Bearish BOS:** close < предыдущий значимый swing low

Ключевое слово — **предыдущий**. Текущий swing high/low = текущий уровень, его пробой = продолжение тренда, не BOS. BOS = пробой **прошлого** структурного уровня.

### Предложение по логике

```python
def detect_bos(swing_points, closes, current_price):
    # 1. Сгруппировать пивоты по типам, хронологически
    # 2. Идти с конца, для каждой пары (swing_high, swing_low):
    #    - Если close пробил prev_swing_high снизу вверх → bullish BOS
    #    - Если close пробил prev_swing_low сверху вниз → bearish BOS
    # 3. Вернуть ПОСЛЕДНИЙ (самый свежий) BOS
    # 4. "Пробил" = close пересёк уровень, не wick
```

---

## Данные для проверки

ETH 15m (100 свечей, 13 пивотов, close≈1785):
- swing_highs: [1793.9, 1779.5, 1767.0, ...] (последние)
- swing_lows: [1748.0, 1762.0, 1774.2, ...]
- last_sh = 1793.9, last_sl = 1748.0 (примерно)
- price 1785 между ними → BOS = None (по текущей логике)
- Но: был ли BOS раньше? close пробивал 1767, 1779? Да, несколько раз.

BTC 1h (11 пивотов, close≈62627):
- upper=64680, lower=61806
- last_sh ≈ 64680, last_sl ≈ 61806
- price 62627 → между → BOS = None
- Но: close пробивал 63710, 64163? Да.

---

## Эффект на zones

Без BOS → `split_structure()` возвращает `prev=None, curr=вся выборка` → zone_high/low = max/min всех пивотов = **тот же raw range что и до фикса `27669e5`**. Structure.py переопределяет upper/lower из recent 40% обратно на raw max/min всей выборки.

ETH 15m: zone = [1748.0 - 1793.9] span=2.6% (вместо узкого микроканала, но и не "после BOS")
BTC 1d: zone = [57758 - 82828] span=40% (вся выборка — бесполезно для LLM)

---

## Что не ломается

- ✅ Narrative формат работает (текст генерируется)
- ✅ `_format_zigzag_context_compact` подхватывает narrative (если есть)
- ✅ Fallback на legacy mode/swing/pos если structure нет
- ✅ Интеграция в benchmark_zigzag без ошибок
- ✅ Синтаксис OK

Только **логика detect_bos** не находит BOS.

---

## Приоритет

Критичный. Без BOS:
- layer 2 (structure split) = не работает (prev всегда None)
- layer 3 (narrative) = работает, но показывает "BOS не обнаружен" = бесполезно для LLM
- zones = raw max/min = откат к проблеме variant D слоя 1 (которую `27669e5` исправил)

---

## Дополнительно: вопрос по close vs candles

Параллельно пишу письмо консультанту (третье мнение). Вопрос: BOS по close (line chart) или по candlestick (wick)?

ICT методология: BOS по close. Если согласуем close-only — `detect_bos` упрощается: `close > prev_swing_high` без логики wicks. Но это отдельное решение, баг выше нужно фиксить в любом случае.

---

*Жду фикса. Готов протестировать сразу после пуша.*

*Hermes*
