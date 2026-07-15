#!/usr/bin/env python3
"""
extract_features.py v1
=======================
Extract 50+ ML features from unified_dataset CSV.

Sources:
  - context_json bars (offset -10..0, past only — NO forward data leakage)
  - Row metadata columns (strength, bias, regime, etc.)

Output: results/features.csv (rows with label_value + features)

Usage:
  python tools/extract_features.py
  python tools/extract_features.py --input results/unified_dataset_v11.csv --output results/features.csv
  python tools/extract_features.py --filter-forward   # skip rows without forward bars
"""

import argparse
import json
import csv
import math
import sys
import os
from collections import defaultdict
from typing import List, Dict, Optional, Tuple


# ============================================================================
# FEATURE EXTRACTORS
# ============================================================================

def _safe_float(val, default=0.0):
    try:
        if val is None or val == "" or val == "nan":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _pct_change(new, old):
    if abs(old) < 1e-12:
        return 0.0
    return (new - old) / abs(old) * 100


def _std(values):
    """Standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _ema(values, span):
    """Exponential moving average."""
    if not values or span < 1:
        return values[-1] if values else 0.0
    k = 2.0 / (span + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = k * v + (1 - k) * ema_val
    return ema_val


def _rsi(closes, period=14):
    """Relative Strength Index from close prices."""
    if len(closes) < period + 1:
        return 50.0  # neutral default
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas[-(period):]]
    losses = [abs(min(d, 0)) for d in deltas[-(period):]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss < 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr_from_bars(bars, period=14):
    """Average True Range from bar dicts."""
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h = bars[i].get("high", 0)
        l = bars[i].get("low", 0)
        c_prev = bars[i-1].get("close", 0)
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if not trs:
        return 0.0
    n = min(len(trs), period)
    return sum(trs[-n:]) / n


def _bb_width(bars, period=14, std_mult=2.0):
    """Bollinger Bands width (normalized)."""
    if len(bars) < period:
        return 0.0
    closes = [b.get("close", 0) for b in bars[-period:]]
    mean = sum(closes) / len(closes)
    std = _std(closes)
    if mean < 1e-12:
        return 0.0
    return (std_mult * 2 * std) / mean * 100


def parse_context_json(raw):
    if not raw or raw.strip() in ("", "[]"):
        return []
    try:
        bars = json.loads(raw)
        if isinstance(bars, list):
            return sorted(bars, key=lambda b: b.get("offset", 0))
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# ============================================================================
# MAIN FEATURE EXTRACTION
# ============================================================================

def extract_features_from_row(row: dict, filter_forward: bool = False) -> Optional[dict]:
    """
    Extract all features from one CSV row.

    Returns dict with feature_name -> float/int values, or None if row should be skipped.
    """
    cj_raw = row.get("context_json", "")
    bars = parse_context_json(cj_raw)

    if not bars:
        return None

    # Check forward bars
    has_fwd = any(b.get("offset", 0) > 0 for b in bars)
    if filter_forward and not has_fwd:
        return None

    # Split bars: past (offset <= 0) and future (offset > 0)
    # For ML: ONLY use past bars to avoid leakage
    past_bars = [b for b in bars if b.get("offset", 0) <= 0]
    entry_bars = [b for b in past_bars if b.get("offset", 0) == 0]

    if len(past_bars) < 3:
        return None

    # ── Raw arrays from past bars ──
    closes = [b.get("close", 0) for b in past_bars]
    highs = [b.get("high", 0) for b in past_bars]
    lows = [b.get("low", 0) for b in past_bars]
    volumes = [b.get("volume", 0) for b in past_bars]
    opens = [b.get("open", 0) for b in past_bars]

    entry_close = closes[-1] if closes else 0
    entry_high = highs[-1] if highs else 0
    entry_low = lows[-1] if lows else 0
    entry_vol = volumes[-1] if volumes else 0

    n_bars = len(past_bars)
    features = {}

    # ================================================================
    # GROUP 1: PRICE FEATURES (from past bars only)
    # ================================================================

    # 1-3: Basic price stats
    features["price_max"] = max(highs) if highs else 0
    features["price_min"] = min(lows) if lows else 0
    features["price_range"] = features["price_max"] - features["price_min"]

    # 4: Price range relative to entry
    features["price_range_pct"] = _pct_change(features["price_range"], entry_close) if entry_close > 0 else 0

    # 5: Close position in range (0=at low, 1=at high)
    if features["price_range"] > 1e-12:
        features["close_position"] = (entry_close - features["price_min"]) / features["price_range"]
    else:
        features["close_position"] = 0.5

    # 6-8: Returns (close-to-close)
    if len(closes) >= 2:
        returns = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        features["return_mean"] = sum(returns) / len(returns) if returns else 0
        features["return_std"] = _std(returns)
        features["return_last"] = returns[-1] if returns else 0
    else:
        features["return_mean"] = 0
        features["return_std"] = 0
        features["return_last"] = 0

    # 9: Multi-period returns
    if len(closes) >= 3:
        features["return_3bar"] = _pct_change(closes[-1], closes[-3])
    else:
        features["return_3bar"] = 0

    if len(closes) >= 6:
        features["return_6bar"] = _pct_change(closes[-1], closes[-6])
    else:
        features["return_6bar"] = 0

    if len(closes) >= 10:
        features["return_full"] = _pct_change(closes[-1], closes[0])
    else:
        features["return_full"] = features.get("return_3bar", 0)

    # 10: Wick analysis on entry bar
    if entry_high > entry_low and entry_close > 0:
        body = abs(entry_close - (opens[-1] if opens else entry_close))
        total_range = entry_high - entry_low
        features["wick_upper"] = (entry_high - max(entry_close, opens[-1] if opens else entry_close)) / total_range
        features["wick_lower"] = (min(entry_close, opens[-1] if opens else entry_close) - entry_low) / total_range
        features["body_ratio"] = body / total_range
    else:
        features["wick_upper"] = 0.5
        features["wick_lower"] = 0.5
        features["body_ratio"] = 0.5

    # 11: Price acceleration (2nd derivative of close)
    if len(closes) >= 3:
        d1 = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        d2 = [d1[i] - d1[i-1] for i in range(1, len(d1))] if len(d1) >= 2 else [0]
        features["price_accel"] = sum(d2[-3:]) / len(d2[-3:]) if d2 else 0
    else:
        features["price_accel"] = 0

    # ================================================================
    # GROUP 2: VOLATILITY FEATURES
    # ================================================================

    # 12: ATR from past bars
    features["atr"] = _atr_from_bars(past_bars, period=min(14, n_bars))
    features["atr_pct"] = _pct_change(features["atr"], entry_close) if entry_close > 0 else 0

    # 13: Close-to-close volatility (std of returns, normalized)
    if entry_close > 0 and len(closes) >= 2:
        returns_pct = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]
        features["volatility_pct"] = _std(returns_pct)
    else:
        features["volatility_pct"] = 0

    # 14: Bollinger Bands width
    features["bb_width"] = _bb_width(past_bars, period=min(14, n_bars - 1))

    # 15: Range contraction/expansion (recent range vs full range)
    if n_bars >= 5:
        recent_highs = highs[-5:]
        recent_lows = lows[-5:]
        recent_range = max(recent_highs) - min(recent_lows)
        full_range = features["price_range"]
        features["range_contraction"] = recent_range / full_range if full_range > 1e-12 else 1.0
    else:
        features["range_contraction"] = 1.0

    # ================================================================
    # GROUP 3: MOMENTUM FEATURES
    # ================================================================

    # 16: RSI
    features["rsi"] = _rsi(closes, period=min(14, n_bars - 1))

    # 17: RSI derivative (momentum of RSI)
    if n_bars >= 6:
        rsi_short = _rsi(closes[-6:], period=5)
        rsi_long = _rsi(closes, period=min(14, n_bars - 1))
        features["rsi_momentum"] = rsi_short - rsi_long
    else:
        features["rsi_momentum"] = 0

    # 18: EMA slope
    if n_bars >= 5 and entry_close > 0:
        ema_5 = _ema(closes, 5)
        ema_start = _ema(closes[:-5], 5) if len(closes) > 5 else closes[0]
        features["ema_slope_pct"] = _pct_change(ema_5, ema_start)
    else:
        features["ema_slope_pct"] = 0

    # 19: MACD (simple version: fast_ema - slow_ema)
    if n_bars >= 10:
        ema_fast = _ema(closes, 5)
        ema_slow = _ema(closes, min(10, n_bars))
        features["macd_pct"] = _pct_change(ema_fast - ema_slow, entry_close) if entry_close > 0 else 0
    else:
        features["macd_pct"] = 0

    # 20: Rate of Change (ROC)
    if n_bars >= 5:
        features["roc_5"] = _pct_change(closes[-1], closes[-5])
    else:
        features["roc_5"] = 0

    if n_bars >= 10:
        features["roc_10"] = _pct_change(closes[-1], closes[-10]) if n_bars >= 10 else 0
    else:
        features["roc_10"] = features.get("roc_5", 0)

    # ================================================================
    # GROUP 4: VOLUME FEATURES
    # ================================================================

    # 21: Volume stats
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    features["vol_avg"] = avg_vol
    features["vol_ratio_entry"] = entry_vol / avg_vol if avg_vol > 1e-12 else 1.0

    # 22: Volume trend (last 5 vs first 5)
    if len(volumes) >= 10:
        vol_recent = sum(volumes[-5:]) / 5
        vol_early = sum(volumes[:5]) / 5
        features["vol_trend"] = vol_recent / vol_early if vol_early > 1e-12 else 1.0
    elif len(volumes) >= 2:
        features["vol_trend"] = volumes[-1] / volumes[0] if volumes[0] > 1e-12 else 1.0
    else:
        features["vol_trend"] = 1.0

    # 23: Volume std (volatility of volume)
    features["vol_std"] = _std(volumes)
    features["vol_std_pct"] = features["vol_std"] / avg_vol if avg_vol > 1e-12 else 0

    # 24: Volume at entry vs median
    sorted_vols = sorted(volumes)
    median_vol = sorted_vols[len(sorted_vols)//2] if sorted_vols else 1
    features["vol_median_ratio"] = entry_vol / median_vol if median_vol > 1e-12 else 1.0

    # 25: Price-Volume correlation (simplified: are big moves on big volume?)
    if len(closes) >= 3 and len(volumes) >= 3 and features["vol_std"] > 1e-12 and features["return_std"] > 1e-12:
        returns_abs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        vol_std_norm = [(v - avg_vol) / features["vol_std"] for v in volumes[1:]]
        ret_std_norm = [(r - sum(returns_abs)/len(returns_abs)) / features["return_std"]
                        for r in returns_abs]
        n_corr = min(len(vol_std_norm), len(ret_std_norm))
        if n_corr > 1:
            cov = sum(vol_std_norm[i] * ret_std_norm[i] for i in range(n_corr)) / n_corr
            features["pv_correlation"] = max(-1, min(1, cov))
        else:
            features["pv_correlation"] = 0
    else:
        features["pv_correlation"] = 0

    # ================================================================
    # GROUP 5: ROW METADATA FEATURES (from CSV columns)
    # ================================================================

    # 26: Divergence type
    ctype = row.get("candidate_type", "").strip().lower()
    features["is_bull"] = 1.0 if ctype in ("bull", "bullish") else 0.0
    features["is_bear"] = 1.0 if ctype in ("bear", "bearish") else 0.0

    # 27: Strength
    features["strength"] = _safe_float(row.get("strength", 0))
    features["strength_sq"] = features["strength"] ** 2

    # 28: Price and flow metrics
    features["price_move_pct"] = _safe_float(row.get("price_move_pct", 0))
    features["flow_abs_change"] = _safe_float(row.get("flow_abs_change", 0))
    features["flow_pct_change"] = _safe_float(row.get("flow_pct_change", 0))
    features["flow_scale"] = _safe_float(row.get("flow_scale", 0))

    # 29: ATR from row
    row_atr = _safe_float(row.get("atr", 0))
    features["row_atr_pct"] = _pct_change(row_atr, entry_close) if entry_close > 0 else 0

    # 30: Volume ratio from row
    features["vol_ratio_row"] = _safe_float(row.get("vol_ratio", 1))

    # 31: Regime
    features["regime_score"] = _safe_float(row.get("regime_score", 0))
    features["regime_confirmed"] = 1.0 if str(row.get("regime_confirmed", "")).lower() in ("true", "1", "yes") else 0.0
    features["regime_trend"] = _safe_int(row.get("regime_trend", 0))
    features["regime_bullish"] = 1.0 if features["regime_trend"] == 1 else 0.0
    features["regime_bearish"] = 1.0 if features["regime_trend"] == -1 else 0.0

    # 32: Bias
    features["bias_score"] = _safe_float(row.get("bias_score", 0))
    features["bias_dir"] = _safe_int(row.get("bias_dir", 0))
    features["bias_above_threshold"] = 1.0 if str(row.get("bias_above", "")).lower() in ("true", "1", "yes") else 0.0
    features["bias_abs_score"] = abs(features["bias_score"])

    # 33: Divergence quality composites
    features["strength_atr_ratio"] = features["strength"] / 10.0  # normalize to 0-1
    features["price_flow_ratio"] = (
        features["price_move_pct"] / (abs(features["flow_pct_change"]) + 1e-12)
    )

    # 34: Hidden divergence
    features["is_hidden"] = 1.0 if str(row.get("hidden", "")).lower() in ("true", "1", "yes") else 0.0

    # 35: CMF and flow slope from row
    features["cmf_score"] = _safe_float(row.get("cmf_score", 0))
    features["flow_slope"] = _safe_float(row.get("flow_slope", 0))

    # ================================================================
    # GROUP 6: CONTEXT STRUCTURE FEATURES
    # ================================================================

    # 36: Number of bars available
    features["n_past_bars"] = n_bars

    # 37: Distance from entry to extremes in context
    if features["price_range"] > 1e-12:
        features["dist_to_high_pct"] = (features["price_max"] - entry_close) / entry_close * 100
        features["dist_to_low_pct"] = (entry_close - features["price_min"]) / entry_close * 100
    else:
        features["dist_to_high_pct"] = 0
        features["dist_to_low_pct"] = 0

    # 38: Higher highs / lower lows count
    hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
    ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i-1])
    features["higher_highs_ratio"] = hh / (n_bars - 1) if n_bars > 1 else 0
    features["lower_lows_ratio"] = ll / (n_bars - 1) if n_bars > 1 else 0

    # 39: Up bars vs down bars ratio
    up_bars = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
    down_bars = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
    total_bars = up_bars + down_bars
    features["up_bars_ratio"] = up_bars / total_bars if total_bars > 0 else 0.5

    # 40: Candle patterns in last 3 bars
    if len(closes) >= 3 and len(opens) >= 3:
        # Engulfing-like: last bar body > prev bar body * 2
        body_last = abs(closes[-1] - opens[-1])
        body_prev = abs(closes[-2] - opens[-2])
        features["engulfing_strength"] = body_last / body_prev if body_prev > 1e-12 else 1.0
    else:
        features["engulfing_strength"] = 1.0

    # ================================================================
    # GROUP 7: TIMEFRAME ENCODING
    # ================================================================

    tf = row.get("tf_profile", "").strip().lower()
    features["tf_15m"] = 1.0 if "15m" in tf else 0.0
    features["tf_1h"] = 1.0 if "1h" in tf else 0.0
    features["tf_4h"] = 1.0 if "4h" in tf else 0.0
    features["tf_1d"] = 1.0 if "1d" in tf else 0.0

    # TF ordinal (for ordinal encoding)
    tf_order = {"15m": 1, "1h": 2, "4h": 3, "1d": 4}
    features["tf_ordinal"] = tf_order.get(tf, 2)

    # ================================================================
    # LABEL (target variable)
    # ================================================================

    label_raw = row.get("label_value", "").strip()
    mfe_raw = row.get("mfe_pct", "").strip()
    mae_raw = row.get("mae_pct", "").strip()

    features["label_value"] = _safe_int(label_raw)
    features["mfe_pct"] = _safe_float(mfe_raw)
    features["mae_pct"] = _safe_float(mae_raw)

    return features


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Extract ML features from unified dataset")
    parser.add_argument("--input", default="results/unified_dataset_v11.csv", help="Input CSV")
    parser.add_argument("--output", default="results/features.csv", help="Output CSV")
    parser.add_argument("--filter-forward", action="store_true",
                        help="Skip rows without forward bars")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        alt_path = os.path.join(script_dir, "..", input_path)
        if os.path.exists(alt_path):
            input_path = alt_path
        else:
            print(f"ERROR: {input_path} not found")
            sys.exit(1)

    print(f"Reading: {input_path}")

    rows = []
    with open(input_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Total rows: {len(rows)}")

    # Extract features
    all_features = []
    skipped_no_fwd = 0
    skipped_no_data = 0
    skipped_no_label = 0

    for i, row in enumerate(rows):
        feat = extract_features_from_row(row, filter_forward=args.filter_forward)
        if feat is None:
            cj_raw = row.get("context_json", "")
            bars = parse_context_json(cj_raw)
            has_fwd = any(b.get("offset", 0) > 0 for b in bars) if bars else False
            if has_fwd:
                skipped_no_data += 1
            else:
                skipped_no_fwd += 1
            continue

        if feat["label_value"] == 0 and feat["mfe_pct"] == 0:
            # Has label but no MFE data — row was filtered by recalculate_labels
            skipped_no_label += 1
            continue

        feat["row_idx"] = i
        feat["symbol"] = row.get("symbol", "")
        feat["tf_profile"] = row.get("tf_profile", "")
        feat["candidate_type"] = row.get("candidate_type", "")
        all_features.append(feat)

    print(f"Skipped (no forward bars): {skipped_no_fwd}")
    print(f"Skipped (no context data): {skipped_no_data}")
    print(f"Skipped (no label/MFE): {skipped_no_label}")
    print(f"Features extracted: {len(all_features)}")

    if not all_features:
        print("ERROR: No features extracted. Run recalculate_labels.py first!")
        sys.exit(1)

    # Determine columns
    # Exclude non-feature columns from the header
    skip_cols = {"row_idx", "symbol", "tf_profile", "candidate_type", "mfe_pct", "mae_pct"}
    feat_cols = sorted([k for k in all_features[0].keys() if k not in skip_cols])
    meta_cols = ["row_idx", "symbol", "tf_profile", "candidate_type", "mfe_pct", "mae_pct"]
    all_cols = meta_cols + feat_cols

    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols)
        writer.writeheader()
        for feat in all_features:
            writer.writerow({k: feat.get(k, "") for k in all_cols})

    print(f"\nWritten: {args.output}")
    print(f"Columns: {len(feat_cols)} features + {len(meta_cols)} metadata")

    # Summary stats
    label_col = "label_value"
    wins = sum(1 for f in all_features if f[label_col] == 1)
    losses = sum(1 for f in all_features if f[label_col] == 0)
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    print(f"\nLabel distribution: {wins} wins, {losses} losses, WR={wr:.1f}%")

    # Feature list
    print(f"\nFeatures ({len(feat_cols)}):")
    for i, col in enumerate(feat_cols, 1):
        vals = [f[col] for f in all_features if col in f]
        non_zero = sum(1 for v in vals if v != 0 and v != 0.0)
        unique = len(set(str(v)[:6] for v in vals))
        print(f"  {i:2d}. {col:<30} non-zero={non_zero}/{len(vals)} unique={unique}")


if __name__ == "__main__":
    main()
