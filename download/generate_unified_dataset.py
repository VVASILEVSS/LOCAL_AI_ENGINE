#!/usr/bin/env python3
"""
Generate unified_dataset CSV from candidate JSON files.
Equivalent to v11generate_unified_dataset.ps1 but in Python.
"""
import json
import math
import os
import glob
from pathlib import Path

import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "results"
DATA_DIR = BASE_DIR / "data" / "ohlcv" / "current"
OUTPUT_CSV = RESULTS_DIR / "unified_dataset_v11.csv"

PIVOT_RIGHT = {"15m": 12, "1h": 16, "4h": 18, "1d": 20}
CONTEXT_N = 10

CANDIDATE_INDEX_KEYS = ("i", "idx", "index", "candidate_index")
TYPE_KEYS = ("type", "kind", "direction", "signal_type")
TIME_KEYS = ("time", "datetime", "timestamp")
PREV_PRICE_KEYS = ("prevPrice", "prev_price")
CURR_PRICE_KEYS = ("currPrice", "curr_price", "price", "candidate_price", "pivot_price")
PRICE_MOVE_PCT_KEYS = ("priceMovePct", "price_move_pct")
PREV_FLOW_KEYS = ("prevFlow", "prev_flow")
CURR_FLOW_KEYS = ("currFlow", "curr_flow")
FLOW_PCT_CHANGE_KEYS = ("flowPctChange", "flow_pct_change")
FLOW_ABS_CHANGE_KEYS = ("flowAbsChange", "flow_abs_change")
FLOW_SCALE_KEYS = ("flowScale", "flow_scale", "flow", "flow_value", "money_flow")
MIN_FLOW_ABS_THRESHOLD_KEYS = ("minFlowAbsThreshold", "min_flow_abs_threshold")
ATR_KEYS = ("atr", "atr_value")
VOL_RATIO_KEYS = ("volRatio", "vol_ratio", "volume_ratio")
HIDDEN_KEYS = ("hidden", "is_hidden", "hidden_divergence")
STRENGTH_KEYS = ("strength", "div_strength", "strength_score")
REGIME_SCORE_KEYS = ("regimeScore", "regime_score")
REGIME_TREND_KEYS = ("regimeTrend", "regime_trend")
REGIME_CONFIRMED_KEYS = ("regimeConfirmed", "regime_confirmed")
BIAS_SCORE_KEYS = ("biasScore", "bias_score")
BIAS_DIR_KEYS = ("biasDir", "bias_dir")
BIAS_ABOVE_KEYS = ("biasAbove", "bias_above")
FLOW_SLOPE_KEYS = ("flowSlope", "flow_slope")
CMF_SCORE_KEYS = ("cmfScore", "cmf_score")
SCORE_KEYS = ("candidate_score", "score", "confidence", "conf")
QUALITY_KEYS = ("candidate_quality", "quality", "grade")

OUTPUT_COLUMNS = [
    "symbol", "tf_profile", "timeframe", "source_file", "rows_count",
    "candidate_index", "label_index", "pivot_right", "context_n", "label_anchor",
    "label_value", "label_datetime", "candidate_time", "candidate_type",
    "prev_price", "curr_price", "price_move_pct",
    "prev_flow", "curr_flow", "flow_pct_change", "flow_abs_change", "flow_scale",
    "min_flow_abs_threshold", "atr", "vol_ratio",
    "hidden_divergence", "div_strength",
    "regime_score", "regime_trend", "regime_confirmed",
    "bias_score", "bias_dir", "bias_above",
    "flow_slope", "cmf_score",
    "candidate_score", "candidate_quality", "candidate_strength",
    "candidate_flow_ratio", "candidate_atr_ratio",
    "context_json",
]


def pick(d: dict, keys: tuple):
    """Return the first matching key's value from dict, or None."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def compute_score(c: dict):
    """Compute candidate score if not already present."""
    price_move_pct = pick(c, PRICE_MOVE_PCT_KEYS) or 0
    flow_abs_change = pick(c, FLOW_ABS_CHANGE_KEYS) or 0
    flow_scale = pick(c, FLOW_SCALE_KEYS) or 0
    min_threshold = pick(c, MIN_FLOW_ABS_THRESHOLD_KEYS) or 0
    atr = pick(c, ATR_KEYS) or 0
    curr_price = pick(c, CURR_PRICE_KEYS) or 0

    price_comp = clamp(abs(price_move_pct) / 0.01)
    flow_ratio = abs(flow_abs_change) / min_threshold if min_threshold > 0 else 0
    flow_comp = clamp(flow_ratio / 5)
    scale_comp = clamp(math.log10(flow_scale + 1) / 4) if flow_scale > 0 else 0
    atr_ratio = atr / curr_price if (curr_price > 0 and atr > 0) else 0
    atr_comp = clamp(atr_ratio / 0.02)

    score01 = 0.40 * price_comp + 0.35 * flow_comp + 0.15 * scale_comp + 0.10 * atr_comp
    score = round(score01 * 100, 2)
    quality = "strong" if score >= 70 else ("medium" if score >= 40 else "weak")
    strength = round(score01, 4)
    flow_r = round(flow_ratio, 4) if min_threshold > 0 else None
    atr_r = round(atr_ratio, 4) if (curr_price > 0 and atr > 0) else None
    return score, quality, strength, flow_r, atr_r


def load_candidates_json(json_path: Path):
    with open(json_path, "r") as f:
        data = json.load(f)
    return data


def build_context_window(csv_df, candidate_idx: int, context_n: int = CONTEXT_N):
    """Build the context window around candidate_index."""
    start = max(0, candidate_idx - context_n)
    end = min(len(csv_df) - 1, candidate_idx + context_n)
    ctx = []
    for k in range(start, end + 1):
        row = csv_df.iloc[k]
        ctx.append({
            "offset": k - candidate_idx,
            "datetime": str(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "label_in_window": "YES" if k == candidate_idx else "NO",
        })
    return ctx


def process_single_json(json_path: Path, base_dir: Path) -> list:
    """Process one *_candidates.json file and return list of output row dicts."""
    data = load_candidates_json(json_path)

    # Resolve paths
    file_rel = data.get("file", "")
    csv_path = base_dir / file_rel
    tf_profile = data.get("profile") or data.get("tf_profile", "1h")
    rows_count = data.get("rows", len(data.get("candidates", [])))
    candidates = data.get("candidates", [])

    if not csv_path.exists():
        print(f"  [WARN] CSV not found: {csv_path}")
        return []

    # Extract symbol from CSV filename
    csv_name = csv_path.stem  # e.g. "BTCUSDT_1h"
    symbol = csv_name.split("_")[0]

    # timeframe = second part of CSV filename (e.g. "1h")
    timeframe = csv_name.split("_")[1] if "_" in csv_name else tf_profile

    # Load OHLCV CSV
    csv_df = pd.read_csv(csv_path)

    pivot_right = PIVOT_RIGHT.get(tf_profile, 16)

    rows_out = []
    for cand in candidates:
        candidate_idx = pick(cand, CANDIDATE_INDEX_KEYS)
        if candidate_idx is None:
            continue

        candidate_idx = int(candidate_idx)
        label_index = candidate_idx

        # candidate_time = datetime from CSV at candidate_idx (or raw "time" from candidate)
        candidate_time_raw = pick(cand, TIME_KEYS)
        if candidate_time_raw is not None:
            try:
                cand_time_int = int(candidate_time_raw)
                if 0 <= cand_time_int < len(csv_df):
                    candidate_time = str(csv_df.iloc[cand_time_int]["time"])
                else:
                    candidate_time = str(candidate_time_raw)
            except (ValueError, TypeError):
                candidate_time = str(candidate_time_raw)
        else:
            candidate_time = ""

        # label_datetime = datetime from CSV at label_index
        label_datetime = ""
        if 0 <= label_index < len(csv_df):
            label_datetime = str(csv_df.iloc[label_index]["time"])

        # Build context window
        ctx = build_context_window(csv_df, candidate_idx)

        # Extract all candidate fields with flexible key names
        candidate_type = pick(cand, TYPE_KEYS) or ""
        prev_price = pick(cand, PREV_PRICE_KEYS)
        curr_price = pick(cand, CURR_PRICE_KEYS)
        price_move_pct = pick(cand, PRICE_MOVE_PCT_KEYS)
        prev_flow = pick(cand, PREV_FLOW_KEYS)
        curr_flow = pick(cand, CURR_FLOW_KEYS)
        flow_pct_change = pick(cand, FLOW_PCT_CHANGE_KEYS)
        flow_abs_change = pick(cand, FLOW_ABS_CHANGE_KEYS)
        flow_scale = pick(cand, FLOW_SCALE_KEYS)
        min_flow_abs_threshold = pick(cand, MIN_FLOW_ABS_THRESHOLD_KEYS)
        atr = pick(cand, ATR_KEYS)
        vol_ratio = pick(cand, VOL_RATIO_KEYS)
        hidden_divergence = pick(cand, HIDDEN_KEYS)
        div_strength = pick(cand, STRENGTH_KEYS)
        regime_score = pick(cand, REGIME_SCORE_KEYS)
        regime_trend = pick(cand, REGIME_TREND_KEYS)
        regime_confirmed = pick(cand, REGIME_CONFIRMED_KEYS)
        bias_score = pick(cand, BIAS_SCORE_KEYS)
        bias_dir = pick(cand, BIAS_DIR_KEYS)
        bias_above = pick(cand, BIAS_ABOVE_KEYS)
        flow_slope = pick(cand, FLOW_SLOPE_KEYS)
        cmf_score = pick(cand, CMF_SCORE_KEYS)

        # Compute score if not already present
        existing_score = pick(cand, SCORE_KEYS)
        existing_quality = pick(cand, QUALITY_KEYS)

        if existing_score is not None:
            candidate_score = float(existing_score)
            candidate_quality = existing_quality if existing_quality is not None else ""
            # Compute strength, flow_ratio, atr_ratio anyway
            _, _, strength, flow_r, atr_r = compute_score(cand)
            candidate_strength = pick(cand, STRENGTH_KEYS)
            if candidate_strength is None:
                candidate_strength = strength
            candidate_flow_ratio = flow_r
            candidate_atr_ratio = atr_r
        else:
            candidate_score, candidate_quality, candidate_strength, candidate_flow_ratio, candidate_atr_ratio = compute_score(cand)

        row = {
            "symbol": symbol,
            "tf_profile": tf_profile,
            "timeframe": timeframe,
            "source_file": file_rel,
            "rows_count": rows_count,
            "candidate_index": candidate_idx,
            "label_index": label_index,
            "pivot_right": pivot_right,
            "context_n": CONTEXT_N,
            "label_anchor": "center",
            "label_value": None,
            "label_datetime": label_datetime,
            "candidate_time": candidate_time,
            "candidate_type": candidate_type,
            "prev_price": prev_price,
            "curr_price": curr_price,
            "price_move_pct": price_move_pct,
            "prev_flow": prev_flow,
            "curr_flow": curr_flow,
            "flow_pct_change": flow_pct_change,
            "flow_abs_change": flow_abs_change,
            "flow_scale": flow_scale,
            "min_flow_abs_threshold": min_flow_abs_threshold,
            "atr": atr,
            "vol_ratio": vol_ratio,
            "hidden_divergence": hidden_divergence,
            "div_strength": div_strength,
            "regime_score": regime_score,
            "regime_trend": regime_trend,
            "regime_confirmed": regime_confirmed,
            "bias_score": bias_score,
            "bias_dir": bias_dir,
            "bias_above": bias_above,
            "flow_slope": flow_slope,
            "cmf_score": cmf_score,
            "candidate_score": candidate_score,
            "candidate_quality": candidate_quality,
            "candidate_strength": candidate_strength,
            "candidate_flow_ratio": candidate_flow_ratio,
            "candidate_atr_ratio": candidate_atr_ratio,
            "context_json": json.dumps(ctx, separators=(",", ":"),
        }
        rows_out.append(row)

    return rows_out


def main():
    all_rows = []
    symbol_counts = {}

    json_files = sorted(RESULTS_DIR.glob("*_candidates.json"))
    if not json_files:
        print("No *_candidates.json files found in", RESULTS_DIR)
        return

    print(f"Found {len(json_files)} candidate JSON files")

    for jf in json_files:
        print(f"Processing: {jf.name}")
        rows = process_single_json(jf, BASE_DIR)
        all_rows.extend(rows)
        for r in rows:
            sym = r["symbol"]
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1

    if not all_rows:
        print("No candidate rows produced.")
        return

    df = pd.DataFrame(all_rows, columns=OUTPUT_COLUMNS)
    df.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*60}")
    print(f"Wrote {len(df)} rows to {OUTPUT_CSV}")
    print(f"Per-symbol breakdown:")
    for sym in sorted(symbol_counts):
        print(f"  {sym}: {symbol_counts[sym]}")
    print(f"Total: {len(df)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
