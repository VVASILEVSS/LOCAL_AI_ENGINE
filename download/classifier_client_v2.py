#!/usr/bin/env python3
"""
classifier_client.py v1
=========================
ML classifier integration layer for signal quality prediction.

Loads trained model from results/model.pkl and provides:
  - predict_signal_quality(features) -> (confidence, is_strong, verdict)
  - extract_features_from_context(context_json, row_meta) -> dict
  - get_model_stats() -> dict with model metadata

Supports both sklearn Pipeline and PureRandomForest models.

Usage:
  from core.classifier_client import predict_signal_quality, extract_features_from_context

  # From context_json (live signal):
  features = extract_features_from_context(context_json_str, row_meta={"tf_profile": "4h", "candidate_type": "bull"})
  confidence, is_strong, verdict = predict_signal_quality(features, divergence_type="bull")

  # From features dict directly:
  confidence, is_strong, verdict = predict_signal_quality(features)
"""

import json
import math
import os
import sys
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ============================================================================
# GLOBALS
# ============================================================================

_model_cache = None       # cached model data dict
_feature_cols_cache = None  # cached feature column list
_model_loaded = False
_metadata = {}
_pure_python = False


# ============================================================================
# FEATURE EXTRACTION (self-contained — no dependency on extract_features.py)
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
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _ema(values, span):
    if not values or span < 1:
        return values[-1] if values else 0.0
    k = 2.0 / (span + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = k * v + (1 - k) * ema_val
    return ema_val


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
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
    if len(bars) < period:
        return 0.0
    closes = [b.get("close", 0) for b in bars[-period:]]
    mean = sum(closes) / len(closes)
    std = _std(closes)
    if mean < 1e-12:
        return 0.0
    return (std_mult * 2 * std) / mean * 100


def _parse_bars(raw):
    """Parse context_json string to sorted list of bar dicts."""
    if not raw or raw.strip() in ("", "[]"):
        return []
    try:
        bars = json.loads(raw)
        if isinstance(bars, list):
            return sorted(bars, key=lambda b: b.get("offset", 0))
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def extract_features_from_context(
    context_json_str: str,
    row_meta: Optional[dict] = None,
) -> Optional[dict]:
    """
    Extract all ML features from a context_json string + optional row metadata.

    This is the LIVE prediction version — uses ONLY past bars (offset <= 0),
    no forward data. Compatible with features used during training.

    Args:
        context_json_str: JSON string with bar data, e.g. from unified_dataset context_json column
        row_meta: dict with additional row fields like:
            - tf_profile (e.g. "15m", "1h", "4h", "1d")
            - candidate_type (e.g. "bull", "bear")
            - strength, bias_score, regime_score, vol_ratio, etc.

    Returns:
        dict of feature_name -> float, or None if extraction failed
    """
    if row_meta is None:
        row_meta = {}

    bars = _parse_bars(context_json_str)
    if not bars:
        return None

    # ONLY past bars (offset <= 0) — no forward data leakage
    past_bars = [b for b in bars if b.get("offset", 0) <= 0]

    if len(past_bars) < 3:
        return None

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

    # ── GROUP 1: PRICE ──
    features["price_max"] = max(highs) if highs else 0
    features["price_min"] = min(lows) if lows else 0
    features["price_range"] = features["price_max"] - features["price_min"]
    features["price_range_pct"] = _pct_change(features["price_range"], entry_close) if entry_close > 0 else 0

    if features["price_range"] > 1e-12:
        features["close_position"] = (entry_close - features["price_min"]) / features["price_range"]
    else:
        features["close_position"] = 0.5

    if len(closes) >= 2:
        returns = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        features["return_mean"] = sum(returns) / len(returns) if returns else 0
        features["return_std"] = _std(returns)
        features["return_last"] = returns[-1] if returns else 0
    else:
        features["return_mean"] = 0
        features["return_std"] = 0
        features["return_last"] = 0

    features["return_3bar"] = _pct_change(closes[-1], closes[-3]) if len(closes) >= 3 else 0
    features["return_6bar"] = _pct_change(closes[-1], closes[-6]) if len(closes) >= 6 else 0
    features["return_full"] = _pct_change(closes[-1], closes[0]) if len(closes) >= 10 else features.get("return_3bar", 0)

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

    if len(closes) >= 3:
        d1 = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        d2 = [d1[i] - d1[i-1] for i in range(1, len(d1))] if len(d1) >= 2 else [0]
        features["price_accel"] = sum(d2[-3:]) / len(d2[-3:]) if d2 else 0
    else:
        features["price_accel"] = 0

    # ── GROUP 2: VOLATILITY ──
    features["atr"] = _atr_from_bars(past_bars, period=min(14, n_bars))
    features["atr_pct"] = _pct_change(features["atr"], entry_close) if entry_close > 0 else 0

    if entry_close > 0 and len(closes) >= 2:
        returns_pct = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]
        features["volatility_pct"] = _std(returns_pct)
    else:
        features["volatility_pct"] = 0

    features["bb_width"] = _bb_width(past_bars, period=min(14, n_bars - 1))

    if n_bars >= 5:
        recent_range = max(highs[-5:]) - min(lows[-5:])
        full_range = features["price_range"]
        features["range_contraction"] = recent_range / full_range if full_range > 1e-12 else 1.0
    else:
        features["range_contraction"] = 1.0

    # ── GROUP 3: MOMENTUM ──
    features["rsi"] = _rsi(closes, period=min(14, n_bars - 1))

    if n_bars >= 6:
        rsi_short = _rsi(closes[-6:], period=5)
        rsi_long = _rsi(closes, period=min(14, n_bars - 1))
        features["rsi_momentum"] = rsi_short - rsi_long
    else:
        features["rsi_momentum"] = 0

    if n_bars >= 5 and entry_close > 0:
        ema_5 = _ema(closes, 5)
        ema_start = _ema(closes[:-5], 5) if len(closes) > 5 else closes[0]
        features["ema_slope_pct"] = _pct_change(ema_5, ema_start)
    else:
        features["ema_slope_pct"] = 0

    if n_bars >= 10:
        ema_fast = _ema(closes, 5)
        ema_slow = _ema(closes, min(10, n_bars))
        features["macd_pct"] = _pct_change(ema_fast - ema_slow, entry_close) if entry_close > 0 else 0
    else:
        features["macd_pct"] = 0

    features["roc_5"] = _pct_change(closes[-1], closes[-5]) if n_bars >= 5 else 0
    features["roc_10"] = _pct_change(closes[-1], closes[-10]) if n_bars >= 10 else features.get("roc_5", 0)

    # ── GROUP 4: VOLUME ──
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    features["vol_avg"] = avg_vol
    features["vol_ratio_entry"] = entry_vol / avg_vol if avg_vol > 1e-12 else 1.0

    if len(volumes) >= 10:
        vol_recent = sum(volumes[-5:]) / 5
        vol_early = sum(volumes[:5]) / 5
        features["vol_trend"] = vol_recent / vol_early if vol_early > 1e-12 else 1.0
    elif len(volumes) >= 2:
        features["vol_trend"] = volumes[-1] / volumes[0] if volumes[0] > 1e-12 else 1.0
    else:
        features["vol_trend"] = 1.0

    features["vol_std"] = _std(volumes)
    features["vol_std_pct"] = features["vol_std"] / avg_vol if avg_vol > 1e-12 else 0

    sorted_vols = sorted(volumes)
    median_vol = sorted_vols[len(sorted_vols)//2] if sorted_vols else 1
    features["vol_median_ratio"] = entry_vol / median_vol if median_vol > 1e-12 else 1.0

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

    # ── GROUP 5: ROW METADATA ──
    ctype = str(row_meta.get("candidate_type", "")).strip().lower()
    features["is_bull"] = 1.0 if ctype in ("bull", "bullish") else 0.0
    features["is_bear"] = 1.0 if ctype in ("bear", "bearish") else 0.0

    features["strength"] = _safe_float(row_meta.get("strength", 0))
    features["strength_sq"] = features["strength"] ** 2

    features["price_move_pct"] = _safe_float(row_meta.get("price_move_pct", 0))
    features["flow_abs_change"] = _safe_float(row_meta.get("flow_abs_change", 0))
    features["flow_pct_change"] = _safe_float(row_meta.get("flow_pct_change", 0))
    features["flow_scale"] = _safe_float(row_meta.get("flow_scale", 0))

    row_atr = _safe_float(row_meta.get("atr", 0))
    features["row_atr_pct"] = _pct_change(row_atr, entry_close) if entry_close > 0 else 0
    features["vol_ratio_row"] = _safe_float(row_meta.get("vol_ratio", 1))

    features["regime_score"] = _safe_float(row_meta.get("regime_score", 0))
    features["regime_confirmed"] = 1.0 if str(row_meta.get("regime_confirmed", "")).lower() in ("true", "1", "yes") else 0.0
    features["regime_trend"] = _safe_int(row_meta.get("regime_trend", 0))
    features["regime_bullish"] = 1.0 if features["regime_trend"] == 1 else 0.0
    features["regime_bearish"] = 1.0 if features["regime_trend"] == -1 else 0.0

    features["bias_score"] = _safe_float(row_meta.get("bias_score", 0))
    features["bias_dir"] = _safe_int(row_meta.get("bias_dir", 0))
    features["bias_above_threshold"] = 1.0 if str(row_meta.get("bias_above", "")).lower() in ("true", "1", "yes") else 0.0
    features["bias_abs_score"] = abs(features["bias_score"])

    features["strength_atr_ratio"] = features["strength"] / 10.0
    features["price_flow_ratio"] = (
        features["price_move_pct"] / (abs(features["flow_pct_change"]) + 1e-12)
    )

    features["is_hidden"] = 1.0 if str(row_meta.get("hidden", "")).lower() in ("true", "1", "yes") else 0.0
    features["cmf_score"] = _safe_float(row_meta.get("cmf_score", 0))
    features["flow_slope"] = _safe_float(row_meta.get("flow_slope", 0))

    # ── GROUP 6: CONTEXT STRUCTURE ──
    features["n_past_bars"] = n_bars

    if entry_close > 1e-12:
        features["dist_to_high_pct"] = (features["price_max"] - entry_close) / entry_close * 100
        features["dist_to_low_pct"] = (entry_close - features["price_min"]) / entry_close * 100
    else:
        features["dist_to_high_pct"] = 0
        features["dist_to_low_pct"] = 0

    hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
    ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i-1])
    features["higher_highs_ratio"] = hh / (n_bars - 1) if n_bars > 1 else 0
    features["lower_lows_ratio"] = ll / (n_bars - 1) if n_bars > 1 else 0

    up_bars = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
    down_bars = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
    total_bars = up_bars + down_bars
    features["up_bars_ratio"] = up_bars / total_bars if total_bars > 0 else 0.5

    if len(closes) >= 3 and len(opens) >= 3:
        body_last = abs(closes[-1] - opens[-1])
        body_prev = abs(closes[-2] - opens[-2])
        features["engulfing_strength"] = body_last / body_prev if body_prev > 1e-12 else 1.0
    else:
        features["engulfing_strength"] = 1.0

    # ── GROUP 7: TIMEFRAME ──
    tf = str(row_meta.get("tf_profile", "")).strip().lower()
    features["tf_15m"] = 1.0 if "15m" in tf else 0.0
    features["tf_1h"] = 1.0 if "1h" in tf else 0.0
    features["tf_4h"] = 1.0 if "4h" in tf else 0.0
    features["tf_1d"] = 1.0 if "1d" in tf else 0.0

    tf_order = {"15m": 1, "1h": 2, "4h": 3, "1d": 4}
    features["tf_ordinal"] = tf_order.get(tf, 2)

    return features


# ============================================================================
# MODEL LOADING
# ============================================================================

def _find_model_path() -> Optional[str]:
    """Find model.pkl in standard locations."""
    candidates = [
        os.path.join("results", "model.pkl"),
        os.path.join("..", "results", "model.pkl"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "model.pkl"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "model.pkl"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return os.path.abspath(path)
    return None


def load_model(model_path: Optional[str] = None) -> bool:
    """
    Load trained model from pickle file.

    Args:
        model_path: explicit path to model.pkl, or auto-detect

    Returns:
        True if loaded successfully, False otherwise
    """
    global _model_cache, _feature_cols_cache, _model_loaded, _metadata, _pure_python

    try:
        import pickle
    except ImportError:
        logger.error("pickle module not available — cannot load model")
        return False

    if model_path is None:
        model_path = _find_model_path()
    if model_path is None:
        logger.warning("model.pkl not found — ML predictions disabled (using dummy 50%)")
        return False

    if not os.path.exists(model_path):
        logger.warning(f"Model file not found: {model_path}")
        return False

    try:
        with open(model_path, "rb") as f:
            data = pickle.load(f)

        _model_cache = data.get("model")
        _feature_cols_cache = data.get("feature_cols", [])
        _metadata = data.get("metadata", {})
        _pure_python = data.get("pure_python", False)

        _model_loaded = True
        logger.info(
            f"Model loaded: {model_path} "
            f"(samples={_metadata.get('n_samples', '?')}, "
            f"features={len(_feature_cols_cache)}, "
            f"type={'pure_python' if _pure_python else 'sklearn'})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        return False


def ensure_model_loaded() -> bool:
    """Ensure model is loaded, loading if necessary."""
    if not _model_loaded:
        return load_model()
    return True


# ============================================================================
# PREDICTION
# ============================================================================

def _features_to_vector(features: dict, feature_cols: list) -> list:
    """Convert feature dict to ordered vector matching model's expected input."""
    vector = []
    for col in feature_cols:
        val = features.get(col, 0.0)
        try:
            vector.append(float(val) if val and str(val) != "nan" else 0.0)
        except (ValueError, TypeError):
            vector.append(0.0)
    return vector


def predict_proba_single(model, feature_vector: list) -> float:
    """
    Get probability of positive class (win) for a single sample.

    Returns probability in [0, 1].
    """
    try:
        # sklearn Pipeline
        if hasattr(model, "predict_proba"):
            import numpy as np
            X = np.array([feature_vector], dtype=float)
            proba = model.predict_proba(X)
            # proba shape: [1, 2] -> [prob_class_0, prob_class_1]
            if hasattr(proba, "shape") and len(proba.shape) == 2 and proba.shape[1] >= 2:
                return float(proba[0][1])  # probability of class 1 (win)
            return float(proba[0])

        # PureRandomForest
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba([feature_vector])
            return float(probs[0])

        # Fallback: use predict() and return 1.0 for class 1, 0.0 for class 0
        pred = model.predict([feature_vector])
        return float(pred[0])

    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        return 0.5  # neutral fallback


def predict_signal_quality(
    features: dict,
    divergence_type: Optional[str] = None,
    confidence_threshold: float = 0.75,
) -> Tuple[float, bool, str]:
    """
    Predict signal quality from feature dict.

    Args:
        features: dict of feature_name -> value (from extract_features_from_context)
        divergence_type: "bull" or "bear" (optional, for verdict text)
        confidence_threshold: minimum confidence to consider signal strong (default 0.75)

    Returns:
        (confidence, is_strong, verdict) where:
          - confidence: float in [0, 1] — probability of win
          - is_strong: bool — True if confidence >= threshold
          - verdict: str — human-readable assessment
    """
    global _model_cache, _feature_cols_cache, _model_loaded, _pure_python

    # Ensure model is loaded
    if not ensure_model_loaded():
        return 0.50, False, "NO_MODEL (dummy 50%)"

    if _model_cache is None or not _feature_cols_cache:
        return 0.50, False, "NO_MODEL (not loaded)"

    # Convert features to vector
    feature_vector = _features_to_vector(features, _feature_cols_cache)

    # Predict
    confidence = predict_proba_single(_model_cache, feature_vector)
    confidence = max(0.0, min(1.0, confidence))

    # Determine verdict
    is_strong = confidence >= confidence_threshold

    if confidence >= 0.90:
        quality = "EXCELLENT"
    elif confidence >= 0.80:
        quality = "STRONG"
    elif confidence >= confidence_threshold:
        quality = "MODERATE"
    elif confidence >= 0.60:
        quality = "WEAK"
    elif confidence >= 0.50:
        quality = "MARGINAL"
    else:
        quality = "POOR"

    d_type = divergence_type or "unknown"
    verdict = f"{quality} {d_type.upper()} (confidence={confidence:.1%}, threshold={confidence_threshold:.0%})"

    if not is_strong:
        verdict += " [FILTERED OUT]"

    return confidence, is_strong, verdict


def predict_from_context(
    context_json_str: str,
    row_meta: Optional[dict] = None,
    divergence_type: Optional[str] = None,
    confidence_threshold: float = 0.75,
) -> Tuple[float, bool, str]:
    """
    One-shot: extract features from context_json and predict.

    Convenience function combining extract_features_from_context + predict_signal_quality.

    Args:
        context_json_str: JSON string with bar data
        row_meta: additional row metadata (tf_profile, candidate_type, etc.)
        divergence_type: "bull" or "bear"
        confidence_threshold: minimum confidence (default 0.75)

    Returns:
        (confidence, is_strong, verdict) or (0.5, False, "NO_DATA") if extraction failed
    """
    features = extract_features_from_context(context_json_str, row_meta)
    if features is None:
        return 0.5, False, "NO_DATA (feature extraction failed)"

    return predict_signal_quality(features, divergence_type, confidence_threshold)


# ============================================================================
# UTILITIES
# ============================================================================

def get_model_stats() -> dict:
    """Get model metadata and stats."""
    ensure_model_loaded()

    stats = {
        "loaded": _model_loaded,
        "pure_python": _pure_python,
        "n_features": len(_feature_cols_cache) if _feature_cols_cache else 0,
        "feature_cols": _feature_cols_cache or [],
    }

    if _metadata:
        stats["metadata"] = _metadata

    return stats


def load_feature_importance() -> dict:
    """Load feature importance from feature_importance.json."""
    import json as _json

    for path in [
        os.path.join("results", "feature_importance.json"),
        os.path.join("..", "results", "feature_importance.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "feature_importance.json"),
    ]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                if isinstance(data, list):
                    return {item["feature"]: item["importance"] for item in data}
                return data
            except Exception:
                continue
    return {}


def get_top_features(n: int = 10) -> list:
    """Get top N most important features."""
    importance = load_feature_importance()
    sorted_feats = sorted(importance.items(), key=lambda x: x[1], reverse=True)
    return sorted_feats[:n]


def format_signal_for_telegram(
    confidence: float,
    is_strong: bool,
    verdict: str,
    symbol: str = "",
    tf: str = "",
    divergence_type: str = "",
    top_reasons: int = 5,
) -> str:
    """
    Format ML prediction result for Telegram message.

    Returns formatted string with emoji indicators.
    """
    icon = "🟢" if is_strong else "🔴"

    lines = [
        f"{icon} ML Signal Filter",
        f"  Confidence: {confidence:.1%}",
        f"  Verdict: {verdict}",
    ]

    if symbol:
        lines.append(f"  Symbol: {symbol}")
    if tf:
        lines.append(f"  TF: {tf}")
    if divergence_type:
        lines.append(f"  Type: {divergence_type.upper()}")

    # Add top feature contributions
    top_feats = get_top_features(top_reasons)
    if top_feats:
        lines.append(f"  Top features:")
        for fname, imp in top_feats:
            lines.append(f"    - {fname}: {imp:.4f}")

    return "\n".join(lines)


# ============================================================================
# CLI (for testing)
# ============================================================================

def main():
    """CLI entry point for testing classifier_client."""
    import argparse

    parser = argparse.ArgumentParser(description="Test ML classifier client")
    parser.add_argument("--stats", action="store_true", help="Show model stats")
    parser.add_argument("--test-prediction", action="store_true", help="Run test prediction")
    parser.add_argument("--threshold", type=float, default=0.75, help="Confidence threshold")
    args = parser.parse_args()

    if args.stats:
        print("Loading model...")
        stats = get_model_stats()
        print(json.dumps(stats, indent=2, default=str))

        fi = load_feature_importance()
        if fi:
            print(f"\nFeature importance ({len(fi)} features):")
            for name, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:15]:
                print(f"  {name:<30} {imp:.4f}")

    if args.test_prediction:
        print("\nRunning test prediction...")
        print(f"Threshold: {args.threshold:.0%}")

        # Create a dummy feature vector
        dummy_features = {
            "atr": 50.0, "atr_pct": 0.3, "bb_width": 2.5,
            "bias_above_threshold": 1.0, "bias_abs_score": 0.7,
            "bias_dir": 1, "bias_score": 0.7,
            "body_ratio": 0.4, "close_position": 0.6,
            "cmf_score": 0.1, "dist_to_high_pct": 1.5,
            "dist_to_low_pct": 2.0, "ema_slope_pct": 0.5,
            "engulfing_strength": 1.2, "flow_abs_change": 5000,
            "flow_pct_change": 3.0, "flow_scale": 100000,
            "flow_slope": 0.01, "higher_highs_ratio": 0.4,
            "is_bear": 0.0, "is_bull": 1.0, "is_hidden": 0.0,
            "lower_lows_ratio": 0.3, "macd_pct": 0.2,
            "n_past_bars": 11, "price_accel": 0.1,
            "price_flow_ratio": 0.5, "price_max": 65000,
            "price_min": 63000, "price_move_pct": 1.5,
            "price_range": 2000, "price_range_pct": 3.0,
            "pv_correlation": 0.3, "range_contraction": 0.6,
            "regime_bearish": 0.0, "regime_bullish": 1.0,
            "regime_confirmed": 0.0, "regime_score": 0.6,
            "regime_trend": 1, "return_3bar": 0.8,
            "return_6bar": 1.2, "return_full": 2.0,
            "return_last": 0.3, "return_mean": 0.2,
            "return_std": 0.5, "roc_10": 1.5,
            "roc_5": 0.7, "row_atr_pct": 0.3,
            "rsi": 55.0, "rsi_momentum": 2.0,
            "strength": 0.0, "strength_atr_ratio": 0.0,
            "strength_sq": 0.0, "tf_15m": 0.0,
            "tf_1d": 0.0, "tf_1h": 0.0, "tf_4h": 1.0,
            "tf_ordinal": 3, "up_bars_ratio": 0.5,
            "vol_avg": 10000, "vol_median_ratio": 1.2,
            "vol_ratio_entry": 1.5, "vol_ratio_row": 1.3,
            "vol_std": 3000, "vol_std_pct": 0.3,
            "vol_trend": 1.1, "volatility_pct": 0.8,
            "wick_lower": 0.3, "wick_upper": 0.2,
        }

        confidence, is_strong, verdict = predict_signal_quality(
            features=dummy_features,
            divergence_type="bull",
            confidence_threshold=args.threshold,
        )

        print(f"\n  Confidence: {confidence:.1%}")
        print(f"  Is Strong:   {is_strong}")
        print(f"  Verdict:     {verdict}")

        print("\n  Telegram format:")
        print(format_signal_for_telegram(
            confidence, is_strong, verdict,
            symbol="BTCUSDT", tf="4h", divergence_type="bull"
        ))

    if not args.stats and not args.test_prediction:
        parser.print_help()


if __name__ == "__main__":
    main()
