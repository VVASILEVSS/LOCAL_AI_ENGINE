# Письмо Z: Variant D — тест показал улучшение, но LLM post-processing срезает обратно

## 1. Variant D — частичный успех ✅

Запушил твой `feb16da` (Variant D). Перезапустил бота. Замеры через 1 цикл:

| Сим | TF | curr-only | **Variant D** | union | цель |
|-----|------|-----------|---------------|-------|------|
| BTC | 1D | 1.39% | **13.56%** ⬆ | 43% | 3-6% ❌ |
| BTC | 4H | 4.93% | **6.12%** ⬆ | 13.5% | 2-5% ❌ (близко) |
| BTC | 1H | 0.17% | **0.51%** ⬆ | 4.93% | 1-2% ❌ (недолет) |
| ETH | 1D | 6.21% | **28.85%** ⬆ | 63% | 3-6% ❌ |
| ETH | 4H | 6.21% | **8.01%** ⬆ | 28.85% | 2-5% ❌ |
| ETH | 1H | 1.18% | **0.72%** ⬇ | 8.01% | 1-2% ❌ (стало МЕНЬШЕ) |
| XAUT | 1D | 6.25% | **6.71%** ⬆ | — | 3-6% ✅ |
| XAUT | 4H | 1.44% | **2.87%** ⬆ | — | 2-5% ✅ |
| XAUT | 1H | 0.49% | **0.49%** = | — | 1-2% ❌ (недолет) |

**XAUT — идеально в цели.** BTC/ETH — лучше curr-only, но 1D/4H ещё широковато,
1H/15M — недолет.

## 2. Root cause недолёта: LLM post-processing срезает обратно

Лог `logs/bot.log` (21:11:08):
```
CONFLUENCE: 15M sticking to 1H but microchannel (0.192% < 0.800%) — removing
POST-LLM: 1H zone too narrow: 0.4835% < min 1.2000%, removing
FALLBACK: 15M zone from ZigZag structure: [4010.00 - 4012.30]
FALLBACK: 1H zone from ZigZag structure: [3998.30 - 4017.70]
```

Variant D расширяет зону в `structure.py` → LLM получает широкую зону →
**`_validate_min_span`** (scheduler.py:1495) срезает обратно если < 1.2% →
**`enforce_zone_uniqueness`** (scheduler.py:1455) удаляет microchannel < 0.8% →
**FALLBACK** берёт ту же зону из ZigZag structure → **петля** → зона не меняется.

## 3. ETH 1H уменьшился (0.72% < 1.18%) — это баг?

Ты писал: `max(curr, last4) = curr если curr широкий`. Но ETH 1H:
- curr-only = 1.18% (до Variant D)
- Variant D = 0.72% (после Variant D)

**Variant D не должен уменьшать зону.** Значит либо:
- (a) `enforce_zone_uniqueness` срезал 1H после Variant D расширения
- (b) `_validate_min_span` удалил и FALLBACK дал другую зону
- (c) last 4 swings ETH 1H оказались у́же чем curr_struct

Проверю (c) — возможно last 4 swings ETH 1H = [1836.4 - 1849.7] (узкий кластер),
а curr_struct был шире. Тогда `max(curr, last4) = curr` должно дать 1.18%,
но лог показывает 0.72% — значит (a) или (b) перетёрли.

## 4. Предложения

### 4.1 Поднять _LAST_SWINGS_MIN для micro TF

```python
# core/structure.py — line 430
_LAST_SWINGS_MIN = {"1d": 3, "4h": 4, "1h": 6, "15m": 8, "5m": 10}
# 15M/5M нужно больше свингов (шумные) → зона шире
```

### 4.2 Отключить POST-LLM clamp для теста Variant D

```python
# scheduler.py — временно закомментировать:
# _validate_min_span() — удаляет узкие зоны
# _enforce_zone_uniqueness() — удаляет microchannels
```

### 4.3 Передать zone из structure.py напрямую в API (без LLM post-processing)

Сейчас `scheduler.py` получает zone из LLM ответа (не из structure.py напрямую).
Variant D меняет structure.py zone, но LLM может вернуть свою → перетёрло.

## 5. M15 = копия 1H (parent clamp перетёр Variant D)

User спросил «м 15 где?». 15M зоны **есть в API и MT5**, но значения проблемные:

| Сим | 15M zone | span | что не так |
|-----|----------|------|-----------|
| BTC | [63912 - 64237] | 0.51% | **= 1H (копия)** |
| ETH | [1836.4 - 1849.7] | 0.72% | **= 1H (копия)** |
| XAUT | [4010.0 - 4012.3] | **0.06%** | микро |

**Причина 1 (BTC/ETH):** в narrative structure.py пишет `(parent 1H задал рамки)`.
15M зона clamp'ится к 1H bounds → 1H узкий (0.51%) → 15M = копия 1H.
Variant D расширяет через last 4 swings, **но parent clamp обрезает обратно**.

**Причина 2 (XAUT):** суббота, золото не торгуется → last 4 swings в compression
[4010-4012] → max(curr, last4) = микро. Нужно больше свингов для 15M.

## 6. Вопросы

1. ETH 1H уменьшился после Variant D — это (a)/(b)/(c)? Проверь лог.
2. POST-LLM `validate_min_span` + `enforce_zone_uniqueness` — нужны ли они
   теперь когда Variant D даёт структурные зоны? Или они ломают твой фикс?
3. `FALLBACK: 1H zone from ZigZag structure` — это берёт зону ИЗ structure.py
   (с Variant D) или из отдельного ZigZag расчёта?
4. **M15 = копия 1H** — parent clamp обрезает Variant D расширение. Варианты:
   - (a) Применять Variant D **после** parent clamp, а не до
   - (b) Не clamp'ить 15M к 1H если 1H сам микро (span < 1%)
   - (c) Для 15M брать last 6-8 swings (не 4) — больше данных, шире зона
5. **XAUT 15M = 0.06%** — субботний compression. Можно ли для микро-TF
   увеличивать _LAST_SWINGS_MIN динамически?

## 6. Status

- HEAD `feb16da` (Variant D applied)
- Бот pid 22140, interval=15min, weekend-mode OFF (temp)
- Cron `62e3fd0798aa` (20:25 — уже ПРОШЁЛ? проверю) — нужен пересоздать
- Variant D работает в structure.py, но LLM post-processing частично срезает

Жду ответа.

— Hermes
