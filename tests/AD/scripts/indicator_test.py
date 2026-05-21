# indicator_test.py
# Минимальная реализация ключевых метрик индикатора для тестирования (Python, pandas)
# Установите: pip install pandas numpy
import sys
import json
import pandas as pd
import numpy as np

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def compute(df, cmf_len=20, ema_len=21, atr_len=14, regime_lb=8):
    # Ensure numeric types
    df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)

    # mfv (Chaikin)
    bar_range = (df['high'] - df['low']).replace(0, 1e-9)
    mfm = ((df['close'] - df['low']) - (df['high'] - df['close'])) / bar_range
    mfv = mfm * df['volume']

    # raw_flow (Chaikin AD)
    raw_flow = mfv.cumsum()

    # smoothed flow
    smoothed_flow = ema(raw_flow, ema_len)

    # flow_slope_pct (relative to regimeLookback)
    prev_val = smoothed_flow.shift(regime_lb)
    flow_slope_pct = ((smoothed_flow - prev_val) / prev_val.abs().replace(0,1e-9) * 100).fillna(0)

    # CMF rolling
    sum_mfv = mfv.rolling(window=cmf_len, min_periods=1).sum()
    sum_vol = df['volume'].rolling(window=cmf_len, min_periods=1).sum()
    cmf = (sum_mfv / sum_vol).fillna(0)

    # simple bias score (demo) as pandas Series
    flow_slope = smoothed_flow - smoothed_flow.shift(1)
    flow_score = pd.Series(np.where(flow_slope > 0, 1, np.where(flow_slope < 0, -1, 0)), index=df.index)
    cmf_score = pd.Series(np.where(cmf > 0.03, 1, np.where(cmf < -0.03, -1, 0)), index=df.index)

    bias_score = flow_score + cmf_score
    ad_bias = pd.Series(np.where(bias_score >= 2, 'bullish', np.where(bias_score <= -2, 'bearish', 'neutral')), index=df.index)

    # Confirmation (very simplified)
    bull_confirm = (bias_score >= 2) & (cmf > 0.05)
    bear_confirm = (bias_score <= -2) & (cmf < -0.05)
    ad_confirmation = pd.Series('none', index=df.index)
    ad_confirmation[bull_confirm] = 'strong_bullish'
    ad_confirmation[bear_confirm] = 'strong_bearish'
    # weak
    weak_mask = (bias_score != 0) & (~bull_confirm) & (~bear_confirm)
    ad_confirmation[weak_mask] = 'weak'

    # Divergence / regime / quality — placeholders (for full parity implement full logic)
    ad_divergence = pd.Series('none', index=df.index)
    ad_regime = pd.Series('flat', index=df.index)
    ad_quality = pd.Series('low', index=df.index)

    # Build last-row output
    last = {
        "profile": "test",
        "flow_mode": "Chaikin",
        "raw_flow": float(raw_flow.iloc[-1]),
        "smoothed_flow": float(smoothed_flow.iloc[-1]),
        "flow_slope_pct": float(flow_slope_pct.iloc[-1]),
        "cmf": float(cmf.iloc[-1]),
        "ad_bias": str(ad_bias.iloc[-1]),
        "ad_confirmation": str(ad_confirmation.iloc[-1]),
        "ad_divergence": str(ad_divergence.iloc[-1]),
        "ad_regime": str(ad_regime.iloc[-1]),
        "ad_quality": str(ad_quality.iloc[-1])
    }
    return last

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python indicator_test.py data.csv")
        sys.exit(1)
    # Read CSV (expect column 'time','open','high','low','close','volume')
    df = pd.read_csv(sys.argv[1], parse_dates=['time'])
    out = compute(df)
    print(json.dumps(out))