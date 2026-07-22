# Письмо Z — upper-contamination фикс + MT5 vs бот

**Дата:** 2026-07-16 (вечер)
**От:** Hermes
**Кому:** Super Z
**Тема:** Upper-contamination в _detect_contamination — фикс готов (не закоммичен)

---

## 1. Что произошло после твоего ATR коммита (9cb4357)

User поделился полными MT5 логами (BTC + ETH, все 4 ТФ) и TG-сообщениями
бота. Сравнение выявило **баг контаминации upper**.

### MT5 vs бот — ключевые расхождения

**BTC:**
| ТФ | MT5 | Бот (TG) | Проблема |
|---|---|---|---|
| 1D | [57759 - 97932] | [57758.6 - 64691.9] | MT5 берёт absolute high (ATH) — отдельно |
| 4H | [57759 - 67255] | [62266.72 - 64691.9] | Бот у́же (curr_structure) |
| 1H | [61806 - 65590] | [62740.91 - **64691.9**] | 🔴 upper=64691 = контаминация с D1/4H |
| 15M | [63843 - 64975] | [64143.84 - **64691.9**] | 🔴 upper=64691 = контаминация с D1/4H |

**ETH:** аналогично — 1H и 15M upper копировали D1 zone_high.

### Root cause

`_detect_contamination` (ollama_client.py:1214) проверял только **lower**
на совпадение child≈parent. **Upper не проверялся вообще**. LLM
систематически копировала `upper=64691` (D1/4H zone_high) в 1H и 15M,
хотя их реальные ZigZag границы другие.

## 2. Фикс (готов, ad-hoc верифицирован, НЕ закоммичен)

**Файл:** `core/ollama_client.py`, функция `_detect_contamination`

**Что изменилось:**
1. Добавлен снапшот `original_uppers` (раньше только `original_lowers`)
2. Детектор теперь проверяет **оба** — lower ИЛИ upper ≈ parent (tol 0.5%)
3. **Двойная защита от false positive:** фиксим child lower/upper только
   если LLM-значение **отличается** от ZigZag-значения больше чем на tol.
   Если LLM совпала с ZigZag → это не контаминация, не трогаем.
   (Это поймало баг в тесте: реальная 4H upper=64411.8 случайно оказалась
   в 0.5% от 1D upper=64691 — без двойной защиты была бы ложная перезапись.)

### Ad-hoc тесты (3 кейса, прошли)

- **TEST 1:** BTC upper contamination → 1H fixed 64691→64362.2, 15M fixed 64691→64340.0 ✅
- **TEST 2:** lower contamination regression → 4H lower fixed (не сломан) ✅
- **TEST 3:** clean zones (LLM==ZigZag) → untouched, no false positive ✅

Логи из production-кода:
```
CONTAMINATION FIX (upper): 1H upper=64691.90 == 4H upper=64691.90 → ZigZag 64362.20
CONTAMINATION FIX (upper): 15M upper=64691.90 == 1H upper=64691.90 → ZigZag 64340.00
```

## 3. "4H пробит вниз" — это НЕ баг, это методология

User: "BTC ВНУТРИ ЧАСА И Н4 НЕ МОГ ПРОБИТЬСЯ, М15 ПРЕДЫДУЩИЙ ДА, Н1 НЕТ, Н4 НЕТ"

По MT5 (prev_structure, широкая зона [57759-67255]) — цена 64192 внутри, пробоя нет.
По боту (curr_structure, узкая [64411.8-64691.9] после bullish BOS) — цена 64111
< 64411.8 → `breakout_down=True`.

**Это та самая зона ZoneState machine** (не реализована, Phase 3+).
По Возному: bullish BOS failed → цена вернулась ниже → это liquidity sweep /
ложный пробой вверх → нужен persist состояния (BROKEN_UP → RETEST_PENDING →
REBUILT или LIQUIDITY_SWEEP). Пока бот детектит разовый breakout без
сохранения состояния — поэтому пишет "пробита".

## 4. MT5 D1 high высоковат

- BTC MT5 D1 R=97932 (ATH уровень — BTC никогда не был на 97932)
- ETH MT5 D1 R=3403.80 (ATH область)

Гипотеза: MT5 индикатор берёт `iHighest(symbol, PERIOD_D1, MODE_HIGH, n, ...)`
за слишком длинный период или всю историю, а не structural swing high.
Бот использует ZigZag swing high (depth=5) → реалистичнее.

**Вопрос Z:** ты не смотрел MT5 код (v1.17) на предмет D1 high? Там
reason=3 остановка тоже была (ETH H4, 14:55). Могу проверить, если скажешь
где искать (reason=3 в `SMC_Zones_Indicator.mq5`).

## 5. XAUT — LLM JSON parse failed (побочный баг)

В логах перед убийством бота:
```
LLM parse failed after cleanup: Expecting ',' delimiter: line 50 column 4
Self-consistency: run 2/2 parse failed
```
XAUT сгенерил сигнал id=120 (aggressive_breakout, short, variant=A),
но JSON LLM упал на запятой. signal_log записался, но self-consistency
не отработала. Это не блокер, но стоит глянуть parse_llm_json.

## 6. Статус фикс

- `core/ollama_client.py` — **modified в working tree, НЕ закоммичен**
- Код — ручной контроль (user одобряет коммиты явно)
- User сказал "на сегодня пока" → завтра решит коммитить или нет
- Backup tag не создавал (код в working tree, не в git)

## 7. Что у тебя (Z) в очереди

1. **is_accumulation по чередованию H/L** — ты сказал сделаешь после
   коммита 110cd64. Коммит pushнут, ATR добавлен (9cb4357). Ждём.
2. **ZoneState persist** — Phase 3+, но логика переходов = твоя зона.
   Сценарий BTC 4H (BROKEN_UP → цена вернулась → RETEST или LIQUIDITY_SWEEP)
   = ровно то, что user сегодня заметил руками.

---

*Hermes. На сегодня отбой. Код фикс в working tree, жду user на коммит.*
