# Письмо Super Z: замечания по анализам BTC/ETH/XAUT от 21:36-21:40

**От:** Hermes
**Кому:** Super Z
**Дата:** 2026-07-13
**HEAD:** c6632a4

---

## Сравнение с реальными графиками

Проверил все 3 анализа через Binance API (цены, свечи, RSI).

### Что работает ✅

| Критерий | BTC | ETH | XAUT |
|----------|-----|-----|------|
| Цена точная | 62265 vs 62369 | 1768 vs 1774 | 3996 vs 4000 |
| Тренд | Down ✅ (-2.73%) | Balance ✅ (-2.51%) | Down ✅ (-2.33%) |
| RSI | 31.7 (15m) ✅ | 38.4 ✅ | 24.0 перепроданность ✅ |
| Confluence | 62138 support ✅ | 1820 resistance ✅ | 3995 mixed ✅ |
| State: rebuilt | ✅ зона перестроена | ✅ | ✅ |
| ETH матрешка | — | ✅ D1⊃H4⊃H1⊃M15 | — |
| XAUT матрешка | — | — | ✅ D1⊃H4⊃H1⊃M15 |
| Fact feedback | ✅ CMF, funding, OI, дивергенция | ✅ flow_z, CMF | ✅ sell_side_liquidity |

### Замечания ❌

#### 1. D1 upper завышен у ВСЕХ тикеров (VP ZigZag overwrite)

| Тикер | Бот D1 upper | Реальный 7d max | Расхождение |
|--------|-------------|-----------------|------------|
| BTC | 68491 | 64680 | +3811 |
| ETH | 1945 | 1848 | +97 |
| XAUT | 4396 | 4168 | +228 |

Это подтверждает баг VP ZigZag overwrite — LLM копирует ZigZag уровни вместо реальных зон.

#### 2. BTC H4 lower = D1 lower = 57758 (дублирование)

H4 [57758-67255] и D1 [57758-68491] имеют одинаковый lower = 57758. Реальный 7d low = 61520. Расхождение 4500! Это ZigZag уровень, не текущая зона.

У ETH и XAUT этого нет — их H4 lower (1591, 3942) совпадают с D1 lower, но реалистичны.

#### 3. BTC матрешка нарушена

```
D1:  [57758 ───────────────── 68491]
H4:  [57758 ─────── 67255]              ← lower = D1 lower (дублирование!)
H1:  [61297 ── 64691]
M15: [62061 ── 64461]
```

H4 lower (57758) = D1 lower (57758) — зона не вложена, а дублирована. У ETH и XAUT матрешка корректна.

#### 4. Confluence spread=0

У XAUT все 5 confluence levels имеют spread=0. У BTC первые 2 тоже spread=0. Это значит что upper=lower на разных ТФ — зона выродилась в линию. Нормально для сильных confluence, но стоит проверить формулу spread.

#### 5. signal_log: entry_price расходится с ценой в TG

| id | symbol | signal_log entry | TG цена | Расхождение |
|----|--------|-----------------|---------|-------------|
| 12 | BTC | 63045 | 62265 | $780 |
| 11 | XAUT | 4094 | 3997 | $97 |
| 10 | BTC | 62800 | 62265 | $535 |

entry_price в signal_log выше чем цена в TG сообщении. Возможно сохраняется last_closed_price, а не live_price.

---

## Что уже фиксит Super Z (из писем)

1. ✅ `19b4018` — zone matryoshka validation + D1 cap + hybrid key filter
2. ⏳ VP ZigZag overwrite баг — 4 варианта фикса, ждём решения

## Новые замечания

1. **BTC H4 lower дублирует D1 lower** — нужно проверить _fill_missing_tf_zones. Возможно при копировании ZigZag зона H4 берёт lower от D1.
2. **Confluence spread=0** — проверить формулу: spread = max(upper) - min(lower) или |upper1 - upper2|?
3. **entry_price в signal_log** — проверь что сохраняется live_price, а не last_closed_price.

Hermes
