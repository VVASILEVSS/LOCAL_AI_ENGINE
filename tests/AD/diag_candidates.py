#!/usr/bin/env python3
# diag_candidates.py
# Print detailed numeric info for first raw divergence candidates (bull/bear)
import sys, json, math
import pandas as pd

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
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
    n = len(df)
    small = 1e-12

    barRange = (df["high"] - df["low"]).replace(0, small)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / barRange
    mfv = mfm * df["volume"]

    rawFlow = mfv.cumsum()
    smoothedFlow = ema(rawFlow, 21)
    flowScale = ema(rawFlow.abs(), max(5,8))
    flowNorm = (smoothedFlow / flowScale.replace(0, small)).fillna(0)

    pivotLeft = 10 if profile=="15m" else 12
    pivotRight = 10 if profile=="15m" else 12
    pl, ph = detect_pivots(df["high"], df["low"], pivotLeft, pivotRight)

    profPricePct = 0.015 if profile=="15m" else 0.012 if profile=="1h" else 0.02 if profile=="4h" else 0.03
    profAdPct = 0.03 if profile=="15m" else 0.035 if profile=="1h" else 0.045 if profile=="4h" else 0.06
    minFlowAbsFrac = 0.03 if profile=="15m" else 0.04 if profile=="1h" else 0.05 if profile=="4h" else 0.06

    candidates = []
    prevPriceLow = prevFlowLow = None
    prevPriceHigh = prevFlowHigh = None

    for i in range(n):
        if pl[i]:
            currPrice = df["low"].iat[i]
            currFlow = flowNorm.iat[i]
            flowScaleVal = flowScale.iat[i] if i < len(flowScale) else 0.0
            if prevPriceLow is not None:
                raw = (currPrice < prevPriceLow) and (currFlow > prevFlowLow)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceLow) / max(prevPriceLow, small)
                    flowPctChange = abs(currFlow - prevFlowLow) / max(abs(prevFlowLow), small)
                    flowAbsChange = abs(currFlow - prevFlowLow)
                    minFlowAbsChange = max(abs(flowScaleVal) * minFlowAbsFrac, small)
                    atrVal = atr(df, 14).iat[i]
                    candidates.append({
                        "type":"bull",
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
                        "atr": float(atrVal)
                    })
            prevPriceLow = currPrice
            prevFlowLow = currFlow

        if ph[i]:
            currPrice = df["high"].iat[i]
            currFlow = flowNorm.iat[i]
            flowScaleVal = flowScale.iat[i] if i < len(flowScale) else 0.0
            if prevPriceHigh is not None:
                raw = (currPrice > prevPriceHigh) and (currFlow < prevFlowHigh)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceHigh) / max(prevPriceHigh, small)
                    flowPctChange = abs(currFlow - prevFlowHigh) / max(abs(prevFlowHigh), small)
                    flowAbsChange = abs(currFlow - prevFlowHigh)
                    minFlowAbsChange = max(abs(flowScaleVal) * minFlowAbsFrac, small)
                    atrVal = atr(df, 14).iat[i]
                    candidates.append({
                        "type":"bear",
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
                        "atr": float(atrVal)
                    })
            prevPriceHigh = currPrice
            prevFlowHigh = currFlow

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
