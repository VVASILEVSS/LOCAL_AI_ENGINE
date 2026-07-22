#!/usr/bin/env python
"""Тест analyze_topdown() на реальных BTC данных с Binance.
4 запроса OHLCV (D1, H4, H1, 15M), пауза 3 сек между.
"""
import sys, os, time, json

# Path к feature/top-down-structure (parent dir of scripts/)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# venv с ccxt/numpy
sys.path.insert(0, "D:/LOCAL_AI_ENGINE/.venv/Lib/site-packages")

import ccxt
import numpy as np
from core.auto_chart import _find_pivots
from core.structure import analyze_topdown

# ---- 1. Запрос OHLCV с паузами (НЕ СПАМИТЬ API) ----
ex = ccxt.binance({"enableRateLimit": True})
symbol = "BTC/USDT"
tfs = ["1d", "4h", "1h", "15m"]
tf_data = {}

print("=== Запрос OHLCV (пауза 3 сек между запросами) ===")
for tf in tfs:
    print(f"  {tf}...", end=" ", flush=True)
    try:
        ohlcv = ex.fetch_ohlcv(symbol, tf, limit=300)
        closes = [c[4] for c in ohlcv]
        highs = np.array([c[2] for c in ohlcv], dtype=float)
        lows = np.array([c[3] for c in ohlcv], dtype=float)
        current_price = closes[-1]

        # Пивоты depth=3 (TZ spec)
        ph, pl = _find_pivots(highs, lows, depth=3)
        swing_points = []
        for idx in sorted(ph + pl):
            if idx in ph:
                swing_points.append({"index": idx, "price": float(highs[idx]), "type": "high"})
            else:
                swing_points.append({"index": idx, "price": float(lows[idx]), "type": "low"})
        swing_points.sort(key=lambda p: p["index"])

        tf_data[tf.upper()] = {
            "swing_points": swing_points,
            "current_price": current_price,
            "closes": closes,
            "total_candles": len(ohlcv),
        }
        print(f"{len(ohlcv)} candles, {len(swing_points)} pivots, price={current_price:.1f}")
    except Exception as e:
        print(f"ERROR: {e}")
    if tf != tfs[-1]:
        time.sleep(3)  # ПРАВИЛО: пауза 3 сек

# ---- 2. analyze_topdown ----
print("\n=== analyze_topdown() ===")
results = analyze_topdown(tf_data)

# ---- 3. Вывод таблицы ----
print("\n| TF | prev.low | prev.high | zone.low | zone.high | span% | parent |")
print("|----|----------|------------|----------|-----------|-------|--------|")

for tf in ["1d", "4h", "1h", "15m"]:
    if tf not in results:
        print(f"| {tf.upper()} | — | — | — | — | — | — |")
        continue
    a = results[tf]
    p = a.prev_structure
    pl = f"{p.low:.1f}" if p else "—"
    ph = f"{p.high:.1f}" if p else "—"
    zl = f"{a.zone_low:.1f}" if a.zone_low is not None else "—"
    zh = f"{a.zone_high:.1f}" if a.zone_high is not None else "—"
    if a.zone_low is not None and a.zone_high is not None and a.zone_low > 0:
        span = (a.zone_high - a.zone_low) / a.zone_low * 100
        sp = f"{span:.1f}%"
    else:
        sp = "—"
    par = a.parent_tf or "—"
    print(f"| {tf.upper()} | {pl} | {ph} | {zl} | {zh} | {sp} | {par} |")

# ---- 4. Проверки ----
print("\n=== Проверки ===")
# 1. prev.high = абсолютный max всех high-пивотов до BOS
# 2. D1 зона самая широкая
# 3. Shared lows (младшие не ниже D1 low)

d1 = results.get("1d")
if d1 and d1.zone_low is not None:
    d1_low = d1.zone_low
    for tf in ["4h", "1h", "15m"]:
        a = results.get(tf)
        if a and a.zone_low is not None:
            if a.zone_low < d1_low * 0.99:
                print(f"  ⚠️ {tf.upper()} zone_low ({a.zone_low:.1f}) < D1 low ({d1_low:.1f}) — shared floor нарушен!")
            else:
                print(f"  ✅ {tf.upper()} zone_low ({a.zone_low:.1f}) ≥ D1 low ({d1_low:.1f}) — shared floor OK")

# Иерархия: D1 span > 4H span > 1H span > 15M span
spans = {}
for tf in ["1d", "4h", "1h", "15m"]:
    a = results.get(tf)
    if a and a.zone_low and a.zone_high:
        spans[tf.upper()] = a.zone_high - a.zone_low
if spans:
    print(f"  Spans: {spans}")
    if spans.get("1D", 0) >= spans.get("4H", 0) >= spans.get("1H", 0) >= spans.get("15M", 0):
        print("  ✅ Иерархия D1 > 4H > 1H > 15M — корректная")
    else:
        print("  ⚠️ Иерархия нарушена!")

# Narrative D1 — проверяем атрибуты
if d1:
    print(f"\n=== D1 Structure ===")
    print(f"  BOS: {d1.bos.direction} @ {d1.bos.price:.1f} (idx={d1.bos.index})")
    if d1.prev_structure:
        print(f"  prev_structure: {d1.prev_structure.direction} [{d1.prev_structure.low:.1f} - {d1.prev_structure.high:.1f}] ({d1.prev_structure.pivot_count} pivots)")
    print(f"  curr_structure: [{d1.curr_structure.low:.1f} - {d1.curr_structure.high:.1f}] ({d1.curr_structure.pivot_count} pivots)")
    print(f"  accumulation: {d1.is_accumulation} ({d1.accumulation_pivot_count} pivots)")
    print(f"  targets: {d1.targets}")
