#!/usr/bin/env python3
# diag_simple.py - simplified AD divergence diagnostics for CSV pipelines

import sys
import json
from typing import Optional

import pandas as pd
import numpy as np


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, (int, float, np.integer, np.floating)):
            return float(v)
        s = str(v).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev).abs()
    tr3 = (low - prev).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=n, min_periods=1).mean()


def detect_pivots(highs: pd.Series, lows: pd.Series, left: int, right: int):
    n = len(highs)
    pl = [False] * n
    ph = [False] * n

    for i in range(left, n - right):
        seg_h = highs[(i - left):(i + right + 1)]
        seg_l = lows[(i - left):(i + right + 1)]

        high_center = _safe_float(highs.iat[i])
        low_center = _safe_float(lows.iat[i])

        if high_center is None or low_center is None:
            continue

        seg_h_max = _safe_float(seg_h.max())
        seg_l_min = _safe_float(seg_l.min())

        seg_h_wo = seg_h.drop(labels=[seg_h.index[left]])
        seg_l_wo = seg_l.drop(labels=[seg_l.index[left]])

        seg_h_wo_max = _safe_float(seg_h_wo.max()) if len(seg_h_wo) > 0 else None
        seg_l_wo_min = _safe_float(seg_l_wo.min()) if len(seg_l_wo) > 0 else None

        if seg_h_max is not None and high_center == seg_h_max and (seg_h_wo_max is None or high_center > seg_h_wo_max):
            ph[i] = True
        if seg_l_min is not None and low_center == seg_l_min and (seg_l_wo_min is None or low_center < seg_l_wo_min):
            pl[i] = True

    return pl, ph


def analyze_and_save(infile, profile, outpath, max_out=30):
    df = pd.read_csv(infile)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["high", "low", "close", "volume"]).reset_index(drop=True)

    n = len(df)
    small = 1e-12

    barRange = (df["high"] - df["low"]).replace(0, small)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / barRange
    mfv = mfm * df["volume"]

    rawFlow = mfv.cumsum()
    smoothedFlow = ema(rawFlow, 21)
    flowScale = ema(rawFlow.abs(), max(5, 8))
    flowNorm = (smoothedFlow / flowScale.replace(0, small)).fillna(0)

    pivotLeft = 10 if profile == "15m" else 12
    pivotRight = 10 if profile == "15m" else 12

    pl, ph = detect_pivots(df["high"], df["low"], pivotLeft, pivotRight)

    profPricePct = 0.015 if profile == "15m" else 0.012 if profile == "1h" else 0.02 if profile == "4h" else 0.03
    profAdPct = 0.03 if profile == "15m" else 0.035 if profile == "1h" else 0.045 if profile == "4h" else 0.06
    minFlowAbsFrac = 0.02 if profile == "15m" else 0.03 if profile == "1h" else 0.04 if profile == "4h" else 0.05

    candidates = []
    prevPriceLow = None
    prevFlowLow = None
    prevPriceHigh = None
    prevFlowHigh = None

    atr_series = atr(df, 14)

    for i in range(n):
        if pl[i]:
            currPrice = _safe_float(df["low"].iat[i])
            currFlow = _safe_float(flowNorm.iat[i])
            flowScaleVal = _safe_float(flowScale.iat[i]) or 0.0
            atrVal = _safe_float(atr_series.iat[i]) or 0.0

            if currPrice is None or currFlow is None:
                continue

            if prevPriceLow is not None and prevFlowLow is not None:
                raw = (currPrice < prevPriceLow) and (currFlow > prevFlowLow)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceLow) / max(abs(prevPriceLow), small)
                    flowPctChange = abs(currFlow - prevFlowLow) / max(abs(prevFlowLow), small)
                    flowAbsChange = abs(currFlow - prevFlowLow)
                    minFlowAbsChange = max(abs(flowScaleVal) * minFlowAbsFrac, small)

                    candidates.append({
                        "type": "bull",
                        "i": i,
                        "time": str(df["time"].iat[i]) if "time" in df.columns else i,
                        "prevPrice": float(prevPriceLow),
                        "currPrice": float(currPrice),
                        "priceMovePct": float(priceMovePct),
                        "prevFlow": float(prevFlowLow),
                        "currFlow": float(currFlow),
                        "flowPctChange": float(flowPctChange),
                        "flowAbsChange": float(flowAbsChange),
                        "flowScale": float(flowScaleVal),
                        "minFlowAbsThreshold": float(minFlowAbsChange),
                        "atr": float(atrVal),
                    })

            prevPriceLow = currPrice
            prevFlowLow = currFlow

        if ph[i]:
            currPrice = _safe_float(df["high"].iat[i])
            currFlow = _safe_float(flowNorm.iat[i])
            flowScaleVal = _safe_float(flowScale.iat[i]) or 0.0
            atrVal = _safe_float(atr_series.iat[i]) or 0.0

            if currPrice is None or currFlow is None:
                continue

            if prevPriceHigh is not None and prevFlowHigh is not None:
                raw = (currPrice > prevPriceHigh) and (currFlow < prevFlowHigh)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceHigh) / max(abs(prevPriceHigh), small)
                    flowPctChange = abs(currFlow - prevFlowHigh) / max(abs(prevFlowHigh), small)
                    flowAbsChange = abs(currFlow - prevFlowHigh)
                    minFlowAbsChange = max(abs(flowScaleVal) * minFlowAbsFrac, small)

                    candidates.append({
                        "type": "bear",
                        "i": i,
                        "time": str(df["time"].iat[i]) if "time" in df.columns else i,
                        "prevPrice": float(prevPriceHigh),
                        "currPrice": float(currPrice),
                        "priceMovePct": float(priceMovePct),
                        "prevFlow": float(prevFlowHigh),
                        "currFlow": float(currFlow),
                        "flowPctChange": float(flowPctChange),
                        "flowAbsChange": float(flowAbsChange),
                        "flowScale": float(flowScaleVal),
                        "minFlowAbsThreshold": float(minFlowAbsChange),
                        "atr": float(atrVal),
                    })

            prevPriceHigh = currPrice
            prevFlowHigh = currFlow

    out = {
        "file": infile,
        "profile": profile,
        "rows": n,
        "pivot_lows": int(sum(pl)),
        "pivot_highs": int(sum(ph)),
        "candidates_shown": min(len(candidates), max_out),
        "candidates": candidates[:max_out],
        "profilePricePct": profPricePct,
        "profileAdPct": profAdPct,
        "minFlowAbsFrac": minFlowAbsFrac,
    }

    with open(outpath, "w", encoding="utf8") as fh:
        json.dump(out, fh, indent=2)

    print("WROTE", outpath)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: diag_simple.py INPUT_CSV PROFILE OUT_JSON")
        sys.exit(1)

    infile = sys.argv[1]
    profile = sys.argv[2]
    out = sys.argv[3]
    analyze_and_save(infile, profile, out)