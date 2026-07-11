#!/usr/bin/env python3
# diag_pivots.py - diagnostics for pivot/divergence pipeline (compatible with older pandas)

import sys
import os
import json
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=n, min_periods=1).mean()


def _safe_float(v: object) -> Optional[float]:
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


def detect_pivots(highs: pd.Series, lows: pd.Series, left: int, right: int):
    n = len(highs)
    pl = [False] * n
    ph = [False] * n

    for i in range(left, n - right):
        seg_high = highs[(i - left):(i + right + 1)]
        seg_low = lows[(i - left):(i + right + 1)]

        high_center = _safe_float(highs.iat[i])
        low_center = _safe_float(lows.iat[i])

        if high_center is not None and low_center is not None:
            high_others = seg_high.drop(seg_high.index[seg_high.index == seg_high.index[left]])
            low_others = seg_low.drop(seg_low.index[seg_low.index == seg_low.index[left]])

            if high_center == seg_high.max() and (len(high_others) == 0 or high_center > float(high_others.max())):
                ph[i] = True
            if low_center == seg_low.min() and (len(low_others) == 0 or low_center < float(low_others.min())):
                pl[i] = True

    return pl, ph


def analyze_file(
    infile: str,
    profile: str = "1h",
    cmfLen: int = 20,
    emaLen: int = 21,
    atrLen: int = 14,
    pivotLeftBase: int = 12,
    pivotRightBase: int = 12,
):
    df = pd.read_csv(Path(infile))

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")

    df = df.reset_index(drop=True)
    n = len(df)
    small = 1e-9

    barRange = (df["high"] - df["low"]).replace(0, small)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / barRange
    mfv = mfm * df["volume"]

    rawFlow = mfv.cumsum()
    smoothedFlow = ema(rawFlow, emaLen)
    flowScale = ema(rawFlow.abs(), max(5, 8))
    flowNorm = (smoothedFlow / flowScale.replace(0, small)).fillna(0)

    pivotLeft = pivotLeftBase if profile != "15m" else max(pivotLeftBase, 10)
    pivotRight = pivotRightBase if profile != "15m" else max(pivotRightBase, 10)
    pl_bool, ph_bool = detect_pivots(df["high"], df["low"], pivotLeft, pivotRight)

    n_pl = sum(pl_bool)
    n_ph = sum(ph_bool)

    prevPriceLow: Optional[float] = None
    prevFlowLow: Optional[float] = None
    prevPriceHigh: Optional[float] = None
    prevFlowHigh: Optional[float] = None

    raw_bull = 0
    raw_bear = 0
    price_ok_cnt = 0
    flow_pct_ok_cnt = 0
    flow_abs_ok_cnt = 0
    atr_ok_cnt = 0

    profPricePct = 0.015 if profile == "15m" else 0.012 if profile == "1h" else 0.02 if profile == "4h" else 0.03
    profAdPct = 0.03 if profile == "15m" else 0.035 if profile == "1h" else 0.045 if profile == "4h" else 0.06
    minFlowAbsFrac = 0.03 if profile == "15m" else 0.04 if profile == "1h" else 0.05 if profile == "4h" else 0.06
    profileAtrMult = 1.1

    atr_series = atr(df, atrLen)

    for i in range(n):
        if pl_bool[i]:
            currPriceLow = _safe_float(df["low"].iat[i]) or 0.0
            currFlowLow = _safe_float(flowNorm.iat[i]) or 0.0

            if prevPriceLow is not None and prevFlowLow is not None:
                raw = (currPriceLow < prevPriceLow) and (currFlowLow > prevFlowLow)
                if raw:
                    raw_bull += 1

                priceMovePct = abs(currPriceLow - prevPriceLow) / max(prevPriceLow, small)
                if priceMovePct >= profPricePct:
                    price_ok_cnt += 1

                flowPctChange = abs(currFlowLow - prevFlowLow) / max(abs(prevFlowLow), small)
                minFlowAbsChange = max(abs(_safe_float(flowScale.iat[i]) or 0.0) * minFlowAbsFrac, 1e-9)
                flowAbsChange = abs(currFlowLow - prevFlowLow)

                if flowPctChange >= profAdPct and flowAbsChange >= minFlowAbsChange:
                    flow_pct_ok_cnt += 1
                    flow_abs_ok_cnt += 1

                atr_val = _safe_float(atr_series.iat[i]) or 0.0
                if abs(currPriceLow - prevPriceLow) >= (atr_val * profileAtrMult):
                    atr_ok_cnt += 1

            prevPriceLow = currPriceLow
            prevFlowLow = currFlowLow

        if ph_bool[i]:
            currPriceHigh = _safe_float(df["high"].iat[i]) or 0.0
            currFlowHigh = _safe_float(flowNorm.iat[i]) or 0.0

            if prevPriceHigh is not None and prevFlowHigh is not None:
                raw = (currPriceHigh > prevPriceHigh) and (currFlowHigh < prevFlowHigh)
                if raw:
                    raw_bear += 1

                priceMovePct = abs(currPriceHigh - prevPriceHigh) / max(prevPriceHigh, small)
                if priceMovePct >= profPricePct:
                    price_ok_cnt += 1

                flowPctChange = abs(currFlowHigh - prevFlowHigh) / max(abs(prevFlowHigh), small)
                minFlowAbsChange = max(abs(_safe_float(flowScale.iat[i]) or 0.0) * minFlowAbsFrac, 1e-9)
                flowAbsChange = abs(currFlowHigh - prevFlowHigh)

                if flowPctChange >= profAdPct and flowAbsChange >= minFlowAbsChange:
                    flow_pct_ok_cnt += 1
                    flow_abs_ok_cnt += 1

                atr_val = _safe_float(atr_series.iat[i]) or 0.0
                if abs(currPriceHigh - prevPriceHigh) >= (atr_val * profileAtrMult):
                    atr_ok_cnt += 1

            prevPriceHigh = currPriceHigh
            prevFlowHigh = currFlowHigh

    out = {
        "file": os.path.basename(infile),
        "rows": int(n),
        "pivot_lows": int(n_pl),
        "pivot_highs": int(n_ph),
        "raw_bull_candidates": int(raw_bull),
        "raw_bear_candidates": int(raw_bear),
        "price_move_ok_count": int(price_ok_cnt),
        "flow_pct_ok_count": int(flow_pct_ok_cnt),
        "flow_abs_ok_count": int(flow_abs_ok_cnt),
        "atr_ok_count": int(atr_ok_cnt),
        "profilePricePct": profPricePct,
        "profileAdPct": profAdPct,
        "minFlowAbsFrac": minFlowAbsFrac,
    }
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python diag_pivots.py INPUT_CSV PROFILE")
        sys.exit(1)

    infile = sys.argv[1]
    profile = sys.argv[2]
    res = analyze_file(infile, profile=profile)
    print(json.dumps(res, indent=2))