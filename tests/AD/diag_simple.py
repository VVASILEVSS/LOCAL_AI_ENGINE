#!/usr/bin/env python3
import sys, json
import pandas as pd
def ema(s, span): return s.ewm(span=span, adjust=False).mean()
def atr(df, n):
    high=df["high"]; low=df["low"]; close=df["close"]; prev=close.shift(1)
    tr1=high-low; tr2=(high-prev).abs(); tr3=(low-prev).abs()
    tr=pd.concat([tr1,tr2,tr3], axis=1).max(axis=1)
    return tr.rolling(window=n, min_periods=1).mean()
def detect_pivots(highs, lows, left, right):
    n=len(highs); pl=[False]*n; ph=[False]*n
    for i in range(left, n-right):
        seg_h=highs[(i-left):(i+right+1)]; seg_l=lows[(i-left):(i+right+1)]
        if highs.iat[i]==seg_h.max() and (len(seg_h.drop(labels=[i]))==0 or highs.iat[i] > seg_h.drop(labels=[i]).max()): ph[i]=True
        if lows.iat[i]==seg_l.min() and (len(seg_l.drop(labels=[i]))==0 or lows.iat[i] < seg_l.drop(labels=[i]).min()): pl[i]=True
    return pl, ph

def analyze_and_save(infile, profile, outpath, max_out=30):
    df = pd.read_csv(infile)
    if "time" in df.columns: df["time"]=pd.to_datetime(df["time"], errors="coerce")
    n=len(df); small=1e-12
    barRange=(df["high"]-df["low"]).replace(0, small)
    mfm = ((df["close"]-df["low"]) - (df["high"]-df["close"])) / barRange
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

    candidates=[]
    prevPriceLow=prevFlowLow=None
    prevPriceHigh=prevFlowHigh=None

    atr_series = atr(df, 14)
    for i in range(n):
        if pl[i]:
            currPrice = float(df["low"].iat[i]); currFlow = float(flowNorm.iat[i])
            flowScaleVal = float(flowScale.iat[i]) if i < len(flowScale) else 0.0
            if prevPriceLow is not None:
                raw = (currPrice < prevPriceLow) and (currFlow > prevFlowLow)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceLow)/max(prevPriceLow, small)
                    flowPctChange = abs(currFlow - prevFlowLow)/max(abs(prevFlowLow), small)
                    flowAbsChange = abs(currFlow - prevFlowLow)
                    minFlowAbsChange = max(abs(flowScaleVal)*minFlowAbsFrac, small)
                    atrVal = float(atr_series.iat[i])
                    candidates.append({"type":"bull","i":i,"time":str(df["time"].iat[i]) if "time" in df.columns else i,
                        "prevPrice":prevPriceLow,"currPrice":currPrice,"priceMovePct":priceMovePct,
                        "prevFlow":prevFlowLow,"currFlow":currFlow,"flowPctChange":flowPctChange,
                        "flowAbsChange":flowAbsChange,"flowScale":flowScaleVal,"minFlowAbsThreshold":minFlowAbsChange,"atr":atrVal})
            prevPriceLow = currPrice; prevFlowLow = currFlow
        if ph[i]:
            currPrice = float(df["high"].iat[i]); currFlow = float(flowNorm.iat[i])
            flowScaleVal = float(flowScale.iat[i]) if i < len(flowScale) else 0.0
            if prevPriceHigh is not None:
                raw = (currPrice > prevPriceHigh) and (currFlow < prevFlowHigh)
                if raw:
                    priceMovePct = abs(currPrice - prevPriceHigh)/max(prevPriceHigh, small)
                    flowPctChange = abs(currFlow - prevFlowHigh)/max(abs(prevFlowHigh), small)
                    flowAbsChange = abs(currFlow - prevFlowHigh)
                    minFlowAbsChange = max(abs(flowScaleVal)*minFlowAbsFrac, small)
                    atrVal = float(atr_series.iat[i])
                    candidates.append({"type":"bear","i":i,"time":str(df["time"].iat[i]) if "time" in df.columns else i,
                        "prevPrice":prevPriceHigh,"currPrice":currPrice,"priceMovePct":priceMovePct,
                        "prevFlow":prevFlowHigh,"currFlow":currFlow,"flowPctChange":flowPctChange,
                        "flowAbsChange":flowAbsChange,"flowScale":flowScaleVal,"minFlowAbsThreshold":minFlowAbsChange,"atr":atrVal})
            prevPriceHigh = currPrice; prevFlowHigh = currFlow

    out = {"file":infile,"profile":profile,"rows":n,"pivot_lows":int(sum(pl)),"pivot_highs":int(sum(ph)),
           "candidates_shown":min(len(candidates), max_out),"candidates":candidates[:max_out],
           "profilePricePct":profPricePct,"profileAdPct":profAdPct,"minFlowAbsFrac":minFlowAbsFrac}
    with open(outpath, "w", encoding="utf8") as fh:
        json.dump(out, fh, indent=2)
    print("WROTE", outpath)

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: diag_simple.py INPUT_CSV PROFILE OUT_JSON")
        sys.exit(1)
    infile=sys.argv[1]; profile=sys.argv[2]; out=sys.argv[3]
    analyze_and_save(infile, profile, out)
