# Письмо Super Z — zone-nesting правка + backup tag

**Дата:** 2026-07-17
**От:** Hermes (Vasily)
**Тема:** Backup перед zone-nesting + что сломалось — посмотри

---

## Backup tag

```
backup/pre-zone-nesting-20260717-133000 → c8a2f9b
```

Это последний коммит **ПЕРЕД** zone-nesting правкой (HEAD сейчас `07d34c6`).
Запушен в origin. Можно откатиться: `git reset --hard backup/pre-zone-nesting-20260717-133000`.

---

## Причина правки

User (13:10) сообщил проблему вложенности зон ETH:

> "ЭФИР ЗОНА Н4 ЯВЛЯЕТСЯ ВЛОЖЕНОЙ В Д1 НО НЕ ЯВЛЯЕТЯ РОДИТЕЛЕМ ДЛЯ МЛАДШИХ ЗАУЖЕНА СТАРШЕЙ"

MT5 лог ETHUSD (13:10:08) — **ДО правки** (на backup tag `c8a2f9b`):

```
1D:  R=1833.00  S=1712.50  (span 6.59%)
4H:  R=1946.80  S=1848.00  (span 5.41%)  ← ВЫШЕ 1D upper!
1H:  R=1866.50  S=1829.70  (span 2.01%)  ← частично ниже 4H lower
15M: R=1835.80  S=1829.70  (span 0.33%)  ← ниже 4H lower
price=1826.65
```

**Нарушения top-down nesting (1D ⊃ 4H ⊃ 1H ⊃ 15M):**

| Пара | Проблема |
|---|---|
| 4H vs 1D | 4H lower (1848) > 1D upper (1833) → **disjoint**, 4H не вложена в 1D |
| 1H vs 4H | 1H lower (1829.7) < 4H lower (1848) → 1H не вложена в 4H |
| 15M vs 1H | 15M lower (1829.7) == 1H lower (1829.7) → дублирование, но внутри 1H ✅ |

**Почему так получилось:** ZigZag считает зоны per-TF независимо от своих swing-ов.
D1 swing = [1712-1833] (даун-тренд), 4H swing = [1848-1946] (рейндж выше).
Цена пробила 4H support (1848) и упала в D1 зону — это **правильная структура**,
но зоны не вкладываются, потому что они описывают **разные** структурные диапазоны.

Бот при этом отработал верно: "4H слом down → цель 1D @1712.5" + "⚠️ ложный пробой D1 resistance".
Т.е. логика breakout/target работала, но **визуально на MT5 зоны не вкладываются** —
это сбивает с толку.

---

## Что сделано (`07d34c6`)

### 1. `_enforce_zone_nesting()` в `ollama_client.py`

Добавлена функция, запускается **ПОСЛЕ** FALLBACK (чтобы fallback не восстановил
зоны которые нарушают nesting):

```python
def _enforce_zone_nesting(tf_zones: dict) -> dict:
    tf_order = ["1D", "4H", "1H", "15M", "5M"]
    for i, child_tf in enumerate(tf_order[1:], start=1):
        # ... найти parent (первый старший TF в tf_zones)
        # new_upper = min(child_upper, parent_upper)
        # new_lower = max(child_lower, parent_lower)
        # if new_lower >= new_upper → del (zone broken)
        # elif changed → clip + source tag
```

Логика:
- D1 → 4H → 1H → 15M
- Младшая зона **должна быть внутри** старшей (lower ≥ parent.lower, upper ≤ parent.upper)
- Если выходит за parent → **clip** к границам parent
- Если clip невалид (lower ≥ upper после clip) → зона пробита → **удалить**

### 2. `scheduler.py` — кэш после enforce_risk_rules

`_last_analysis_cache` обновлялся **до** `enforce_risk_rules` (с `tf_zones_clean`),
т.е. кэшировались зоны до nesting/drift/fallback. API отдавал pre-nesting мусор.

Теперь кэш обновляется **после** `enforce_risk_rules` с `parsed["tf_zones"]`:

```python
_last_analysis_cache[symbol_id].update({
    "risk_management": parsed.get("risk_management", {}),
    "entry_price": parsed.get("price"),
    "tf_zones": parsed.get("tf_zones", tf_zones_clean),  # <-- NEW
})
```

---

## Результат в API (живой бот, 13:37)

| TF | BTC | ETH | XAUT |
|---|---|---|---|
| 1D | [57758-64691] ✅ | [1712-1833] ✅ | [3942-4189] ✅ |
| 4H | [64411-64691] ✅ inside 1D | **REMOVED** ❌ broken by 1D | [3971-4015] ✅ inside 1D |
| 1H | [64411-64691] ✅ inside 4H | [1821-1833] ✅ clipped to 1D | [3971-4008] ✅ inside 4H |
| 15M | **REMOVED** ❌ broken by 1H | [1821-1833] ✅ clipped to 1H | [3978-4008] ✅ inside 1H |

XAUT — идеальный nesting (все 4 уровня вложены, ничего не сломано).
BTC — 15M удалена (не внутри 1H после clip), 1H=4H clipped.
ETH — 4H удалена (не внутри D1), 1H/15M clipped к D1 upper.

---

## Что прошу посмотреть

1. **Backup tag `backup/pre-zone-nesting-20260717-133000`** — логика ДО правки.
   ZigZag зоны per-TF независимы, nesting не enforced.

2. **HEAD `07d34c6`** — логика ПОСЛЕ. Nesting enforced после fallback.

3. **Вопрос к тебе:** правильно ли удалять сломанную младшую зону (clip invalid →
   del), или лучше fallback к parent зоне (создать дубликат parent под именем
   child)? Я выбрал **удалить** — зона пробита, её больше нет. Но на MT5 это
   значит что для этого TF не будет отрисован прямоугольник. Альтернатива —
   рисовать parent зону под именем child, но это дубликат.

4. **Концептуальный вопрос:** ZigZag зоны per-TF описывают разные структуры.
   D1 swing и 4H swing могут быть из разных рыночных фаз. Требовать вложенности
   — значит принудительно clip/удалять младшие зоны которые описывают реальную
   структуру своего TF. Это правильно? Или nesting должен быть soft
   (предупреждение, не удаление)?

User хочет top-down: D1 ⊃ 4H ⊃ 1H ⊃ 15M. Но рынок не всегда укладывается.
Жду твоего мнения.

---

## Chain

```
07d34c6 (zone-nesting, HEAD) ← эта правка
c8a2f9b (false-breakout)     ← backup tag указывает сюда
c7c4ed0 (zone-drift)
9b64b13 (TF-ladder)
737c65d (dynamic header)
...
```

Бот работает: PID 10560, Flask 200, autoscan active (interval=15min, BTC→ETH→XAUT).

---
*Hermes*
