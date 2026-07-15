#!/usr/bin/env python3
"""
backtest_stats_generator.py v2
================================
Generates comprehensive backtest statistics from unified_dataset CSV.
Uses pre-computed labels from recalculate_labels.py (MFE/MAE-based).
Filters out rows without forward bars (max_offset==0).

Output: results/backtest_stats.json
"""

import json
import csv
import sys
import os
from collections import defaultdict
from datetime import datetime


TF_CONFIG = {
    "15m": {"horizon": 10, "threshold_pct": 0.15},
    "1h":  {"horizon": 8,  "threshold_pct": 0.30},
    "4h":  {"horizon": 6,  "threshold_pct": 0.50},
    "1d":  {"horizon": 5,  "threshold_pct": 1.00},
}


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


def classify_row(bars):
    if not bars:
        return False, 0, 0, 0
    offsets = [b.get("offset", 0) for b in bars]
    return max(offsets) > 0, max(offsets), min(offsets), len(bars)


def main():
    csv_path = "results/unified_dataset_v11.csv"
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found")
        sys.exit(1)

    print(f"Reading: {csv_path}")

    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Total rows: {len(rows)}")

    # ─── Classify & filter ────────────────────────────────────────────
    labeled_rows = []
    skipped_no_forward = 0
    skipped_no_label = 0

    for row in rows:
        # Check if row has forward bars
        cj_raw = row.get("context_json", "")
        bars = parse_context_json(cj_raw)
        has_fwd, max_off, min_off, n_bars = classify_row(bars)

        if not has_fwd:
            skipped_no_forward += 1
            continue

        # Check if row was labeled by recalculate_labels.py
        mfe_raw = row.get("mfe_pct", "").strip()
        label_raw = row.get("label_value", "").strip()

        if not mfe_raw or mfe_raw == "" or label_raw == "":
            skipped_no_label += 1
            continue

        labeled_rows.append(row)

    print(f"Skipped (no forward bars): {skipped_no_forward}")
    print(f"Skipped (no label computed): {skipped_no_label}")
    print(f"Labeled rows with forward data: {len(labeled_rows)}")

    if not labeled_rows:
        print("ERROR: No labeled rows to analyze!")
        sys.exit(1)

    # ─── Aggregate stats ───────────────────────────────────────────────
    # Overall
    total = len(labeled_rows)
    wins = 0
    losses = 0
    total_pnl = 0.0
    win_pnl = 0.0
    loss_pnl = 0.0
    mfe_sum = 0.0
    mae_sum = 0.0

    # Per-TF
    by_tf = defaultdict(lambda: {
        "total": 0, "wins": 0, "losses": 0,
        "mfe_sum": 0.0, "mae_sum": 0.0, "pnl_sum": 0.0,
        "symbols": set(), "win_streak": 0, "loss_streak": 0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "current_streak_type": None, "current_streak_len": 0,
    })

    # Per-symbol
    by_sym = defaultdict(lambda: {
        "total": 0, "wins": 0, "losses": 0,
        "mfe_sum": 0.0, "mae_sum": 0.0, "pnl_sum": 0.0,
    })

    # Per-type (bull/bear)
    by_type = defaultdict(lambda: {
        "total": 0, "wins": 0, "losses": 0,
        "mfe_sum": 0.0, "mae_sum": 0.0, "pnl_sum": 0.0,
    })

    # Per-TF x type
    by_tf_type = defaultdict(lambda: {
        "total": 0, "wins": 0, "losses": 0,
    })

    # Per-symbol x type
    by_sym_type = defaultdict(lambda: {
        "total": 0, "wins": 0, "losses": 0,
    })

    # Consecutive results (for streak calculation)
    # Equity curve
    equity_curve = []
    running_pnl = 0.0

    for i, row in enumerate(labeled_rows):
        tf = row.get("tf_profile", "").strip()
        sym = row.get("symbol", "").strip()
        ctype = row.get("candidate_type", "").strip().lower()
        mfe = float(row.get("mfe_pct", "0"))
        mae = float(row.get("mae_pct", "0"))
        label = int(row.get("label_value", "0"))

        # Simplified PnL: label=1 → +avg_mfe, label=0 → -avg_threshold
        tf_key = tf if tf in TF_CONFIG else "15m"
        thr = TF_CONFIG[tf_key]["threshold_pct"]

        if label == 1:
            pnl = mfe  # simplified: profit = MFE achieved
            wins += 1
            is_win = True
        else:
            pnl = -thr  # simplified: loss = threshold size
            losses += 1
            is_win = False

        total_pnl += pnl
        running_pnl += pnl
        equity_curve.append({
            "idx": i,
            "pnl": round(running_pnl, 4),
            "tf": tf,
            "sym": sym,
            "type": ctype,
            "mfe": round(mfe, 4),
            "mae": round(mae, 4),
            "label": label,
        })

        mfe_sum += mfe
        mae_sum += mae

        if is_win:
            win_pnl += pnl
        else:
            loss_pnl += abs(pnl)

        # Per-TF
        by_tf[tf]["total"] += 1
        by_tf[tf]["mfe_sum"] += mfe
        by_tf[tf]["mae_sum"] += mae
        by_tf[tf]["pnl_sum"] += pnl
        by_tf[tf]["symbols"].add(sym)

        # Streak tracking per TF
        if is_win:
            by_tf[tf]["wins"] += 1
            if by_tf[tf]["current_streak_type"] == "win":
                by_tf[tf]["current_streak_len"] += 1
            else:
                by_tf[tf]["current_streak_type"] = "win"
                by_tf[tf]["current_streak_len"] = 1
            by_tf[tf]["max_win_streak"] = max(
                by_tf[tf]["max_win_streak"],
                by_tf[tf]["current_streak_len"]
            )
        else:
            by_tf[tf]["losses"] += 1
            if by_tf[tf]["current_streak_type"] == "loss":
                by_tf[tf]["current_streak_len"] += 1
            else:
                by_tf[tf]["current_streak_type"] = "loss"
                by_tf[tf]["current_streak_len"] = 1
            by_tf[tf]["max_loss_streak"] = max(
                by_tf[tf]["max_loss_streak"],
                by_tf[tf]["current_streak_len"]
            )

        # Per-symbol
        by_sym[sym]["total"] += 1
        by_sym[sym]["mfe_sum"] += mfe
        by_sym[sym]["mae_sum"] += mae
        by_sym[sym]["pnl_sum"] += pnl
        if is_win:
            by_sym[sym]["wins"] += 1
        else:
            by_sym[sym]["losses"] += 1

        # Per-type
        by_type[ctype]["total"] += 1
        by_type[ctype]["mfe_sum"] += mfe
        by_type[ctype]["mae_sum"] += mae
        by_type[ctype]["pnl_sum"] += pnl
        if is_win:
            by_type[ctype]["wins"] += 1
        else:
            by_type[ctype]["losses"] += 1

        # Per-TF x type
        by_tf_type[f"{tf}_{ctype}"]["total"] += 1
        if is_win:
            by_tf_type[f"{tf}_{ctype}"]["wins"] += 1
        else:
            by_tf_type[f"{tf}_{ctype}"]["losses"] += 1

        # Per-symbol x type
        by_sym_type[f"{sym}_{ctype}"]["total"] += 1
        if is_win:
            by_sym_type[f"{sym}_{ctype}"]["wins"] += 1
        else:
            by_sym_type[f"{sym}_{ctype}"]["losses"] += 1

    # ─── Compute derived metrics ────────────────────────────────────────
    wr = wins / total * 100 if total > 0 else 0
    avg_win = win_pnl / wins if wins > 0 else 0
    avg_loss = loss_pnl / losses if losses > 0 else 0
    profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else float("inf")
    avg_mfe = mfe_sum / total if total > 0 else 0
    avg_mae = mae_sum / total if total > 0 else 0
    edge_ratio = avg_mfe / avg_mae if avg_mae > 0 else 0
    expectancy = (wr / 100 * avg_win) - ((1 - wr / 100) * avg_loss)

    # Max drawdown from equity curve
    max_dd = 0.0
    peak = 0.0
    for pt in equity_curve:
        if pt["pnl"] > peak:
            peak = pt["pnl"]
        dd = peak - pt["pnl"]
        if dd > max_dd:
            max_dd = dd

    # Build TF-level summaries
    tf_summary = {}
    for tf in ["15m", "1h", "4h", "1d"]:
        s = by_tf[tf]
        if s["total"] == 0:
            continue
        t_wr = s["wins"] / s["total"] * 100
        t_avg_mfe = s["mfe_sum"] / s["total"]
        t_avg_mae = s["mae_sum"] / s["total"]
        t_ratio = t_avg_mfe / t_avg_mae if t_avg_mae > 0 else 0
        t_pf = (s["pnl_sum"] > 0) else None  # simplified

        tf_summary[tf] = {
            "total": s["total"],
            "wins": s["wins"],
            "losses": s["losses"],
            "wr_pct": round(t_wr, 1),
            "avg_mfe_pct": round(t_avg_mfe, 3),
            "avg_mae_pct": round(t_avg_mae, 3),
            "edge_ratio": round(t_ratio, 2),
            "edge": "POSITIVE" if t_ratio > 1.0 else "NEGATIVE",
            "max_win_streak": s["max_win_streak"],
            "max_loss_streak": s["max_loss_streak"],
            "symbols": sorted(s["symbols"]),
            "threshold_pct": TF_CONFIG.get(tf, {}).get("threshold_pct", 0.3),
            "horizon": TF_CONFIG.get(tf, {}).get("horizon", 6),
        }

    # Build symbol summary
    sym_summary = {}
    for sym in sorted(by_sym.keys()):
        s = by_sym[sym]
        sym_summary[sym] = {
            "total": s["total"],
            "wins": s["wins"],
            "losses": s["losses"],
            "wr_pct": round(s["wins"] / s["total"] * 100, 1) if s["total"] > 0 else 0,
            "avg_mfe_pct": round(s["mfe_sum"] / s["total"], 3) if s["total"] > 0 else 0,
        }

    # Build type summary
    type_summary = {}
    for ctype in sorted(by_type.keys()):
        s = by_type[ctype]
        type_summary[ctype] = {
            "total": s["total"],
            "wins": s["wins"],
            "losses": s["losses"],
            "wr_pct": round(s["wins"] / s["total"] * 100, 1) if s["total"] > 0 else 0,
            "avg_mfe_pct": round(s["mfe_sum"] / s["total"], 3) if s["total"] > 0 else 0,
        }

    # Build TF x type summary
    tf_type_summary = {}
    for key in sorted(by_tf_type.keys()):
        s = by_tf_type[key]
        tf_type_summary[key] = {
            "total": s["total"],
            "wins": s["wins"],
            "losses": s["losses"],
            "wr_pct": round(s["wins"] / s["total"] * 100, 1) if s["total"] > 0 else 0,
        }

    # ─── Assemble final stats ──────────────────────────────────────────
    stats = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "label_mode": "threshold",
        "filter_forward": True,
        "source_file": "results/unified_dataset_v11.csv",
        "total_rows_in_csv": len(rows),
        "rows_without_forward_bars": skipped_no_forward,
        "rows_without_labels": skipped_no_label,
        "rows_analyzed": total,
        "overall": {
            "wins": wins,
            "losses": losses,
            "wr_pct": round(wr, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "INF",
            "avg_win_pct": round(avg_win, 3),
            "avg_loss_pct": round(avg_loss, 3),
            "avg_mfe_pct": round(avg_mfe, 3),
            "avg_mae_pct": round(avg_mae, 3),
            "edge_ratio": round(edge_ratio, 2),
            "expectancy": round(expectancy, 4),
            "total_pnl": round(total_pnl, 3),
            "max_drawdown": round(max_dd, 3),
        },
        "by_timeframe": tf_summary,
        "by_symbol": sym_summary,
        "by_type": type_summary,
        "by_tf_x_type": tf_type_summary,
        "equity_curve": equity_curve,
    }

    # ─── Write output ──────────────────────────────────────────────────
    out_path = "results/backtest_stats.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(f"\nWritten: {out_path}")
    print(f"\n{'='*50}")
    print(f"OVERALL WR: {wr:.1f}% ({wins}W / {losses}L of {total} trades)")
    print(f"PROFIT FACTOR: {profit_factor:.2f}")
    print(f"AVG WIN: {avg_win:.3f}% | AVG LOSS: {avg_loss:.3f}%")
    print(f"EDGE RATIO: {edge_ratio:.2f}x")
    print(f"EXPECTANCY: {expectancy:.4f}")
    print(f"MAX DRAWDOWN: {max_dd:.3f}")
    print(f"{'='*50}")

    print("\nBy timeframe:")
    for tf in ["15m", "1h", "4h", "1d"]:
        if tf in tf_summary:
            s = tf_summary[tf]
            print(f"  {tf}: WR={s['wr_pct']:.1f}% ({s['wins']}W/{s['losses']}L) "
                  f"MFE={s['avg_mfe_pct']:.3f}% MAE={s['avg_mae_pct']:.3f}% "
                  f"Edge={s['edge_ratio']:.2f}x [{s['edge']}]")

    print("\nBy symbol:")
    for sym in sorted(sym_summary.keys()):
        s = sym_summary[sym]
        print(f"  {sym}: WR={s['wr_pct']:.1f}% ({s['wins']}W/{s['losses']}L)")

    print("\nBy type:")
    for ctype in sorted(type_summary.keys()):
        s = type_summary[ctype]
        print(f"  {ctype}: WR={s['wr_pct']:.1f}% ({s['wins']}W/{s['losses']}L)")


if __name__ == "__main__":
    main()
