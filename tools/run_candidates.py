#!/usr/bin/env python3
"""
run_candidates.py — run diag_candidates.analyze() and save JSON to results/

v2.3 — aligned with diag_candidates.py v2.3 (BTC profiles, regime/bias)

Pipeline step: CSV data -> analyze() -> *_candidates.json -> PS1 -> unified_dataset_v11.csv

Usage:
    python tools/run_candidates.py --files data/ohlcv/current/BTCUSDT_1h.csv --profiles 1h
    python tools/run_candidates.py --data-dir data/ohlcv/current --pattern "*.csv" --profiles 15m 1h
    python tools/run_candidates.py --files data/ohlcv/current/BTCUSDT_1h.csv --profiles 1h --use-autotune
    python tools/run_candidates.py --files data/ohlcv/current/BTCUSDT_1h.csv --profiles 1h --max-out 50
"""

import argparse
import io
import json
import os
import sys
import contextlib
from pathlib import Path

# Direct import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.AD.diag_candidates import analyze


def run_and_save(infile: str, profile: str, out_dir: str, max_out: int = 20,
                 overrides: dict = None) -> dict:
    """
    Run analyze() on a CSV file and save JSON to out_dir.
    Returns parsed result dict.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        analyze(infile, profile, max_out, overrides=overrides)
    txt = buf.getvalue()
    if not txt.strip():
        return {"error": "analyze() produced no output", "file": infile, "profile": profile}

    try:
        result = json.loads(txt)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}", "file": infile, "profile": profile, "raw": txt[:500]}

    # Save to results/ as <SYMBOL>_<TF>_candidates.json
    os.makedirs(out_dir, exist_ok=True)
    basename = os.path.basename(infile)
    # Expect pattern: SYMBOL_TF.csv or SYMBOL_TF_other.csv
    parts = basename.replace(".csv", "").split("_")
    sym = parts[0].upper() if parts else "UNKNOWN"
    out_name = f"{sym}_{profile}_candidates.json"
    out_path = os.path.join(out_dir, out_name)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return {"saved": out_path, "candidates": len(result.get("candidates", []))}


def load_autotune_params(results_dir: str) -> dict:
    """Load autotune_best_params.json if it exists."""
    path = os.path.join(results_dir, "autotune_best_params.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    p = argparse.ArgumentParser(description="Run A/D candidate detection and save JSON results (v2.3)")
    p.add_argument("--files", nargs="+", help="Explicit CSV files to process")
    p.add_argument("--data-dir", help="Directory to scan for CSV files")
    p.add_argument("--pattern", default="*.csv", help="Glob pattern for data-dir scan")
    p.add_argument("--profiles", nargs="+", default=["1h"], help="Profiles: 15m 1h 4h 1d")
    p.add_argument("--max-out", type=int, default=20, help="Max candidates per file/profile")
    p.add_argument("--results-dir", default="results", help="Output directory for JSON files")
    p.add_argument("--use-autotune", action="store_true", help="Apply autotune_best_params.json overrides")
    args = p.parse_args()

    # Collect files
    files = []
    if args.files:
        files = list(args.files)
    elif args.data_dir:
        import glob
        files = sorted(glob.glob(os.path.join(args.data_dir, args.pattern)))
    else:
        print("Error: provide --files or --data-dir", file=sys.stderr)
        sys.exit(1)

    if not files:
        print("No CSV files found.", file=sys.stderr)
        sys.exit(1)

    # Load autotune params if requested
    autotune_map = {}
    if args.use_autotune:
        autotune_map = load_autotune_params(args.results_dir)
        if autotune_map:
            print(f"Loaded autotune params for {len(autotune_map)} symbols")
        else:
            print("No autotune params found, using defaults")

    total = 0
    ok = 0
    err = 0

    print(f"\n=== run_candidates v2.3 | files={len(files)} profiles={args.profiles} ===\n")

    for infile in files:
        for prof in args.profiles:
            total += 1
            overrides = None

            # Apply autotune overrides if available
            if autotune_map:
                sym = os.path.basename(infile).split("_")[0].upper()
                if sym in autotune_map and prof in autotune_map[sym]:
                    overrides = autotune_map[sym][prof]

            res = run_and_save(infile, prof, args.results_dir, args.max_out, overrides=overrides)

            if "error" in res:
                err += 1
                print(f"  ERROR: {os.path.basename(infile)} | {prof} | {res['error']}")
            else:
                ok += 1
                print(f"  OK: {os.path.basename(infile)} | {prof} | {res['candidates']} candidates -> {res['saved']}")

    print(f"\n{'='*60}")
    print(f"Total: {total} | OK: {ok} | Errors: {err}")
    print(f"Output dir: {os.path.abspath(args.results_dir)}")
    print(f"{'='*60}")

    if err > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
