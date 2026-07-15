#!/usr/bin/env python3
"""
recalculate_labels.py v4
=======================
Recalculate labels from context_json with proper MFE/MAE analysis.
Supports 4 modes: direction, threshold, atr, hybrid.
v4: Added --oos-test for out-of-sample validation (70/30 split).

Usage:
  python tools/recalculate_labels.py --mode threshold --dry-run --filter-forward
  python tools/recalculate_labels.py --mode threshold --dry-run --filter-forward --oos-test
  python tools/recalculate_labels.py --mode threshold --dry-run --filter-forward --diag-only
"""

import argparse
import json
import csv
import sys
import os
import random
from collections import defaultdict

# ─── TF CONFIG ──────────────────────────────────────────────────────────────
TF_CONFIG = {
    "15m": {"horizon": 10, "threshold_pct": 0.15, "atr_mult": 0.5},
    "1h":  {"horizon": 8,  "threshold_pct": 0.30, "atr_mult": 0.5},
    "4h":  {"horizon": 6,  "threshold_pct": 0.50, "atr_mult": 0.5},
    "1d":  {"horizon": 5,  "threshold_pct": 1.00, "atr_mult": 0.5},
}


def parse_context_json(raw):
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
    if not bars:
        return 0.0, 0.0, None, None, None, 0

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

    forward_bars = [b for b in bars if 0 < b.get("offset", 0) <= horizon]
    if not forward_bars:
        return 0.0, 0.0, None, None, None, 0

    mfe_pct = 0.0
    mae_pct = 0.0
    exit_close = forward_bars[-1].get("close", entry_close)

    for i, b in enumerate(forward_bars):
        close = b.get("close", entry_close)
        high = b.get("high", close)
        low = b.get("low", close)

        if direction == "bull":
            fav_move = (high - entry_close) / entry_close * 100
            adv_move = (entry_close - low) / entry_close * 100
        else:
            fav_move = (entry_close - low) / entry_close * 100
            adv_move = (high - entry_close) / entry_close * 100

        if fav_move > mfe_pct:
            mfe_pct = fav_move
        if adv_move > mae_pct:
            mae_pct = adv_move

    return mfe_pct, mae_pct, None, None, exit_close, len(forward_bars)


def label_one_row(row, mode, threshold_factor=1.0):
    """Compute label for one row. Returns (is_win, mfe, mae, tf) or None if failed."""
    cj_raw = row.get("context_json", "")
    bars = parse_context_json(cj_raw)
    tf = row.get("tf_profile", "").strip()
    ctype = row.get("candidate_type", "").strip().lower()

    for k in TF_CONFIG:
        if k in tf:
            tf = k
            break
    if tf not in TF_CONFIG:
        return None

    cfg = TF_CONFIG[tf]
    horizon = cfg["horizon"]
    threshold_pct = cfg["threshold_pct"] * threshold_factor

    has_fwd, max_off, min_off, n_bars, has_entry = classify_row(bars)
    if not has_fwd:
        return None

    direction = "bull" if ctype in ("bull", "bullish") else "bear"
    mfe, mae, _, _, exit_close, bars_used = compute_mfe_mae(bars, horizon, direction)

    if bars_used == 0:
        return None

    if mode == "direction":
        is_win = mfe > 0
    elif mode == "threshold":
        is_win = mfe >= threshold_pct
    elif mode == "atr":
        atr_threshold = threshold_pct * cfg["atr_mult"]
        is_win = mfe >= atr_threshold
    elif mode == "hybrid":
        is_win = mfe > 0 and mfe >= threshold_pct * 0.5
    else:
        is_win = mfe > 0

    return is_win, mfe, mae, tf


def print_stats(labeled_rows, title=""):
    """Print summary stats for a list of (is_win, mfe, mae, tf, row) tuples."""
    if not labeled_rows:
        print("  (no rows)")
        return

    stats_by_tf = defaultdict(lambda: {"total": 0, "win": 0, "loss": 0, "mfe": 0.0, "mae": 0.0})
    stats_by_type = defaultdict(lambda: {"total": 0, "win": 0, "loss": 0})
    stats_by_sym = defaultdict(lambda: {"total": 0, "win": 0, "loss": 0})

    total_w = 0
    total_l = 0

    for is_win, mfe, mae, tf, row in labeled_rows:
        ctype = row.get("candidate_type", "").strip().lower()
        sym = row.get("symbol", "").strip()

        stats_by_tf[tf]["total"] += 1
        stats_by_tf[tf]["mfe"] += mfe
        stats_by_tf[tf]["mae"] += mae
        if is_win:
            stats_by_tf[tf]["win"] += 1
            stats_by_type[ctype]["win"] += 1
            stats_by_sym[sym]["win"] += 1
            total_w += 1
        else:
            stats_by_tf[tf]["loss"] += 1
            stats_by_type[ctype]["loss"] += 1
            stats_by_sym[sym]["loss"] += 1
            total_l += 1
        stats_by_type[ctype]["total"] += 1
        stats_by_sym[sym]["total"] += 1

    total = total_w + total_l
    wr = total_w / total * 100 if total > 0 else 0

    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")

    print(f"  Total: {total}  Wins: {total_w}  Losses: {total_l}  WR: {wr:.1f}%")

    print(f"\n  By TF:")
    for tf in ["15m", "1h", "4h", "1d"]:
        s = stats_by_tf[tf]
        if s["total"] == 0:
            continue
        t_wr = s["win"] / s["total"] * 100
        avg_mfe = s["mfe"] / s["total"]
        avg_mae = s["mae"] / s["total"]
        cfg = TF_CONFIG[tf]
        print(f"    {tf}: {s['win']}W/{s['loss']}L ({t_wr:.1f}%) "
              f"MFE={avg_mfe:.3f}% MAE={avg_mae:.3f}% H={cfg['horizon']}")

    print(f"\n  By Symbol:")
    for sym in sorted(stats_by_sym.keys()):
        s = stats_by_sym[sym]
        if s["total"] == 0:
            continue
        s_wr = s["win"] / s["total"] * 100
        print(f"    {sym}: {s['win']}W/{s['loss']}L ({s_wr:.1f}%)")

    print(f"\n  By Type:")
    for ctype in sorted(stats_by_type.keys()):
        s = stats_by_type[ctype]
        if s["total"] == 0:
            continue
        s_wr = s["win"] / s["total"] * 100
        print(f"    {ctype}: {s['win']}W/{s['loss']}L ({s_wr:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Recalculate labels v4 with OOS test")
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
                        help="Multiply all thresholds by this factor")
    parser.add_argument("--diag-only", action="store_true",
                        help="Only show context_json diagnostics")
    parser.add_argument("--oos-test", action="store_true",
                        help="Out-of-sample test: 70/30 split with 3 random seeds")
    parser.add_argument("--oos-seed", type=int, default=None,
                        help="Fixed seed for reproducible OOS split")
    parser.add_argument("--oos-ratio", type=float, default=0.3,
                        help="Test set ratio (default: 0.3 = 30%)")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        alt_path = os.path.join(script_dir, "..", input_path)
        if os.path.exists(alt_path):
            input_path = alt_path
        else:
            print(f"ERROR: Cannot find input file: {input_path}")
            sys.exit(1)

    print(f"Reading: {input_path}")

    rows = []
    with open(input_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    print(f"Total rows in CSV: {len(rows)}")
    mode = args.mode
    print(f"Mode: {mode}")

    # ─── DIAGNOSTICS ONLY ───────────────────────────────────────────────
    if args.diag_only:
        print("\n" + "=" * 70)
        print("CONTEXT_JSON DIAGNOSTICS")
        print("=" * 70)

        offset_dist = defaultdict(int)
        bar_count_dist = defaultdict(int)
        has_forward_count = 0
        no_forward_count = 0
        no_context_count = 0

        for i, row in enumerate(rows):
            cj = row.get("context_json", "")
            bars = parse_context_json(cj)
            if not bars:
                no_context_count += 1
                continue
            has_fwd, max_off, min_off, n_bars, has_entry = classify_row(bars)
            bar_count_dist[n_bars] += 1
            if has_fwd:
                has_forward_count += 1
            else:
                no_forward_count += 1

        print(f"\nRows with valid context_json: {has_forward_count + no_forward_count}")
        print(f"Rows with NO/empty context:   {no_context_count}")
        print(f"Rows WITH forward bars:      {has_forward_count} ({has_forward_count/(has_forward_count+no_forward_count)*100:.1f}%)")
        print(f"Rows WITHOUT forward bars:   {no_forward_count} ({no_forward_count/(has_forward_count+no_forward_count)*100:.1f}%)")

        print(f"\nBar count distribution:")
        for bc in sorted(bar_count_dist.keys()):
            print(f"  {bc} bars: {bar_count_dist[bc]} rows")

        print("\n" + "=" * 70)
        print("[DIAG ONLY] Done.")
        return

    # ─── LABEL ALL ROWS ────────────────────────────────────────────────
    labeled_all = []  # (is_win, mfe, mae, tf, row)
    failed = 0
    filtered = 0

    for row in rows:
        result = label_one_row(row, mode, args.threshold_factor)
        if result is None:
            if args.filter_forward:
                cj_raw = row.get("context_json", "")
                bars = parse_context_json(cj_raw)
                has_fwd, _, _, _, _ = classify_row(bars)
                if not has_fwd:
                    filtered += 1
                else:
                    failed += 1
            else:
                failed += 1
            continue

        is_win, mfe, mae, tf = result
        labeled_all.append((is_win, mfe, mae, tf, row))

    print(f"Filtered (no forward): {filtered}")
    print(f"Failed: {failed}")
    print(f"Labeled: {len(labeled_all)}")

    # ─── OOS TEST ───────────────────────────────────────────────────────
    if args.oos_test:
        test_ratio = args.oos_ratio
        seeds = [args.oos_seed] if args.oos_seed is not None else [42, 123, 999]
        n = len(labeled_all)

        print(f"\n{'#'*60}")
        print(f"  OUT-OF-SAMPLE TEST: {int((1-test_ratio)*100)}% TRAIN / {int(test_ratio*100)}% TEST")
        print(f"  Labeled rows: {n}")
        print(f"  Seeds: {seeds}")
        print(f"{'#'*60}")

        all_train_wrs = []
        all_test_wrs = []

        for seed in seeds:
            rng = random.Random(seed)
            shuffled = list(range(n))
            rng.shuffle(shuffled)

            test_size = max(1, int(n * test_ratio))
            test_indices = set(shuffled[:test_size])
            train_indices = set(shuffled[test_size:])

            train_rows = [labeled_all[i] for i in range(n) if i in train_indices]
            test_rows = [labeled_all[i] for i in range(n) if i in test_indices]

            train_wins = sum(1 for r in train_rows if r[0])
            test_wins = sum(1 for r in test_rows if r[0])

            train_wr = train_wins / len(train_rows) * 100 if train_rows else 0
            test_wr = test_wins / len(test_rows) * 100 if test_rows else 0

            all_train_wrs.append(train_wr)
            all_test_wrs.append(test_wr)

            print(f"\n{'─'*50}")
            print(f"  Seed {seed}:")
            print(f"    Train: {len(train_rows)} rows, WR={train_wr:.1f}%")
            print(f"    Test:  {len(test_rows)} rows, WR={test_wr:.1f}%")
            print(f"    Gap:   {train_wr - test_wr:+.1f}pp")
            print(f"    Ratio: {test_wr/max(train_wr,0.01)*100:.0f}% (test WR as % of train WR)")

            # Per-TF breakdown for test set
            tf_test = defaultdict(lambda: {"w": 0, "l": 0})
            for is_win, mfe, mae, tf, row in test_rows:
                if is_win:
                    tf_test[tf]["w"] += 1
                else:
                    tf_test[tf]["l"] += 1
            print(f"    Test per-TF:")
            for tf in ["15m", "1h", "4h", "1d"]:
                s = tf_test[tf]
                t = s["w"] + s["l"]
                if t == 0:
                    continue
                wr = s["w"] / t * 100
                print(f"      {tf}: {s['w']}W/{s['l']}L ({wr:.1f}%)")

        # Summary across seeds
        print(f"\n{'='*60}")
        print(f"  OOS SUMMARY (across {len(seeds)} seeds)")
        print(f"{'='*60}")
        avg_train = sum(all_train_wrs) / len(all_train_wrs)
        avg_test = sum(all_test_wrs) / len(all_test_wrs)
        std_test = (sum((x - avg_test)**2 for x in all_test_wrs) / len(all_test_wrs)) ** 0.5

        print(f"  Avg Train WR:  {avg_train:.1f}%")
        print(f"  Avg Test WR:   {avg_test:.1f}% (std={std_test:.1f}%)")
        print(f"  Avg Gap:       {avg_train - avg_test:+.1f}pp")

        # Robustness verdict
        min_test = min(all_test_wrs)
        max_gap = max(train - test for train, test in zip(all_train_wrs, all_test_wrs))

        print(f"\n  Robustness checks:")
        if min_test >= 70:
            print(f"    Min test WR: {min_test:.1f}% >= 70% -> STRONG")
        elif min_test >= 60:
            print(f"    Min test WR: {min_test:.1f}% >= 60% -> MODERATE")
        else:
            print(f"    Min test WR: {min_test:.1f}% < 60% -> WEAK (possible overfit)")

        if max_gap <= 15:
            print(f"    Max train-test gap: {max_gap:.1f}pp <= 15pp -> STABLE")
        elif max_gap <= 25:
            print(f"    Max train-test gap: {max_gap:.1f}pp <= 25pp -> ACCEPTABLE")
        else:
            print(f"    Max train-test gap: {max_gap:.1f}pp > 25pp -> UNSTABLE (overfit likely)")

        # Overall verdict
        is_robust = min_test >= 60 and max_gap <= 20
        print(f"\n  VERDICT: {'ROBUST - ready for ML training' if is_robust else 'NOT ROBUST - need more data or tuning'}")
        print(f"{'='*60}")

        # Also show full dataset stats
        print_stats(labeled_all, "FULL DATASET (all labeled rows)")

        if args.dry_run:
            print("\n[DRY RUN + OOS] No changes written.")
        return

    # ─── STANDARD REPORT (no OOS) ────────────────────────────────────────
    print_stats(labeled_all, "LABELING RESULTS")

    # ─── WRITE ─────────────────────────────────────────────────────────
    if not args.dry_run:
        # Update rows with label data
        for is_win, mfe, mae, tf, row in labeled_all:
            row["label_value"] = str(1 if is_win else 0)
            row["mfe_pct"] = f"{mfe:.4f}"
            row["mae_pct"] = f"{mae:.4f}"
            row["label_horizon"] = str(TF_CONFIG.get(tf, {}).get("horizon", 6))
            row["label_mode"] = mode

        output_path = args.output or input_path
        new_cols = ["mfe_pct", "mae_pct", "label_horizon", "label_mode"]
        for col in new_cols:
            if col not in fieldnames:
                fieldnames.append(col)

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWritten to: {output_path}")
    else:
        print("\n[DRY RUN] No changes written.")


if __name__ == "__main__":
    main()
