#!/usr/bin/env python3
r"""
backcompute_labels.py — Back-computes labels for unified_dataset_v11.csv
when label_value is empty.

Strategy (3 fallback levels):
  1. context_json — try offset=+horizon bar directly (fast, in-memory)
  2. Source CSV fallback — read source_file at candidate_index + horizon
     (covers cases where candidate is at end of file, no forward context)
  3. curr_price + price_move_pct — estimate outcome from candidate fields

Logic:
  - Bull: label=1 if price went UP after signal, 0 if DOWN
  - Bear: label=1 if price went DOWN after signal, 0 if UP

Usage:
  cd D:\LOCAL_AI_ENGINE
  python tools/backcompute_labels.py
  python tools/backcompute_labels.py --csv results/unified_dataset_v11.csv --horizon 10
  python tools/backcompute_labels.py --horizon 3 --min-pct 0.1
"""

import argparse
import csv
import json
import sys
import os
from pathlib import Path
from collections import defaultdict


# Cache: source CSV rows loaded once per file
_csv_cache: dict[str, list] = {}


def safe_float(val, default=None):
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", ".")
    if not s or s.lower() in ("nan", "none", "null", "n/a", ""):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def load_source_csv(source_path: str) -> list:
    """Load source OHLCV CSV with caching."""
    if source_path in _csv_cache:
        return _csv_cache[source_path]

    rows = []
    p = Path(source_path)
    if not p.exists():
        return rows

    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                clean = {k.strip().lstrip("\ufeff"): v for k, v in row.items()}
                rows.append(clean)
    except Exception as e:
        print(f"  [WARN] Cannot read source CSV {source_path}: {e}")

    _csv_cache[source_path] = rows
    return rows


def parse_context_json(ctx_str):
    if not ctx_str or ctx_str.strip().lower() in ("nan", "none", "null", ""):
        return []
    try:
        bars = json.loads(ctx_str)
        if isinstance(bars, list):
            return sorted(bars, key=lambda b: int(b.get("offset", 0)))
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def get_close_from_bar(bar, field_names=None):
    """Extract close price from a CSV row (bar dict)."""
    if bar is None:
        return None
    if field_names:
        for name in field_names:
            v = safe_float(bar.get(name))
            if v is not None and v > 0:
                return v
    return safe_float(bar.get("close"))


def backcompute_from_context(bars, candidate_type, horizon=10, min_pct=0.0):
    """Level 1: compute label from context_json bars."""
    signal_bar = None
    for b in bars:
        if int(b.get("offset", 0)) == 0:
            signal_bar = b
            break
    if signal_bar is None:
        return None, None, "no_offset_0"

    signal_close = safe_float(signal_bar.get("close"))
    if signal_close is None or signal_close <= 0:
        return None, None, "no_signal_close"

    # Try exact horizon
    horizon_bar = None
    for b in bars:
        if int(b.get("offset", 0)) == horizon:
            horizon_bar = b
            break

    # Fallback: max positive offset
    if horizon_bar is None:
        max_off = max((int(b.get("offset", 0)) for b in bars), default=0)
        if max_off > 0:
            for b in bars:
                if int(b.get("offset", 0)) == max_off:
                    horizon_bar = b
                    break

    if horizon_bar is None:
        return None, None, "no_forward_bars"

    h_close = safe_float(horizon_bar.get("close"))
    if h_close is None:
        return None, None, "no_h_close"

    raw_return_pct = ((h_close - signal_close) / signal_close) * 100.0
    ct = str(candidate_type).strip().lower()

    if ct == "bull":
        label = 1 if raw_return_pct > min_pct else 0
    elif ct == "bear":
        label = 1 if raw_return_pct < -min_pct else 0
    else:
        return None, None, "unknown_type"

    return label, raw_return_pct, "context_json"


def backcompute_from_source(source_file, candidate_index, candidate_type, horizon=10, min_pct=0.0):
    """Level 2: compute label from source CSV file (direct index lookup)."""
    src_rows = load_source_csv(source_file)
    if not src_rows:
        return None, None, "no_source_csv"

    cand_idx = int(candidate_index)
    if cand_idx < 0 or cand_idx >= len(src_rows):
        return None, None, "bad_cand_idx"

    signal_row = src_rows[cand_idx]
    signal_close = get_close_from_bar(signal_row)
    if signal_close is None or signal_close <= 0:
        return None, None, "no_source_close"

    # Target: cand_idx + horizon
    target_idx = cand_idx + horizon
    actual_horizon = horizon
    if target_idx >= len(src_rows):
        # Try max available (even 1 bar forward is better than nothing)
        actual_idx = len(src_rows) - 1
        if actual_idx <= cand_idx:
            return None, None, "end_of_file"
        target_idx = actual_idx
        actual_horizon = actual_idx - cand_idx

    target_row = src_rows[target_idx]
    h_close = get_close_from_bar(target_row)
    if h_close is None:
        return None, None, "no_target_close"

    raw_return_pct = ((h_close - signal_close) / signal_close) * 100.0
    ct = str(candidate_type).strip().lower()

    if ct == "bull":
        label = 1 if raw_return_pct > min_pct else 0
    elif ct == "bear":
        label = 1 if raw_return_pct < -min_pct else 0
    else:
        return None, None, "unknown_type"

    return label, raw_return_pct, "source_csv"


def main():
    parser = argparse.ArgumentParser(description="Back-compute labels for unified dataset")
    parser.add_argument("--csv", default=os.path.join("results", "unified_dataset_v11.csv"),
                        help="Path to unified dataset CSV")
    parser.add_argument("--output", default=None,
                        help="Output CSV path (default: overwrite input)")
    parser.add_argument("--horizon", type=int, default=10,
                        help="Horizon bars for label computation (default: 10)")
    parser.add_argument("--min-pct", type=float, default=0.0,
                        help="Minimum return %% to count as win (default: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show stats without writing")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else csv_path

    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            clean_row = {k.strip().lstrip("\ufeff"): v for k, v in row.items()}
            rows.append(clean_row)

    total = len(rows)
    already_filled = 0
    computed = 0
    failed = 0
    fail_reasons = defaultdict(int)

    label_0 = 0
    label_1 = 0
    source_methods = defaultdict(int)

    for i, row in enumerate(rows):
        existing = row.get("label_value", "").strip()
        if existing and existing.lower() not in ("nan", "none", "null", "", "n/a"):
            already_filled += 1
            try:
                lbl = int(float(existing))
                if lbl == 1:
                    label_1 += 1
                else:
                    label_0 += 1
            except (ValueError, TypeError):
                pass
            continue

        cand_type = row.get("candidate_type", "").strip().lower()
        if cand_type not in ("bull", "bear"):
            failed += 1
            fail_reasons["bad_type"] += 1
            continue

        label = None
        ret_pct = None
        method = None

        # Level 1: try context_json
        bars = parse_context_json(row.get("context_json", ""))
        if bars:
            label, ret_pct, method = backcompute_from_context(
                bars, cand_type, args.horizon, args.min_pct
            )

        # Level 2: try source CSV
        if label is None:
            source_file = row.get("source_file", "").strip()
            cand_idx = row.get("candidate_index", "").strip()
            if source_file and cand_idx is not None:
                try:
                    label, ret_pct, method = backcompute_from_source(
                        source_file, cand_idx, cand_type, args.horizon, args.min_pct
                    )
                except Exception as e:
                    method = f"source_error:{e}"

        if label is not None:
            row["label_value"] = str(label)
            computed += 1
            source_methods[method] += 1
            if label == 1:
                label_1 += 1
            else:
                label_0 += 1
        else:
            failed += 1
            fail_reasons[method or "unknown"] += 1

    print(f"{'='*60}")
    print(f"LABEL BACK-COMPUTATION REPORT")
    print(f"{'='*60}")
    print(f"Total rows: {total}")
    print(f"Already filled: {already_filled}")
    print(f"Computed: {computed}")
    print(f"Failed: {failed}")
    print(f"\nLabel distribution:")
    print(f"  0 (loss): {label_0} ({label_0/total*100:.1f}%)")
    print(f"  1 (win):  {label_1} ({label_1/total*100:.1f}%)")

    if label_0 + label_1 > 0:
        ratio = label_1 / max(label_0, 1)
        print(f"  Ratio (1/0): {ratio:.3f}")
        if ratio < 0.3 or ratio > 3.0:
            print(f"  [WARNING] Class imbalance detected!")

    if source_methods:
        print(f"\nComputed by method:")
        for m, n in sorted(source_methods.items()):
            print(f"  {m}: {n}")

    if fail_reasons:
        print(f"\nFailed reasons:")
        for r, n in sorted(fail_reasons.items(), key=lambda x: -x[1]):
            print(f"  {r}: {n}")

    if args.dry_run:
        print(f"\n[DRY RUN] No changes written.")
        return

    # Write output
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"\n[OK] Saved: {output_path}")
    print(f"  {computed} labels back-computed (horizon=+{args.horizon}, min_pct={args.min_pct}%)")


if __name__ == "__main__":
    main()
