# Черновик письма Z: zone=curr_structure ONLY → узкие зоны не вкладываются

**Дата:** 2026-07-17
**От:** Hermes (Vasily)
**Кому:** Super Z
**Тема:** `110cd64` (Fix 1-6, zone=curr_structure ONLY) — узкие зоны, nesting ломается
**Статус:** ⚠️ ЧЕРНОВИК — не отправлять, разбираемся с багами

---

## Проблема

BTC зоны сейчас (лог 14:24):

| TF | Zone | BOS | Span |
|---|---|---|---|
| 1D | [57758-64691] | @62232 ↓ 24св | 12.0% |
| **4H** | **[64411-64691]** | @64411 ↑ 18св | **0.43%** |
| 1H | [62907-63833] | @63833 ↓ 11св | 1.55% |
| 15M | [62907-62947] | @63281 ↓ 17св | 0.06% |

4H [64411-64691] — узкая полоса на верхах (0.43%).
1H [62907-63833] — ниже 4H.
**Младшая зона провалилась под старшую** — нелогично.

## Корень: `110cd64` — zone = curr_structure ONLY

Коммит `110cd64` (Fix 1-6, "zone=curr_structure ONLY") изменил определение зоны.

### Backup `pre-zone-curr-structure-20260716-160000` (ДО `110cd64`) — нормальная логика:

```python
if curr_struct:
    zone_high = curr_struct.high
    zone_low = curr_struct.low
    if prev_struct:
        zone_high = max(zone_high, prev_struct.high)  # UNION с prev
        zone_low = min(zone_low, prev_struct.low)
```

Zone = **union(curr_struct, prev_struct)** — широкая, включает prev_structure.
prev_struct.high содержит значимый LH/HH (например D1 high=82850 из мая).

### После `110cd64` (текущий код):

```python
# ── ZONE = curr_structure ONLY (post-BOS range). ──
if curr_struct:
    zone_high = curr_struct.high
    zone_low = curr_struct.low
    # prev_struct НЕ объединяется
```

Zone = **curr_struct ONLY** — узкая полоса после последнего BOS.

### Что это делает с зонами:

- **4H BOS up @64411** → curr_struct = [64411, 64691] (узкая полоса на верхах)
- **1H BOS down @63833** → curr_struct = [62907, 63833] (узкая полоса ниже)
- Разные BOS в разных направлениях → зоны разбросаны, не вкладываются

Без union с prev_struct, zone теряет контекст предыдущей структуры.
4H prev_struct (до BOS up) содержал бы swing low ~57800 → расширил бы зону вниз.

## Концепция Vasily

Младшая зона ВСЕГДА внутри старшей (с небольшой разницей):
- D1 [57500-82500] ⊃ 4H [57800-65600] ⊃ 1H [61400-65600] ⊃ 15M [62600-65000]

Union(curr, prev) давал это — расширял зону до крайних свингов всей структуры.
curr_only сужает зону до полосы после BOS → nesting ломается.

## Вопросы

1. `110cd64` (Fix 1-6, zone=curr_structure ONLY) — это было сделано чтобы
   избежать подтягивания пробитого уровня (BUG 1). Но побочный эффект —
   узкие зоны. Можно ли вернуть union(curr, prev) с защитой от BUG 1?

2. Или: zone = curr_struct, но curr_struct должен включать swing low всей
   текущей структуры (не только post-BOS range)?

3. Какой backup ты считаешь "нормальным"?
   `backup/pre-zone-curr-structure-20260716-160000` (union логика) или
   другой?

## Backup tags

- `backup/pre-zone-curr-structure-20260716-160000` → `9f2b804` (union логика, ДО 110cd64)
- `backup/pre-zone-nesting-20260717-133000` → `c8a2f9b` (curr_only, после 110cd64)
- `backup/pre-nesting-revert-20260717-140000` → `07d34c6` (curr_only + clip/delete nesting)

---
*Hermes — черновик, не отправлять пока не разобрались*
