#!/usr/bin/env python3
r"""
label_population_checker.py — Проверяет заполненность label_value в unified_dataset_v11.csv
и diagnose проблемы с labels.

Также показывает:
  - Процент заполненных/пустых labels
  - Распределение label (0 vs 1)
  - Предупреждения о дисбалансе классов
  - Рекомендации по фиксу

Usage:
  cd D:\LOCAL_AI_ENGINE
  python tools/label_population_checker.py
  python tools/label_population_checker.py --csv results/unified_dataset_v11.csv
"""

import argparse
import csv
import json
import sys
import os
from collections import Counter
from pathlib import Path


def check_labels(csv_path):
    """Check label population in unified dataset CSV."""
    print(f"Reading: {csv_path}")

    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            clean_row = {k.strip().lstrip("\ufeff"): v for k, v in row.items()}
            rows.append(clean_row)

    if not rows:
        print("[ERROR] CSV is empty")
        return

    total = len(rows)
    columns = list(rows[0].keys())

    # Check label_value column existence
    if "label_value" not in columns:
        print(f"\n[CRITICAL] Column 'label_value' NOT FOUND in CSV!")
        print(f"Available columns: {columns}")
        print("\nThis means v11generate_unified_dataset.ps1 didn't export label_value.")
        print("Check the PS1 script and ensure source JSON candidates have 'label' field.")
        return

    # Analyze labels
    labels = []
    empty_count = 0
    for row in rows:
        val = row.get("label_value", "").strip()
        if not val or val.lower() in ("nan", "none", "null", "n/a", ""):
            labels.append(None)
            empty_count += 1
        else:
            # Try to parse as number
            try:
                labels.append(int(float(val)))
            except (ValueError, TypeError):
                labels.append(val)

    filled_count = total - empty_count

    print(f"\n{'='*60}")
    print(f"LABEL POPULATION REPORT")
    print(f"{'='*60}")
    print(f"Total rows: {total}")
    print(f"Filled labels: {filled_count} ({filled_count/total*100:.1f}%)")
    print(f"Empty labels: {empty_count} ({empty_count/total*100:.1f}%)")

    if filled_count == 0:
        print(f"\n[CRITICAL] ALL LABELS ARE EMPTY!")
        print(f"\nPossible causes:")
        print(f"  1. Source *_candidates.json files don't have 'label' field")
        print(f"  2. diag_candidates.py doesn't compute/set label during detection")
        print(f"  3. v11generate_unified_dataset.ps1 reads wrong field name")
        print(f"\nRecommendations:")
        print(f"  - Run: python -c \"import json; d=json.load(open('results/X_candidates.json')); print(d['candidates'][0].keys())\"")
        print(f"  - Check if 'label' key exists in candidate objects")
        print(f"  - If missing, run diag_candidates.py with --label flag or add label logic")
        return

    # Distribution of filled labels
    filled_labels = [l for l in labels if l is not None]
    counter = Counter(filled_labels)

    print(f"\nLabel distribution:")
    for label, count in sorted(counter.items()):
        pct = count / total * 100
        print(f"  {label}: {count} ({pct:.1f}%)")

    # Check class balance
    if 0 in counter and 1 in counter:
        ratio = counter[1] / counter[0]
        print(f"\nClass balance ratio (1/0): {ratio:.3f}")
        if ratio < 0.3 or ratio > 3.0:
            print(f"  [WARNING] Significant class imbalance!")
            print(f"  Consider: class_weight='balanced', SMOTE, or resampling")
        else:
            print(f"  [OK] Reasonable class balance")

    # Show sample rows with labels
    print(f"\nSample rows with labels:")
    shown = 0
    for i, row in enumerate(rows):
        val = row.get("label_value", "").strip()
        if val and val.lower() not in ("nan", "none", "null", ""):
            print(f"  Row {i}: symbol={row.get('symbol')}, tf={row.get('tf_profile')}, "
                  f"type={row.get('candidate_type')}, label={val}, "
                  f"time={row.get('candidate_time', '')[:16]}")
            shown += 1
            if shown >= 5:
                break

    # Show sample empty rows
    if empty_count > 0:
        print(f"\nSample empty label rows:")
        shown = 0
        for i, row in enumerate(rows):
            val = row.get("label_value", "").strip()
            if not val or val.lower() in ("nan", "none", "null", ""):
                print(f"  Row {i}: symbol={row.get('symbol')}, tf={row.get('tf_profile')}, "
                      f"type={row.get('candidate_type')}, label='[empty]', "
                      f"time={row.get('candidate_time', '')[:16]}")
                shown += 1
                if shown >= 5:
                    break

    # Cross-tab: labels by symbol
    print(f"\n--- Labels by Symbol ---")
    sym_labels = {}
    for row in rows:
        sym = row.get("symbol", "?")
        val = row.get("label_value", "").strip()
        if sym not in sym_labels:
            sym_labels[sym] = {"total": 0, "filled": 0, "0": 0, "1": 0}
        sym_labels[sym]["total"] += 1
        if val and val.lower() not in ("nan", "none", "null", ""):
            sym_labels[sym]["filled"] += 1
            try:
                lbl = int(float(val))
                if lbl in (0, 1):
                    sym_labels[sym][str(lbl)] += 1
            except (ValueError, TypeError):
                pass

    for sym, s in sorted(sym_labels.items()):
        print(f"  {sym}: total={s['total']}, filled={s['filled']} ({s['filled']/s['total']*100:.0f}%), "
              f"0={s['0']}, 1={s['1']}")

    # Cross-tab: labels by TF
    print(f"\n--- Labels by Timeframe ---")
    tf_labels = {}
    for row in rows:
        tf = row.get("tf_profile", row.get("timeframe", "?"))
        val = row.get("label_value", "").strip()
        if tf not in tf_labels:
            tf_labels[tf] = {"total": 0, "filled": 0, "0": 0, "1": 0}
        tf_labels[tf]["total"] += 1
        if val and val.lower() not in ("nan", "none", "null", ""):
            tf_labels[tf]["filled"] += 1
            try:
                lbl = int(float(val))
                if lbl in (0, 1):
                    tf_labels[tf][str(lbl)] += 1
            except (ValueError, TypeError):
                pass

    for tf, s in sorted(tf_labels.items()):
        print(f"  {tf}: total={s['total']}, filled={s['filled']} ({s['filled']/s['total']*100:.0f}%), "
              f"0={s['0']}, 1={s['1']}")

    # Check source JSON candidates for label field
    print(f"\n--- Source JSON Label Check ---")
    results_dir = Path(csv_path).parent
    cand_files = list(results_dir.glob("*_candidates.json"))
    print(f"Candidate files found: {len(cand_files)}")

    for cf in sorted(cand_files)[:3]:
        try:
            with open(cf, "r", encoding="utf-8") as f:
                data = json.load(f)
            cands = data.get("candidates", [])
            if cands:
                first = cands[0] if isinstance(cands, list) else cands
                keys = list(first.keys()) if isinstance(first, dict) else []
                has_label = "label" in keys
                print(f"  {cf.name}: {len(cands)} candidates, keys={keys[:8]}, has_label={has_label}")
                if has_label:
                    print(f"    label value: {first.get('label')}")
        except Exception as e:
            print(f"  {cf.name}: error reading - {e}")

    # Verdict
    print(f"\n{'='*60}")
    if empty_count == 0:
        print(f"[OK] All labels populated. Ready for ML training.")
    elif filled_count > total * 0.8:
        print(f"[WARNING] Most labels populated ({filled_count/total*100:.0f}%), but {empty_count} empty.")
        print(f"  Consider dropping empty rows or imputing labels.")
    elif filled_count > 0:
        print(f"[WARNING] Only {filled_count/total*100:.0f}% labels filled. Dataset may need repair.")
        print(f"  Check *_candidates.json for 'label' field.")
    else:
        print(f"[CRITICAL] No labels populated! Cannot train ML model.")
        print(f"  Action required: add label computation to diag_candidates.py")


def main():
    parser = argparse.ArgumentParser(description="Check label population in unified dataset")
    parser.add_argument("--csv", default=os.path.join("results", "unified_dataset_v11.csv"),
                        help="Path to unified dataset CSV")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}")
        sys.exit(1)

    check_labels(str(csv_path))


if __name__ == "__main__":
    main()
