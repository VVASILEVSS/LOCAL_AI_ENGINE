#!/usr/bin/env python3
# diag_candidates.py
# Print detailed numeric info for first raw divergence candidates (bull/bear)
import sys
import json
import math
from typing import Optional
import os
from pathlib import Path
import pandas as pd
import numpy as np

def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()

def atr(df, n):
    high = df["high"]; low = df["low"]; close = df["close"]
    prev = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev).abs()
    tr3 = (low - prev).abs()
    tr = pd.concat([tr1,tr2,tr3], axis=1).max(axis=1)
    return tr.rolling(window=n, min_periods=1).mean()

def detect_pivots(highs, lows, left, right):
    n = len(highs)
    pl=[False]*n; ph=[False]*n
    for i in range(left, n-right):
        seg_h = highs[(i-left):(i+right+1)]
        seg_l = lows[(i-left):(i+right+1)]
        if highs.iat[i] == seg_h.max() and (len(seg_h.drop(labels=[i]))==0 or highs.iat[i] > seg_h.drop(labels=[i]).max()):
            ph[i] = True
        if lows.iat[i] == seg_l.min() and (len(seg_l.drop(labels=[i]))==0 or lows.iat[i] < seg_l.drop(labels=[i]).min()):
            pl[i] = True
    return pl, ph

def analyze(infile, profile="1d", max_out=20):
    df = pd.read_csv(infile)

    # Coerce key numeric columns to floats to avoid mixed dtypes and satisfy static analysis
    num_cols = [c for c in ["open","high","low","close","volume"] if c in df.columns]
    if num_cols:
        df[num_cols] = df[num_cols].apply(pd.to_numeric, errors="coerce")

    # Parse time with explicit format (US style with AM/PM) to avoid pandas warning
    if "time" in df.columns:
        try:
            df["time"] = pd.to_datetime(df["time"], format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
        except Exception:
            # fallback to generic parsing if format differs
            df["time"] = pd.to_datetime(df["time"], errors="coerce")

    n = len(df)
    small = 1e-12

    # compute A/D raw and normalized series
    barRange = (df["high"] - df["low"]).replace(0, small)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / barRange
    mfv = mfm * df["volume"]

    rawFlow = mfv.cumsum()
    smoothedFlow = ema(rawFlow, 21)
    flowScale = ema(rawFlow.abs(), max(5,8))
    flowNorm = (smoothedFlow / flowScale.replace(0, small)).fillna(0)

    # window for flowNorm std (used as absolute floor in flowNorm units)
    cmfLen = 20
    flowNormStd = flowNorm.rolling(window=cmfLen, min_periods=1).std().fillna(0.0)

    # convert to numpy arrays of concrete types to remove pandas-Scalar/NaT issues
    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    flowNorm_arr = flowNorm.to_numpy(dtype=float)
    flowNormStd_arr = flowNormStd.to_numpy(dtype=float)
    flowScale_arr = flowScale.to_numpy(dtype=float)
    atr_series = atr(df, 14).to_numpy(dtype=float)
    times = df["time"].astype(str).to_numpy() if "time" in df.columns else None

    # stronger pivot settings per TF
    pivotLeft = 12 if profile=="15m" else 16 if profile=="1h" else 18 if profile=="4h" else 20
    pivotRight = pivotLeft

    # --- Set defaults BEFORE possible AUTOTUNE override to avoid possibly-unbound warnings ---
    minFlowAbsFrac = 0.03 if profile=="15m" else 0.04 if profile=="1h" else 0.05 if profile=="4h" else 0.06
    minPriceMovePct = 0.008 if profile in ("15m", "1h") else 0.012 if profile=="4h" else 0.015
    minFlowPct = 0.12 if profile=="15m" else 0.15 if profile=="1h" else 0.18 if profile=="4h" else 0.20
    # --- End defaults ---

    # --- AUTOTUNE OVERRIDE: apply per-symbol/profile mapping if available ---
    try:
        mapping_path = os.path.join("results", "autotune_best_params.json")
        if os.path.exists(mapping_path):
            _mapping = json.load(open(mapping_path, "r", encoding="utf-8"))
            # derive symbol from infile path (expects tests/AD/data/SYMBOL_profile.csv)
            sym = None
            try:
                sym = os.path.basename(infile).split("_")[0].upper()
            except Exception:
                sym = None
            if sym and sym in _mapping and profile in _mapping[sym]:
                _p = _mapping[sym][profile]
                # apply overrides if provided
                if "pivotLeft" in _p:
                    pivotLeft = max(8, int(_p["pivotLeft"]))
                if "pivotRight" in _p:
                    pivotRight = max(8, int(_p["pivotRight"]))
                if "minFlowAbsFrac" in _p:
                    minFlowAbsFrac = max(0.02, float(_p["minFlowAbsFrac"]))
                if "minFlowPct" in _p:
                    minFlowPct = max(0.10, float(_p["minFlowPct"]))
                if "minPriceMovePct" in _p:
                    minPriceMovePct = max(0.006, float(_p["minPriceMovePct"]))
                # write override info to results/diag_log.txt (do not use stderr)
                try:
                    os.makedirs("results", exist_ok=True)
                    with open("results/diag_log.txt","a",encoding="utf-8") as _lf:
                        _lf.write(f"AUTOTUNE OVERRIDE applied for {sym} {profile}: {_p}\n")
                except Exception:
                    # ignore logging errors
                    pass
    except Exception:
        # if mapping file is malformed or other I/O error, continue with defaults
        pass
    # --- end AUTOTUNE OVERRIDE ---

    pl, ph = detect_pivots(df["high"], df["low"], pivotLeft, pivotRight)

    # sensitivity profiles (adjusted)
    profPricePct = 0.015 if profile=="15m" else 0.015 if profile=="1h" else 0.025 if profile=="4h" else 0.035
    profAdPct    = 0.03  if profile=="15m" else 0.05  if profile=="1h" else 0.06  if profile=="4h" else 0.06

    candidates = []
    prevPriceLow: Optional[float] = None
    prevFlowLow: Optional[float] = None
    prevPriceHigh: Optional[float] = None
    prevFlowHigh: Optional[float] = None

    for i in range(n):
        if pl[i]:
            currPrice = float(lows[i])
            currFlow = float(flowNorm_arr[i])
            flowScaleVal = float(flowScale_arr[i]) if i < len(flowScale_arr) else 0.0

            # only compare if we have previous numeric pivots
            if prevPriceLow is not None and prevFlowLow is not None:
                raw = (currPrice < prevPriceLow) and (currFlow > prevFlowLow)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceLow) / max(prevPriceLow, small)
                    flowPctChange = abs(currFlow - prevFlowLow) / max(abs(prevFlowLow), small)
                    flowAbsChange = abs(currFlow - prevFlowLow)
                    # absolute floor expressed in same units as flowAbsChange
                    localFlowStd = float(flowNormStd_arr[i]) if i < len(flowNormStd_arr) else small
                    minFlowAbsChange = max(localFlowStd * minFlowAbsFrac, small)
                    atrVal = float(atr_series[i]) if i < len(atr_series) else float(small)

                    # additional checks to filter noise
                    if priceMovePct < minPriceMovePct:
                        # too small price move
                        pass
                    elif flowPctChange < minFlowPct:
                        # too small relative flow change
                        pass
                    elif flowAbsChange < minFlowAbsChange:
                        # below absolute flow threshold
                        pass
                    else:
                        candidates.append({
                            "type":"bull",
                            "i": i,
                            "time": str(times[i]) if times is not None else i,
                            "prevPrice": float(prevPriceLow),
                            "currPrice": float(currPrice),
                            "priceMovePct": float(priceMovePct),
                            "prevFlow": float(prevFlowLow),
                            "currFlow": float(currFlow),
                            "flowPctChange": float(flowPctChange),
                            "flowAbsChange": float(flowAbsChange),
                            "flowScale": float(flowScaleVal),
                            "minFlowAbsThreshold": float(minFlowAbsChange),
                            "atr": float(atrVal)
                        })
            prevPriceLow = float(currPrice)
            prevFlowLow = float(currFlow)

        if ph[i]:
            currPrice = float(highs[i])
            currFlow = float(flowNorm_arr[i])
            flowScaleVal = float(flowScale_arr[i]) if i < len(flowScale_arr) else 0.0

            if prevPriceHigh is not None and prevFlowHigh is not None:
                raw = (currPrice > prevPriceHigh) and (currFlow < prevFlowHigh)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceHigh) / max(prevPriceHigh, small)
                    flowPctChange = abs(currFlow - prevFlowHigh) / max(abs(prevFlowHigh), small)
                    flowAbsChange = abs(currFlow - prevFlowHigh)
                    localFlowStd = float(flowNormStd_arr[i]) if i < len(flowNormStd_arr) else small
                    minFlowAbsChange = max(localFlowStd * minFlowAbsFrac, small)
                    atrVal = float(atr_series[i]) if i < len(atr_series) else float(small)

                    # additional checks to filter noise
                    if priceMovePct < minPriceMovePct:
                        pass
                    elif flowPctChange < minFlowPct:
                        pass
                    elif flowAbsChange < minFlowAbsChange:
                        pass
                    else:
                        candidates.append({
                            "type":"bear",
                            "i": i,
                            "time": str(times[i]) if times is not None else i,
                            "prevPrice": float(prevPriceHigh),
                            "currPrice": float(currPrice),
                            "priceMovePct": float(priceMovePct),
                            "prevFlow": float(prevFlowHigh),
                            "currFlow": float(currFlow),
                            "flowPctChange": float(flowPctChange),
                            "flowAbsChange": float(flowAbsChange),
                            "flowScale": float(flowScaleVal),
                            "minFlowAbsThreshold": float(minFlowAbsChange),
                            "atr": float(atrVal)
                        })
            prevPriceHigh = float(currPrice)
            prevFlowHigh = float(currFlow)

    # sort by index and limit
    candidates = sorted(candidates, key=lambda x: x["i"])[:max_out]
    print(json.dumps({
        "file": infile,
        "profile": profile,
        "rows": n,
        "pivot_lows": sum(pl),
        "pivot_highs": sum(ph),
        "candidates_shown": len(candidates),
        "candidates": candidates
    }, indent=2))

if __name__ == "__main__":
    infile = sys.argv[1] if len(sys.argv) > 1 else "data/ohlcv/current/BTCUSDT_1h.csv"
    profile = sys.argv[2] if len(sys.argv) > 2 else "1h"
    max_out = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    analyze(infile, profile, max_out)