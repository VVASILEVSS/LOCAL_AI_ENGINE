#!/usr/bin/env python
"""Скачивает OHLCV с Binance и сохраняет в data/ohlcv_cache/<SYMBOL>_<TF>.csv
Паузы 3 сек между запросами. Без бана.
"""
import ccxt
import csv
import time
import os
import sys

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ohlcv_cache")
os.makedirs(OUT_DIR, exist_ok=True)

ex = ccxt.binance({"enableRateLimit": True})

SYMBOLS = ["BTC/USDT", "ETH/USDT", "XAUT/USDT"]
TIMEFRAMES = ["1d", "4h", "1h", "15m"]
LIMIT = 500  # Binance max per request

for sym in SYMBOLS:
    sym_id = sym.replace("/", "")
    for tf in TIMEFRAMES:
        print(f"Fetching {sym} {tf}...", end=" ", flush=True)
        try:
            ohlcv = ex.fetch_ohlcv(sym, tf, limit=LIMIT)
            fname = os.path.join(OUT_DIR, f"{sym_id}_{tf}.csv")
            with open(fname, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                w.writerows(ohlcv)
            print(f"{len(ohlcv)} candles saved → {fname}")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(3)  # anti-ban

print("\nDone.")
