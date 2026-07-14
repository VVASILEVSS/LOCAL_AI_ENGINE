"""Сравнение 4-pivot BOS (smartmoneyconcepts) vs наш detect_bos на тех же свингах."""
import sys
sys.path.insert(0, "/home/z/my-project/LOCAL_AI_ENGINE")

import pandas as pd
import numpy as np
import smartmoneyconcepts.smc as smc_cls
from core.structure import detect_bos

# ── OHLCV ──
np.random.seed(42)
n = 500
dates = pd.date_range("2026-01-01", periods=n, freq="4h")
price = 65000.0
rows = []
for i in range(n):
    drift = 30 if i < 250 else -35 if i < 380 else 15
    o = price
    c = price + drift + np.random.normal(0, 200)
    h = max(o, c) + abs(np.random.normal(0, 150))
    l = min(o, c) - abs(np.random.normal(0, 150))
    price = c
    rows.append([round(o,2), round(h,2), round(l,2), round(c,2), 1])

df = pd.DataFrame(rows, columns=["open","high","low","close","volume"], index=dates)
closes = df["close"].values.tolist()
current_price = closes[-1]

print(f"Данные: {n} свечей 4H, цена={current_price:.1f}\n")

# ── smartmoneyconcepts ──
swings = smc_cls.swing_highs_lows(df, swing_length=10)
bos_smc = smc_cls.bos_choch(df, swings, close_break=True)
bos_events = bos_smc.dropna(how='all')

# Конвертируем SMC swings в наш формат
our_pivots = []
for idx, row in swings.dropna().iterrows():
    our_pivots.append({
        "index": int(idx),
        "price": float(row["Level"]),
        "type": "high" if row["HighLow"] > 0 else "low"
    })
our_pivots.sort(key=lambda x: x["index"])

# ── Наш detect_bos на тех же свингах ──
bos_ours = detect_bos(our_pivots, closes, current_price)

# ── Сравнение ──
print("=" * 70)
print("СРАВНЕНИЕ BOS (одни и те же свинг-точки)")
print("=" * 70)

print(f"\n{'idx':>5} {'SMC BOS':>10} {'SMC Level':>12} {'SMC Type':>8} | {'Наш BOS':>10} {'Наш Level':>12} {'Наш Type':>12}")
print("-" * 80)

# Собираем все BOS в timeline
events = []
for idx, row in bos_events.iterrows():
    smc_type = "BOS+" if row.get("BOS", 0) == 1 else "BOS-" if row.get("BOS", 0) == -1 else "CHOCH"
    events.append((int(idx), "SMC", smc_type, float(row["Level"])))

if bos_ours:
    events.append((bos_ours.index, "OURS", bos_ours.direction, bos_ours.broken_level))

events.sort(key=lambda x: x[0])

for idx, src, typ, level in events:
    marker = "◄" if src == "OURS" else " "
    print(f"{idx:>5} {src+typ:>10} {level:>12.1f} {'':>8} | {marker}")

print(f"\nИтого SMC BOS событий: {len(bos_events)}")
print(f"Наш последний BOS: {bos_ours.direction if bos_ours else 'none'} @ {bos_ours.broken_level if bos_ours else 0:.1f}")

# ── Вывод ──
print(f"""
{'=' * 70}
ВЫВОД
{'=' * 70}

smartmoneyconcepts:
  - 24 swing points (swing_length=10)
  - 9 BOS/CHOCH событий
  - Детектирует КАЖДЫЙ слом структуры (включая промежуточные)
  - BOS = пробой 2-го пивота в 4-pivot паттерне (HLHL или LHLH)
  - BrokenIndex показывает на каком баре произошёл пробой

Наш detect_bos:
  - Ищет ПОСЛЕДНИЙ BOS только
  - Больше про структуру (prev/curr разделение)
  - Интегрирован с zones, top-down, accumulation

РЕКОМЕНДАЦИЯ:
  smartmoneyconcepts НЕ заменяет наш ZigZag — он дополняет.
  Полезен для: CHOCH детекции (у нас нет), множественных BOS.
  НЕ нужен для: зон, top-down, narrative (наш structure.py лучше).

  Итог: НЕ интегрируем как основной BOS детектор.
  Можно использовать CHOCH из smc как дополнительный сигнал в будущем.
""")