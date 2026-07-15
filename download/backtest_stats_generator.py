#!/usr/bin/env python3
r"""
backtest_stats_generator.py — Парсит backtest_results.json + unified_dataset_v11.csv
и генерирует backtest_stats.json для интеграции в prompt бота.

Генерирует:
  results/backtest_stats.json — структура:
    {
      "generated_at": "2024-...",
      "total_candidates": 447,
      "horizons": {"3": {...}, "5": {...}, "10": {...}},
      "by_symbol": {
        "BTCUSDT": {
          "total": 95,
          "winrate": 87.5,
          "profit_factor": 19.4,
          "avg_pnl_pct": 1.46,
          "by_tf": { "1h": {...}, "4h": {...} },
          "by_type": { "bull": {...}, "bear": {...} }
        }
      },
      "by_tf": {...},
      "by_type": {...},
      "summary_for_prompt": "Текстовая строка для вставки в LLM prompt"
    }

Usage:
  cd D:\LOCAL_AI_ENGINE
  python tools/backtest_stats_generator.py
  python tools/backtest_stats_generator.py --json results/backtest_results.json --csv results/unified_dataset_v11.csv
"""

import argparse
import csv
import json
import sys
import os
from collections import defaultdict
from pathlib import Path
from datetime import datetime


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


def compute_stats(results):
    if not results:
        return {"n": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "avg_return_pct": 0.0, "total_return_pct": 0.0,
                "profit_factor": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0}

    wins = [r for r in results if r["win"]]
    losses = [r for r in results if not r["win"]]
    n = len(results)
    n_wins = len(wins)
    n_losses = len(losses)
    winrate = (n_wins / n * 100.0) if n > 0 else 0.0
    returns = [r["return_pct"] for r in results]
    avg_ret = sum(returns) / n if n > 0 else 0.0
    total_ret = sum(returns)
    avg_win = (sum(r["return_pct"] for r in wins) / n_wins) if n_wins > 0 else 0.0
    avg_loss = (sum(r["return_pct"] for r in losses) / n_losses) if n_losses > 0 else 0.0

    gross_profit = sum(r["return_pct"] for r in wins) if wins else 0.0
    gross_loss = abs(sum(r["return_pct"] for r in losses)) if losses else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    return {
        "n": n, "wins": n_wins, "losses": n_losses,
        "winrate": round(winrate, 1),
        "avg_return_pct": round(avg_ret, 3),
        "total_return_pct": round(total_ret, 2),
        "profit_factor": round(pf, 1),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
    }


def run_backtest_from_csv(csv_path, horizons=[3, 5, 10]):
    """Run quick backtest from CSV (like backtest_divergence.py but returns grouped data)."""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            clean_row = {k.strip().lstrip("\ufeff"): v for k, v in row.items()}
            rows.append(clean_row)

    all_results = {h: [] for h in horizons}

    for row in rows:
        symbol = str(row.get("symbol", "?")).strip()
        tf = str(row.get("tf_profile", row.get("timeframe", "?"))).strip()
        cand_type = str(row.get("candidate_type", "")).strip().lower()
        cand_score = safe_float(row.get("candidate_score"))
        cand_quality = str(row.get("candidate_quality", "")).strip() or "N/A"

        if cand_type not in ("bull", "bear"):
            continue

        bars = parse_context_json(row.get("context_json", ""))
        if not bars:
            continue

        signal_bar = None
        for b in bars:
            if int(b.get("offset", 0)) == 0:
                signal_bar = b
                break
        if signal_bar is None:
            continue

        signal_close = safe_float(signal_bar.get("close"))
        if signal_close is None or signal_close <= 0:
            continue

        for h in horizons:
            horizon_bar = None
            for b in bars:
                if int(b.get("offset", 0)) == h:
                    horizon_bar = b
                    break
            if horizon_bar is None:
                continue

            h_close = safe_float(horizon_bar.get("close"))
            if h_close is None:
                continue

            raw_return_pct = ((h_close - signal_close) / signal_close) * 100.0

            if cand_type == "bull":
                win = h_close > signal_close
                pnl_pct = raw_return_pct
            else:
                win = h_close < signal_close
                pnl_pct = -raw_return_pct

            all_results[h].append({
                "symbol": symbol,
                "tf": tf,
                "cand_type": cand_type,
                "cand_score": cand_score,
                "cand_quality": cand_quality,
                "win": win,
                "return_pct": pnl_pct,
                "raw_return_pct": raw_return_pct,
            })

    return all_results


def group_results(results, group_keys):
    """Group results by one or more keys."""
    groups = defaultdict(list)
    for r in results:
        key = tuple(r.get(k, "?") for k in group_keys)
        groups[key].append(r)
    return groups


def generate_stats(all_results, horizons):
    """Generate full stats structure from backtest results."""
    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "horizons": {},
    }

    # Pick primary horizon (default: 3)
    primary_h = 3
    if str(primary_h) not in all_results:
        primary_h = horizons[0] if horizons else 3

    primary_results = all_results.get(primary_h, [])
    primary_stats = compute_stats(primary_results)

    output["primary_horizon"] = primary_h
    output["total_candidates"] = primary_stats["n"]
    output["overall"] = primary_stats

    # By symbol
    sym_groups = group_results(primary_results, ["symbol"])
    by_symbol = {}
    for (sym,), results in sym_groups.items():
        stats = compute_stats(results)
        # By TF within symbol
        tf_groups = group_results(results, ["tf"])
        by_tf = {}
        for (tf,), tf_results in tf_groups.items():
            by_tf[tf] = compute_stats(tf_results)
        # By type within symbol
        type_groups = group_results(results, ["cand_type"])
        by_type = {}
        for (ct,), type_results in type_groups.items():
            by_type[ct] = compute_stats(type_results)

        by_symbol[sym] = {
            **stats,
            "by_tf": by_tf,
            "by_type": by_type,
        }
    output["by_symbol"] = by_symbol

    # By TF (global)
    tf_groups = group_results(primary_results, ["tf"])
    by_tf = {}
    for (tf,), results in tf_groups.items():
        by_tf[tf] = compute_stats(results)
    output["by_tf"] = by_tf

    # By type (global)
    type_groups = group_results(primary_results, ["cand_type"])
    by_type = {}
    for (ct,), results in type_groups.items():
        by_type[ct] = compute_stats(results)
    output["by_type"] = by_type

    # By quality
    quality_groups = group_results(primary_results, ["cand_quality"])
    by_quality = {}
    for (q,), results in quality_groups.items():
        by_quality[q] = compute_stats(results)
    output["by_quality"] = by_quality

    # All horizons summary
    for h in horizons:
        h_results = all_results.get(h, [])
        output["horizons"][str(h)] = compute_stats(h_results)

    return output


def generate_prompt_summary(stats):
    """Generate compact text for LLM prompt injection."""
    lines = ["Историческая точность A/D divergence сигналов (backtest):"]

    overall = stats.get("overall", {})
    if overall.get("n", 0) > 0:
        lines.append(
            f"  Общее: {overall['n']} сделок, Win Rate={overall['winrate']}%, "
            f"Profit Factor={overall['profit_factor']}x, Avg P&L={overall['avg_return_pct']}%"
        )

    by_symbol = stats.get("by_symbol", {})
    if by_symbol:
        lines.append("  По символам:")
        for sym, s in sorted(by_symbol.items()):
            if s.get("n", 0) > 0:
                lines.append(
                    f"    {sym}: WR={s['winrate']}%, PF={s['profit_factor']}x, "
                    f"n={s['n']}, Avg P&L={s['avg_return_pct']}%"
                )

    by_type = stats.get("by_type", {})
    if by_type:
        lines.append("  По типу:")
        for ct, s in sorted(by_type.items()):
            if s.get("n", 0) > 0:
                lines.append(
                    f"    {ct}: WR={s['winrate']}%, PF={s['profit_factor']}x, n={s['n']}"
                )

    by_quality = stats.get("by_quality", {})
    if by_quality:
        lines.append("  По качеству:")
        for q, s in sorted(by_quality.items()):
            if s.get("n", 0) > 0:
                lines.append(
                    f"    {q}: WR={s['winrate']}%, PF={s['profit_factor']}x, n={s['n']}"
                )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate backtest_stats.json from unified dataset")
    parser.add_argument("--csv", default=os.path.join("results", "unified_dataset_v11.csv"),
                        help="Path to unified dataset CSV")
    parser.add_argument("--json-input", default=None,
                        help="Optional: existing backtest_results.json (if available)")
    parser.add_argument("--output", default=os.path.join("results", "backtest_stats.json"),
                        help="Output JSON path")
    parser.add_argument("--horizons", nargs="+", type=int, default=[3, 5, 10],
                        help="Horizons to evaluate")
    parser.add_argument("--primary-horizon", type=int, default=3,
                        help="Primary horizon for stats grouping (default: 3)")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}")
        print(f"[HINT] Run v11generate_unified_dataset.ps1 first to create the dataset.")
        sys.exit(1)

    print(f"Reading: {csv_path}")
    horizons = sorted(args.horizons)

    # Run backtest
    print(f"Running backtest with horizons: {horizons}")
    all_results = run_backtest_from_csv(str(csv_path), horizons)

    for h in horizons:
        r = all_results.get(h, [])
        print(f"  H=+{h}: {len(r)} trades")

    # Generate stats
    print("\nGenerating stats...")
    stats = generate_stats(all_results, horizons)
    stats["primary_horizon"] = args.primary_horizon

    # Generate prompt summary
    stats["summary_for_prompt"] = generate_prompt_summary(stats)

    # Save
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Saved: {out_path}")
    print(f"\n=== PROMPT SUMMARY ===\n{stats['summary_for_prompt']}")


if __name__ == "__main__":
    main()
