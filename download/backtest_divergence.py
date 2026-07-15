#!/usr/bin/env python3
"""
backtest_divergence.py - backtest A/D divergence signals from unified_dataset_v11.csv

Logic:
  - Parse context_json (bars offset -10..+10) for each candidate
  - offset=0 is the signal bar (curr_price)
  - At horizons +1, +3, +5, +10 bars compare close price with offset=0
  - Bull: win if close(horizon) > close(0), P&L positive when price rises
  - Bear: win if close(horizon) < close(0), P&L sign FLIPPED (positive when price drops)
  - Output: winrate, avg_return %, profit factor, trade count
  - Cross-tabs: symbol, tf_profile, quality, type, vol_ratio, strength

Usage:
  cd D:\LOCAL_AI_ENGINE
  python tools/backtest_divergence.py
  python tools/backtest_divergence.py --csv results/unified_dataset_v11.csv --horizons 1 3 5 10
  python tools/backtest_divergence.py --min-score 40 --min-bars 5
"""

import argparse
import csv
import json
import sys
import os
from collections import defaultdict
from pathlib import Path
from typing import Optional


def parse_args():
    p = argparse.ArgumentParser(description="Backtest A/D divergence signals from unified_dataset_v11.csv")
    p.add_argument("--csv", default=os.path.join("results", "unified_dataset_v11.csv"),
                   help="Path to unified dataset CSV (default: results/unified_dataset_v11.csv)")
    p.add_argument("--horizons", nargs="+", type=int, default=[1, 3, 5, 10],
                   help="Bar horizons to evaluate (default: 1 3 5 10)")
    p.add_argument("--min-score", type=float, default=0.0,
                   help="Minimum candidate_score filter (default: 0 = no filter)")
    p.add_argument("--min-bars", type=int, default=0,
                   help="Minimum available bars after signal (default: 0 = use whatever exists)")
    p.add_argument("--output", default=None,
                   help="Output report path (default: print to console)")
    p.add_argument("--json-output", default=None,
                   help="Output results as JSON for downstream use")
    return p.parse_args()


def safe_float(val, default=None):
    """Convert value to float, return default on failure."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", ".")
    if not s or s.lower() in ("nan", "none", "null", "n/a", "н/д", ""):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def parse_context_json(ctx_str):
    """Parse context_json column into list of bar dicts sorted by offset."""
    if not ctx_str or ctx_str.strip().lower() in ("nan", "none", "null", ""):
        return []
    try:
        bars = json.loads(ctx_str)
        if isinstance(bars, list):
            return sorted(bars, key=lambda b: int(b.get("offset", 0)))
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def extract_horizon_outcome(bars, horizon, signal_close, candidate_type):
    """
    Determine win/loss at a given horizon after the signal bar.

    CRITICAL: return_pct is P&L (profit & loss), NOT raw price change.
    - For bull: P&L positive when price goes UP (long trade profit)
    - For bear: P&L positive when price goes DOWN (short trade profit, sign FLIPPED)
    """
    if signal_close is None or signal_close <= 0:
        return {"available": False, "signal_close": signal_close}

    # Find bar at offset = horizon (e.g., offset=5 means 5 bars after signal)
    horizon_bar = None
    for b in bars:
        off = int(b.get("offset", 0))
        if off == horizon:
            horizon_bar = b
            break

    if horizon_bar is None:
        return {"available": False, "signal_close": signal_close}

    h_close = safe_float(horizon_bar.get("close"))
    if h_close is None:
        return {"available": False, "signal_close": signal_close}

    # Raw price change (positive = price went up)
    raw_return_pct = ((h_close - signal_close) / signal_close) * 100.0

    # Determine win condition and P&L based on candidate type
    ct = str(candidate_type).strip().lower() if candidate_type else ""
    if ct == "bull":
        win = h_close > signal_close
        # Long trade: profit when price rises
        pnl_pct = raw_return_pct
    elif ct == "bear":
        win = h_close < signal_close
        # Short trade: FLIP sign - profit when price drops
        pnl_pct = -raw_return_pct
    else:
        return {"available": False, "signal_close": signal_close}

    bps = pnl_pct * 100.0  # basis points (P&L)

    return {
        "available": True,
        "signal_close": signal_close,
        "horizon_close": h_close,
        "return_pct": pnl_pct,
        "raw_return_pct": raw_return_pct,
        "bps": bps,
        "win": win,
    }


def compute_stats(results):
    """Compute aggregate stats from list of win/loss outcomes."""
    if not results:
        return {
            "n": 0, "wins": 0, "losses": 0,
            "winrate": 0.0, "avg_return_pct": 0.0,
            "total_return_pct": 0.0, "profit_factor": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "max_win_pct": 0.0, "max_loss_pct": 0.0,
        }

    wins = [r for r in results if r["win"]]
    losses = [r for r in results if not r["win"]]

    n = len(results)
    n_wins = len(wins)
    n_losses = len(losses)

    winrate = (n_wins / n * 100.0) if n > 0 else 0.0

    # P&L returns (already sign-corrected: positive = profit)
    returns = [r["return_pct"] for r in results]
    avg_ret = sum(returns) / n if n > 0 else 0.0
    total_ret = sum(returns)

    avg_win = (sum(r["return_pct"] for r in wins) / n_wins) if n_wins > 0 else 0.0
    avg_loss = (sum(r["return_pct"] for r in losses) / n_losses) if n_losses > 0 else 0.0

    max_win = max(r["return_pct"] for r in wins) if wins else 0.0
    max_loss = min(r["return_pct"] for r in losses) if losses else 0.0

    # Profit factor: sum of winning P&L / abs(sum of losing P&L)
    gross_profit = sum(r["return_pct"] for r in wins) if wins else 0.0
    gross_loss = abs(sum(r["return_pct"] for r in losses)) if losses else 0.0
    if gross_loss > 0:
        pf = gross_profit / gross_loss
    elif gross_profit > 0:
        pf = float("inf")
    else:
        pf = 0.0

    return {
        "n": n,
        "wins": n_wins,
        "losses": n_losses,
        "winrate": round(winrate, 2),
        "avg_return_pct": round(avg_ret, 4),
        "total_return_pct": round(total_ret, 4),
        "profit_factor": round(pf, 2) if pf != float("inf") else "INF",
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "max_win_pct": round(max_win, 4),
        "max_loss_pct": round(max_loss, 4),
    }


def vol_ratio_bucket(vol_ratio):
    """Classify vol_ratio into buckets for cross-tab."""
    if vol_ratio is None:
        return "N/A"
    if vol_ratio < 0.8:
        return "<0.8"
    elif vol_ratio < 1.2:
        return "0.8-1.2"
    elif vol_ratio < 2.0:
        return "1.2-2.0"
    elif vol_ratio < 3.0:
        return "2.0-3.0"
    else:
        return "3.0+"


def score_bucket(score):
    """Classify candidate_score into quality buckets."""
    if score is None:
        return "N/A"
    if score < 30:
        return "<30"
    elif score < 50:
        return "30-50"
    elif score < 70:
        return "50-70"
    else:
        return "70+"


def strength_bucket(strength):
    """Classify candidate_strength into buckets."""
    if strength is None:
        return "N/A"
    if strength < 0.3:
        return "<0.3"
    elif strength < 0.5:
        return "0.3-0.5"
    elif strength < 0.7:
        return "0.5-0.7"
    else:
        return "0.7+"


def format_stats_table(title, data, indent=2):
    """Format stats dict as readable text block."""
    pad = " " * indent
    lines = [f"{pad}=== {title} ==="]
    lines.append(f"{pad}  Trades: {data['n']} | Wins: {data['wins']} | Losses: {data['losses']}")
    lines.append(f"{pad}  Winrate: {data['winrate']}%")
    lines.append(f"{pad}  Avg P&L: {data['avg_return_pct']}%")
    lines.append(f"{pad}  Total P&L: {data['total_return_pct']}%")
    lines.append(f"{pad}  Profit Factor: {data['profit_factor']}")
    lines.append(f"{pad}  Avg Win: {data['avg_win_pct']}% | Avg Loss: {data['avg_loss_pct']}%")
    lines.append(f"{pad}  Max Win: {data['max_win_pct']}% | Max Loss: {data['max_loss_pct']}%")
    return "\n".join(lines)


def cross_tab(results, key_fn, label):
    """Group results by key_fn and print per-group stats."""
    groups = defaultdict(list)
    for r in results:
        key = key_fn(r)
        groups[key].append(r)

    lines = [f"\n--- Cross-tab: {label} ---"]
    for key in sorted(groups.keys()):
        stats = compute_stats(groups[key])
        lines.append(format_stats_table(key, stats, indent=4))
    return "\n".join(lines)


def run_backtest(args):
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}")
        sys.exit(1)

    print(f"Reading: {csv_path}")

    rows = []
    # utf-8-sig handles BOM from PowerShell Export-Csv
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Strip BOM artifacts and whitespace from all keys
            clean_row = {k.strip().lstrip("\ufeff"): v for k, v in row.items()}
            rows.append(clean_row)

    print(f"CSV rows: {len(rows)}")

    # Show column names for debugging
    if rows:
        print(f"Columns: {list(rows[0].keys())[:10]}...")

    # Filter by min score
    if args.min_score > 0:
        before = len(rows)
        rows = [r for r in rows if safe_float(r.get("candidate_score"), 0) >= args.min_score]
        print(f"Filter score>={args.min_score}: {before} -> {len(rows)}")

    horizons = sorted(args.horizons)
    print(f"Horizons: {horizons}")

    # Process each row
    total_candidates = 0
    skipped_no_context = 0
    skipped_no_signal = 0
    skipped_unknown_type = 0

    all_results = {h: [] for h in horizons}

    for idx, row in enumerate(rows):
        symbol = row.get("symbol", "?")
        if not symbol or symbol.strip() == "":
            symbol = "?"
        symbol = symbol.strip()

        tf = row.get("tf_profile", row.get("timeframe", "?"))
        if not tf:
            tf = "?"
        tf = str(tf).strip()

        cand_type = str(row.get("candidate_type", "")).strip().lower()
        cand_score = safe_float(row.get("candidate_score"))
        cand_quality = str(row.get("candidate_quality", "")).strip() or "N/A"
        cand_strength = safe_float(row.get("candidate_strength"))
        vol_ratio = safe_float(row.get("vol_ratio"))
        hidden_div = str(row.get("hidden_divergence", "")).strip()
        cmf_score = safe_float(row.get("cmf_score"))
        flow_slope = safe_float(row.get("flow_slope"))

        # Parse context_json
        bars = parse_context_json(row.get("context_json", ""))

        if not bars:
            skipped_no_context += 1
            continue

        # Find signal bar (offset=0)
        signal_bar = None
        for b in bars:
            if int(b.get("offset", 0)) == 0:
                signal_bar = b
                break

        if signal_bar is None:
            # Fallback: closest to offset=0
            by_offset = [(abs(int(b.get("offset", 999))), b) for b in bars]
            by_offset.sort()
            signal_bar = by_offset[0][1] if by_offset else None

        if signal_bar is None:
            skipped_no_signal += 1
            continue

        signal_close = safe_float(signal_bar.get("close"))
        if signal_close is None or signal_close <= 0:
            skipped_no_signal += 1
            continue

        if cand_type not in ("bull", "bear"):
            skipped_unknown_type += 1
            continue

        total_candidates += 1

        # Check available bars after signal
        max_available = max((int(b.get("offset", 0)) for b in bars), default=0)

        meta = {
            "symbol": symbol,
            "tf": tf,
            "cand_type": cand_type,
            "cand_score": cand_score,
            "cand_quality": cand_quality,
            "cand_strength": cand_strength,
            "vol_ratio": vol_ratio,
            "hidden_divergence": hidden_div,
            "cmf_score": cmf_score,
            "flow_slope": flow_slope,
            "candidate_time": row.get("candidate_time", ""),
            "max_available_offset": max_available,
        }

        for h in horizons:
            if h > max_available:
                continue
            if h == 0:
                continue

            outcome = extract_horizon_outcome(bars, h, signal_close, cand_type)
            if outcome.get("available"):
                outcome.update(meta)
                all_results[h].append(outcome)

    # Print summary
    print(f"\n{'='*60}")
    print(f"BACKTEST A/D DIVERGENCES")
    print(f"{'='*60}")
    print(f"Total CSV rows: {len(rows)}")
    print(f"Processed candidates: {total_candidates}")
    print(f"Skipped (no context): {skipped_no_context}")
    print(f"Skipped (no signal bar): {skipped_no_signal}")
    print(f"Skipped (unknown type): {skipped_unknown_type}")

    output_lines = []
    json_results = {}

    for h in horizons:
        results = all_results[h]
        stats = compute_stats(results)

        section = f"\n{'='*60}"
        section += f"\nHORIZON: +{h} bar(s)"
        section += f"\n{'='*60}"
        section += format_stats_table(f"Overall (h={h})", stats)

        # Cross-tabs
        section += cross_tab(results, lambda r: r.get("symbol", "?"), "Symbol")
        section += cross_tab(results, lambda r: r.get("tf", "?"), "Timeframe")
        section += cross_tab(results, lambda r: r.get("cand_type", "?"), "Type (bull/bear)")
        section += cross_tab(results, lambda r: r.get("cand_quality", "N/A"), "Quality")
        section += cross_tab(results, lambda r: score_bucket(r.get("cand_score")), "Score Bucket")
        section += cross_tab(results, lambda r: vol_ratio_bucket(r.get("vol_ratio")), "Vol Ratio")
        section += cross_tab(results, lambda r: strength_bucket(r.get("cand_strength")), "Strength Bucket")

        # Combo: symbol + type
        def sym_type_key(r):
            return f"{r.get('symbol', '?')} | {r.get('cand_type', '?')}"

        section += cross_tab(results, sym_type_key, "Symbol + Type")

        # Combo: type + quality
        def type_quality_key(r):
            ct = r.get("cand_type", "?")
            q = r.get("cand_quality", "N/A")
            return f"{ct} | {q}"

        section += cross_tab(results, type_quality_key, "Type + Quality")

        output_lines.append(section)
        json_results[str(h)] = {
            "stats": stats,
            "n_results": len(results),
        }

    full_report = "\n".join(output_lines)
    print(full_report)

    # Signal direction consistency check
    print(f"\n{'='*60}")
    print(f"DIRECTION CONSISTENCY")
    print(f"{'='*60}")
    for h in horizons:
        results = all_results[h]
        bull_results = [r for r in results if r.get("cand_type") == "bull"]
        bear_results = [r for r in results if r.get("cand_type") == "bear"]
        if bull_results:
            bull_stats = compute_stats(bull_results)
            print(f"  H=+{h} BULL: WR={bull_stats['winrate']}% (n={bull_stats['n']}) "
                  f"Avg P&L={bull_stats['avg_return_pct']}% PF={bull_stats['profit_factor']}")
        if bear_results:
            bear_stats = compute_stats(bear_results)
            print(f"  H=+{h} BEAR: WR={bear_stats['winrate']}% (n={bear_stats['n']}) "
                  f"Avg P&L={bear_stats['avg_return_pct']}% PF={bear_stats['profit_factor']}")

    # Save output
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_report)
        print(f"\n[OK] Report saved: {out_path}")

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_results, f, indent=2, ensure_ascii=False)
        print(f"[OK] JSON saved: {json_path}")

    return json_results


if __name__ == "__main__":
    args = parse_args()
    run_backtest(args)
