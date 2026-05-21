# diag_pivots.py
# Diagnostic: count pivots and candidate divergences per file / TF using same core logic as compute_full
import sys, os
import pandas as pd
import numpy as np

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def atr(df, n):
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=n, min_periods=1).mean()

def detect_pivots(highs, lows, left, right):
    n = len(highs)
    pl = [False] * n
    ph = [False] * n
    for i in range(left, n - right):
        seg_high = highs[(i - left):(i + right + 1)]
        seg_low = lows[(i - left):(i + right + 1)]
        if highs[i] == seg_high.max() and (len(seg_high.drop(labels=[i]))==0 or highs[i] > seg_high.drop(labels=[i]).max()):
            ph[i] = True
        if lows[i] == seg_low.min() and (len(seg_low.drop(labels=[i]))==0 or lows[i] < seg_low.drop(labels=[i]).min()):
            pl[i] = True
    return pl, ph

def analyze_file(infile, profile="1h", cmfLen=20, emaLen=21, atrLen=14, pivotLeftBase=12, pivotRightBase=12):
    df = pd.read_csv(infile, parse_dates=['time'], dayfirst=False, infer_datetime_format=True)
    df = df.reset_index(drop=True)
    n = len(df)
    small = 1e-9

    barRange = (df['high'] - df['low']).replace(0, small)
    mfm = ((df['close'] - df['low']) - (df['high'] - df['close'])) / barRange
    mfv = mfm * df['volume']

    rawFlow = mfv.cumsum()
    smoothedFlow = ema(rawFlow, emaLen)
    flowScale = ema(rawFlow.abs(), max(5, 8))
    flowNorm = smoothedFlow / flowScale.replace(0, small)

    # pivots
    pivotLeft = pivotLeftBase if profile!="15m" else max(pivotLeftBase, 10)
    pivotRight = pivotRightBase if profile!="15m" else max(pivotRightBase, 10)
    pl_bool, ph_bool = detect_pivots(df['high'], df['low'], pivotLeft, pivotRight)

    # counts
    n_pl = sum(pl_bool)
    n_ph = sum(ph_bool)

    # walk pivots and count raw candidates (price+flow direction only), then incremental filters
    prevPriceLow = None
    prevFlowLow = None
    prevPriceHigh = None
    prevFlowHigh = None

    raw_bull = 0
    raw_bear = 0
    price_ok_cnt = 0
    flow_pct_ok_cnt = 0
    flow_abs_ok_cnt = 0
    atr_ok_cnt = 0

    # thresholds used in compute_full (for diagnostics use profile defaults)
    profPricePct = 0.015 if profile=="15m" else 0.012 if profile=="1h" else 0.02 if profile=="4h" else 0.03
    profAdPct = 0.03 if profile=="15m" else 0.035 if profile=="1h" else 0.045 if profile=="4h" else 0.06
    minFlowAbsFrac = 0.03 if profile=="15m" else 0.04 if profile=="1h" else 0.05 if profile=="4h" else 0.06
    profileAtrMult = 1.1

    for i in range(len(df)):
        if pl_bool[i]:
            currPriceLow = df['low'].iloc[i]
            currFlowLow  = flowNorm.iloc[i]
            if prevPriceLow is not None:
                raw = (currPriceLow < prevPriceLow) and (currFlowLow > prevFlowLow)
                if raw: raw_bull += 1
                # price move pct
                priceMovePct = abs(currPriceLow - prevPriceLow) / max(prevPriceLow, small)
                if priceMovePct >= profPricePct: price_ok_cnt += 1
                # flow pct change
                flowPctChange = abs(currFlowLow - prevFlowLow) / max(abs(prevFlowLow), small)
                # absolute floor
                minFlowAbsChange = max(abs(flowScale.iloc[i]) * minFlowAbsFrac, 1e-9)
                flowAbsChange = abs(currFlowLow - prevFlowLow)
                if flowPctChange >= profAdPct and flowAbsChange >= minFlowAbsChange: flow_pct_ok_cnt += 1; flow_abs_ok_cnt += 1
                # atr
                atrVal = atr(df, atrLen).iloc[i]
                if abs(currPriceLow - prevPriceLow) >= (atrVal * profileAtrMult): atr_ok_cnt += 1
            prevPriceLow = currPriceLow
            prevFlowLow = currFlowLow

        if ph_bool[i]:
            currPriceHigh = df['high'].iloc[i]
            currFlowHigh  = flowNorm.iloc[i]
            if prevPriceHigh is not None:
                raw = (currPriceHigh > prevPriceHigh) and (currFlowHigh < prevFlowHigh)
                if raw: raw_bear += 1
                priceMovePct = abs(currPriceHigh - prevPriceHigh) / max(prevPriceHigh, small)
                if priceMovePct >= profPricePct: price_ok_cnt += 1
                flowPctChange = abs(currFlowHigh - prevFlowHigh) / max(abs(prevFlowHigh), small)
                minFlowAbsChange = max(abs(flowScale.iloc[i]) * minFlowAbsFrac, 1e-9)
                flowAbsChange = abs(currFlowHigh - prevFlowHigh)
                if flowPctChange >= profAdPct and flowAbsChange >= minFlowAbsChange: flow_pct_ok_cnt += 1; flow_abs_ok_cnt += 1
                atrVal = atr(df, atrLen).iloc[i]
                if abs(currPriceHigh - prevPriceHigh) >= (atrVal * profileAtrMult): atr_ok_cnt += 1
            prevPriceHigh = currPriceHigh
            prevFlowHigh = currFlowHigh

    out = {
        "file": os.path.basename(infile),
        "rows": n,
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
        "minFlowAbsFrac": minFlowAbsFrac
    }
    return out

if __name__ == "__main__":
    # usage: python diag_pivots.py ../data/BTCUSDT_1d.csv 1d
    if len(sys.argv) < 3:
        print("Usage: python diag_pivots.py INPUT_CSV PROFILE")
        sys.exit(1)
    infile = sys.argv[1]
    profile = sys.argv[2]
    r = analyze_file(infile, profile=profile)
    import json
    print(json.dumps(r, indent=2))