#!/usr/bin/env python3
"""
recalculate_labels.py v3
=======================
Recalculate labels from context_json with proper MFE/MAE analysis.
Supports 4 modes: direction, threshold, atr, hybrid.
NEW: --filter-forward flag to skip rows with no forward bars (max_offset==0).

Usage:
  python tools/recalculate_labels.py --mode direction --dry-run
  python tools/recalculate_labels.py --mode direction --dry-run --filter-forward
  python tools/recalculate_labels.py --mode threshold --dry-run --filter-forward
  python tools/recalculate_labels.py --mode threshold --filter-forward
"""

import argparse
import json
import csv
import sys
import os
from collections import defaultdict

# ─── TF CONFIG ──────────────────────────────────────────────────────────────
# Adaptive horizons and thresholds per timeframe
TF_CONFIG = {
    "15m": {"horizon": 10, "threshold_pct": 0.15, "atr_mult": 0.5},
    "1h":  {"horizon": 8,  "threshold_pct": 0.30, "atr_mult": 0.5},
    "4h":  {"horizon": 6,  "threshold_pct": 0.50, "atr_mult": 0.5},
    "1d":  {"horizon": 5,  "threshold_pct": 1.00, "atr_mult": 0.5},
}


def parse_context_json(raw):
    """Parse context_json string, return list of bar dicts sorted by offset."""
    if not raw or raw.strip() == "" or raw.strip() == "[]":
        return []
    try:
        bars = json.loads(raw)
        if isinstance(bars, list):
            return sorted(bars, key=lambda b: b.get("offset", 0))
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def classify_row(bars):
    """
    Analyze context_json bars to classify the row type.
    Returns: (has_forward, max_offset, min_offset, num_bars, has_entry_bar)
    """
    if not bars:
        return False, 0, 0, 0, False

    offsets = [b.get("offset", 0) for b in bars]
    max_offset = max(offsets)
    min_offset = min(offsets)
    num_bars = len(bars)
    has_entry = 0 in offsets

    has_forward = max_offset > 0
    return has_forward, max_offset, min_offset, num_bars, has_entry


def compute_mfe_mae(bars, horizon, direction="bull"):
    """
    Compute Max Favorable Excursion and Max Adverse Excursion
    over N forward bars from the entry bar (offset=0).

    direction="bull" → MFE = max upward move, MAE = max downward move
    direction="bear" → MFE = max downward move, MAE = max upward move

    Returns: (mfe_pct, mae_pct, mfe_bar_idx, mae_bar_idx, exit_close, bars_used)
    """
    if not bars:
        return 0.0, 0.0, None, None, None, 0

    # Find entry bar (offset=0)
    entry_bar = None
    for b in bars:
        if b.get("offset") == 0:
            entry_bar = b
            break

    if entry_bar is None:
        return 0.0, 0.0, None, None, None, 0

    entry_close = entry_bar.get("close", 0)
    if entry_close <= 0:
        return 0.0, 0.0, None, None, None, 0

    # Collect forward bars up to horizon
    forward_bars = [b for b in bars if 0 < b.get("offset", 0) <= horizon]
    if not forward_bars:
        return 0.0, 0.0, None, None, None, 0

    mfe_pct = 0.0
    mae_pct = 0.0
    exit_close = forward_bars[-1].get("close", entry_close)  # last bar in horizon

    for i, b in enumerate(forward_bars):
        close = b.get("close", entry_close)
        high = b.get("high", close)
        low = b.get("low", close)

        # For bull: upward = favorable, downward = adverse
        # For bear: downward = favorable, upward = adverse
        if direction == "bull":
            fav_move = (high - entry_close) / entry_close * 100
            adv_move = (entry_close - low) / entry_close * 100
        else:  # bear
            fav_move = (entry_close - low) / entry_close * 100
            adv_move = (high - entry_close) / entry_close * 100

        if fav_move > mfe_pct:
            mfe_pct = fav_move
        if adv_move > mae_pct:
            mae_pct = adv_move

    return mfe_pct, mae_pct, None, None, exit_close, len(forward_bars)


def main():
    parser = argparse.ArgumentParser(description="Recalculate labels v3 with forward-bar filtering")
    parser.add_argument("--mode", choices=["direction", "threshold", "atr", "hybrid"],
                        default="direction", help="Label mode")
    parser.add_argument("--dry-run", action="store_true", help="Don't write, just report")
    parser.add_argument("--filter-forward", action="store_true",
                        help="Skip rows with max_offset==0 (no forward bars)")
    parser.add_argument("--input", default="results/unified_dataset_v11.csv",
                        help="Input CSV file")
    parser.add_argument("--output", default=None,
                        help="Output CSV file (default: overwrite input)")
    parser.add_argument("--threshold-factor", type=float, default=1.0,
                        help="Multiply all thresholds by this factor (e.g. 0.7 to lower)")
    parser.add_argument("--diag-only", action="store_true",
                        help="Only show context_json diagnostics, skip labeling")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        # Try with script directory prefix
        script_dir = os.path.dirname(os.path.abspath(__file__))
        alt_path = os.path.join(script_dir, "..", input_path)
        if os.path.exists(alt_path):
            input_path = alt_path
        else:
            print(f"ERROR: Cannot find input file: {input_path}")
            sys.exit(1)

    print(f"Reading: {input_path}")

    # Read CSV
    rows = []
    with open(input_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    print(f"Total rows in CSV: {len(rows)}")

    # ─── DIAGNOSTICS ONLY MODE ────────────────────────────────────────────
    if args.diag_only:
        print("\n" + "=" * 70)
        print("CONTEXT_JSON DIAGNOSTICS")
        print("=" * 70)

        offset_dist = defaultdict(int)
        bar_count_dist = defaultdict(int)
        has_forward_count = 0
        no_forward_count = 0
        no_context_count = 0
        offset_range_dist = defaultdict(int)  # "min_offset..max_offset" → count
        samples_no_forward = []

        for i, row in enumerate(rows):
            cj = row.get("context_json", "")
            bars = parse_context_json(cj)

            if not bars:
                no_context_count += 1
                continue

            has_fwd, max_off, min_off, n_bars, has_entry = classify_row(bars)
            bar_count_dist[n_bars] += 1
            offset_range_dist[f"{min_off}..{max_off}"] += 1

            if has_fwd:
                has_forward_count += 1
            else:
                no_forward_count += 1
                if len(samples_no_forward) < 5:
                    tf = row.get("tf_profile", "?")
                    sym = row.get("symbol", "?")
                    ctype = row.get("candidate_type", "?")
                    samples_no_forward.append(
                        f"  Row {i}: bars={n_bars}, range=[{min_off}..{max_off}], "
                        f"tf={tf}, sym={sym}, type={ctype}"
                    )

        print(f"\nRows with valid context_json: {has_forward_count + no_forward_count}")
        print(f"Rows with NO/empty context:   {no_context_count}")
        print(f"Rows WITH forward bars:      {has_forward_count} ({has_forward_count/(has_forward_count+no_forward_count)*100:.1f}%)")
        print(f"Rows WITHOUT forward bars:   {no_forward_count} ({no_forward_count/(has_forward_count+no_forward_count)*100:.1f}%)")

        print(f"\nBar count distribution:")
        for bc in sorted(bar_count_dist.keys()):
            print(f"  {bc} bars: {bar_count_dist[bc]} rows")

        print(f"\nOffset range distribution (min..max):")
        for rng in sorted(offset_range_dist.keys(), key=lambda x: [int(p) for p in x.split("..")]):
            print(f"  [{rng}]: {offset_range_dist[rng]} rows")

        if samples_no_forward:
            print(f"\nSample rows WITHOUT forward bars:")
            for s in samples_no_forward:
                print(s)

        print("\n" + "=" * 70)
        print("[DIAG ONLY] Done.")
        return

    # ─── LABELING MODE ───────────────────────────────────────────────────
    mode = args.mode
    print(f"Mode: {mode}")
    if args.filter_forward:
        print("Filter: ENABLED (skip rows with max_offset==0)")
    if args.threshold_factor != 1.0:
        print(f"Threshold factor: {args.threshold_factor}x")

    # Stats accumulators
    stats_by_tf = defaultdict(lambda: {
        "total": 0, "win": 0, "loss": 0,
        "mfe_sum": 0.0, "mae_sum": 0.0,
        "mfe_zero": 0,  # MFE == 0 (price never moves in predicted direction)
    })
    stats_by_type = defaultdict(lambda: {"total": 0, "win": 0, "loss": 0, "mfe_sum": 0.0})
    stats_by_sym = defaultdict(lambda: {"total": 0, "win": 0, "loss": 0, "mfe_sum": 0.0})
    stats_by_sym_type = defaultdict(lambda: {"total": 0, "win": 0, "loss": 0})

    filtered_out = 0
    labeled_count = 0
    failed_count = 0

    for i, row in enumerate(rows):
        cj_raw = row.get("context_json", "")
        bars = parse_context_json(cj_raw)
        tf = row.get("tf_profile", "").strip()
        sym = row.get("symbol", "").strip()
        ctype = row.get("candidate_type", "").strip().lower()

        if tf not in TF_CONFIG:
            # Try to match partial
            for k in TF_CONFIG:
                if k in tf:
                    tf = k
                    break

        if tf not in TF_CONFIG:
            failed_count += 1
            continue

        cfg = TF_CONFIG[tf]
        horizon = cfg["horizon"]
        threshold_pct = cfg["threshold_pct"] * args.threshold_factor

        # ─── FORWARD BAR FILTER ──────────────────────────────────────
        if args.filter_forward:
            has_fwd, max_off, min_off, n_bars, has_entry = classify_row(bars)
            if not has_fwd:
                filtered_out += 1
                continue

        # Normalize direction
        direction = "bull" if ctype in ("bull", "bullish") else "bear"

        mfe, mae, _, _, exit_close, bars_used = compute_mfe_mae(bars, horizon, direction)

        if bars_used == 0:
            failed_count += 1
            continue

        # ─── LABEL DECISION ──────────────────────────────────────────
        is_win = False

        if mode == "direction":
            # Win if price moves AT ALL in predicted direction
            is_win = mfe > 0

        elif mode == "threshold":
            # Win if MFE >= threshold
            is_win = mfe >= threshold_pct

        elif mode == "atr":
            # Win if MFE >= ATR-based threshold
            # Since ATR isn't in context_json bars, fall back to threshold_pct * atr_mult
            atr_threshold = threshold_pct * cfg["atr_mult"]
            is_win = mfe >= atr_threshold

        elif mode == "hybrid":
            # Direction correctness + strength bonus
            if mfe > 0:
                if mae > 0 and mfe / mae > 1.0:
                    is_win = True  # positive edge
                elif mfe >= threshold_pct:
                    is_win = True  # strong move even if adverse
                # else: direction right but weak → still counts as win
                # Actually let's make hybrid stricter:
                is_win = mfe > 0 and mfe >= threshold_pct * 0.5

        # ─── UPDATE STATS ─────────────────────────────────────────────
        label_val = 1 if is_win else 0

        # Update row data
        row["label_value"] = str(label_val)
        row["mfe_pct"] = f"{mfe:.4f}"
        row["mae_pct"] = f"{mae:.4f}"
        row["label_horizon"] = str(horizon)
        row["label_mode"] = mode

        labeled_count += 1

        # Accumulate stats
        stats_by_tf[tf]["total"] += 1
        stats_by_tf[tf]["mfe_sum"] += mfe
        stats_by_tf[tf]["mae_sum"] += mae
        if mfe == 0:
            stats_by_tf[tf]["mfe_zero"] += 1

        if is_win:
            stats_by_tf[tf]["win"] += 1
            stats_by_type[ctype]["win"] += 1
            stats_by_sym[sym]["win"] += 1
            stats_by_sym_type[f"{sym}_{ctype}"]["win"] += 1
        else:
            stats_by_tf[tf]["loss"] += 1
            stats_by_type[ctype]["loss"] += 1
            stats_by_sym[sym]["loss"] += 1
            stats_by_sym_type[f"{sym}_{ctype}"]["loss"] += 1

        stats_by_type[ctype]["total"] += 1
        stats_by_type[ctype]["mfe_sum"] += mfe
        stats_by_sym[sym]["total"] += 1
        stats_by_sym[sym]["mfe_sum"] += mfe
        stats_by_sym_type[f"{sym}_{ctype}"]["total"] += 1

    # ─── PRINT REPORT ─────────────────────────────────────────────────────
    if args.filter_forward:
        print(f"\n*** FILTERED OUT: {filtered_out} rows (no forward bars) ***")
        print(f"*** REMAINING:    {len(rows) - filtered_out} rows ***")

    print()
    if mode == "threshold":
        print(f"{'TF':<6} {'Horizon':>9} {'Threshold':>11} {'Total':>7} {'Win':>7} "
              f"{'Loss':>7} {'WR%':>8} {'AvgMFE':>9} {'AvgMAE':>9}")
        print("-" * 80)
        for tf in ["15m", "1h", "4h", "1d"]:
            s = stats_by_tf[tf]
            if s["total"] == 0:
                continue
            cfg = TF_CONFIG[tf]
            wr = s["win"] / s["total"] * 100
            avg_mfe = s["mfe_sum"] / s["total"]
            avg_mae = s["mae_sum"] / s["total"]
            thr = cfg["threshold_pct"] * args.threshold_factor
            print(f"{tf:<6} H={cfg['horizon']:>5d} {thr:>9.2f}% {s['total']:>7} {s['win']:>7} "
                  f"{s['loss']:>7} {wr:>7.1f}% {avg_mfe:>8.3f}% {avg_mae:>8.3f}%")
    else:
        print(f"{'TF':<6} {'Horizon':>9} {'Total':>7} {'Win':>7} "
              f"{'Loss':>7} {'WR%':>8} {'AvgMFE':>9} {'AvgMAE':>9}")
        print("-" * 68)
        for tf in ["15m", "1h", "4h", "1d"]:
            s = stats_by_tf[tf]
            if s["total"] == 0:
                continue
            cfg = TF_CONFIG[tf]
            wr = s["win"] / s["total"] * 100
            avg_mfe = s["mfe_sum"] / s["total"]
            avg_mae = s["mae_sum"] / s["total"]
            print(f"{tf:<6} H={cfg['horizon']:>5d} {s['total']:>7} {s['win']:>7} "
                  f"{s['loss']:>7} {wr:>7.1f}% {avg_mfe:>8.3f}% {avg_mae:>8.3f}%")
    print("-" * 68)

    # MFE=0 analysis per TF
    print("\nMFE=0 analysis (price never moved in predicted direction):")
    for tf in ["15m", "1h", "4h", "1d"]:
        s = stats_by_tf[tf]
        if s["total"] == 0:
            continue
        pct_zero = s["mfe_zero"] / s["total"] * 100
        print(f"  {tf}: {s['mfe_zero']}/{s['total']} ({pct_zero:.1f}%) had MFE=0")

    print("\nBy type:")
    for ctype in sorted(stats_by_type.keys()):
        s = stats_by_type[ctype]
        if s["total"] == 0:
            continue
        wr = s["win"] / s["total"] * 100
        avg_mfe = s["mfe_sum"] / s["total"]
        print(f"  {ctype:<6} n={s['total']:<5} W={s['win']:<5} L={s['loss']:<5} "
              f"WR={wr:.1f}% AvgMFE={avg_mfe:.3f}%")

    print("\nBy symbol:")
    for sym in sorted(stats_by_sym.keys()):
        s = stats_by_sym[sym]
        if s["total"] == 0:
            continue
        wr = s["win"] / s["total"] * 100
        avg_mfe = s["mfe_sum"] / s["total"]
        print(f"  {sym:<12} n={s['total']:<5} W={s['win']:<5} L={s['loss']:<5} "
              f"WR={wr:.1f}% AvgMFE={avg_mfe:.3f}%")

    print("\nBy symbol x type:")
    for key in sorted(stats_by_sym_type.keys()):
        s = stats_by_sym_type[key]
        if s["total"] == 0:
            continue
        wr = s["win"] / s["total"] * 100
        print(f"  {key:<20} n={s['total']:<5} W={s['win']:<5} L={s['loss']:<5} WR={wr:.1f}%")

    total_wins = sum(s["win"] for s in stats_by_tf.values())
    total_losses = sum(s["loss"] for s in stats_by_tf.values())
    total_labeled = total_wins + total_losses

    print(f"\n{'='*50}")
    print(f"TOTAL LABELED: {total_labeled} / {len(rows)}")
    if args.filter_forward:
        print(f"FILTERED OUT: {filtered_out} (no forward bars)")
    print(f"WINS: {total_wins} | LOSSES: {total_losses}")
    if total_labeled > 0:
        print(f"OVERALL WR: {total_wins / total_labeled * 100:.1f}%")
    print(f"FAILED (no data): {failed_count}")

    # Edge analysis per TF
    print(f"\n{'='*50}")
    print("EDGE ANALYSIS (per TF):")
    for tf in ["15m", "1h", "4h", "1d"]:
        s = stats_by_tf[tf]
        if s["total"] == 0:
            continue
        wr = s["win"] / s["total"] * 100
        avg_mfe = s["mfe_sum"] / s["total"]
        avg_mae = s["mae_sum"] / s["total"]
        ratio = avg_mfe / avg_mae if avg_mae > 0 else 0
        edge = "POSITIVE" if ratio > 1.0 else "NEGATIVE"
        print(f"  {tf}    WR={wr:5.1f}% | AvgMFE={avg_mfe:6.3f}% | "
              f"AvgMAE={avg_mae:6.3f}% | Ratio={ratio:.2f}x | {edge}")

    print(f"\n{'='*50}")

    if args.dry_run:
        print("[DRY RUN] No changes written.")
    else:
        output_path = args.output or input_path
        # Ensure new columns are in fieldnames
        new_cols = ["mfe_pct", "mae_pct", "label_horizon", "label_mode"]
        for col in new_cols:
            if col not in fieldnames:
                fieldnames.append(col)

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Written to: {output_path}")


if __name__ == "__main__":
    main()
