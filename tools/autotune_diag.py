#!/usr/bin/env python3
"""
Autotune for tests/AD/diag_candidates.py

This script runs the analyze(...) function from diag_candidates.py across a grid
of parameter combinations for given CSV files and profiles, scores the results,
and writes a summary (results/autotune_summary.json).

Notes:
- To allow parameter overrides without editing the original diag_candidates.py,
  we create a temporary modified copy of the source with a small injected snippet
  that reads overrides from a module-level dict _AUTOTUNE_OVERRIDES and applies
  them right after the profile-based parameter assignments.
- This keeps the original source untouched and lets us run analyze(...) with
  different parameter combinations.
"""

import argparse
import glob
import json
import os
import runpy
import statistics
import sys
import tempfile
from collections import defaultdict
from itertools import product
from typing import Any, Dict, Optional, Tuple

MODPATH = os.path.join("tests", "AD", "diag_candidates.py")


def _read_source(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _make_temp_module_with_overrides(src_text: str, overrides: Dict[str, Any]) -> str:
    """
    Create a temporary Python file based on src_text with an injected snippet that
    applies overrides for parameters inside analyze(...).

    The injection target is the block where pivotLeft/pivotRight/profPricePct/profAdPct/minFlowAbsFrac
    are defined. If that anchor isn't found, we fallback to prepending a module-level
    _AUTOTUNE_OVERRIDES and a warning comment (best-effort).
    """
    anchor = "    # stronger pivot settings per TF"
    inject_after = None

    if anchor in src_text:
        # find insertion point: after the block that defines minFlowAbsFrac and related thresholds
        idx = src_text.find(anchor)
        # find the line 'minFlowAbsFrac' after idx
        idx_min = src_text.find("minFlowAbsFrac", idx)
        if idx_min != -1:
            # find end of line containing minFlowAbsFrac (first newline after)
            nl = src_text.find("\n", idx_min)
            inject_after = nl + 1  # insert after this newline
    # fallback: search for 'def analyze(' and try to insert after its header (less ideal)
    if inject_after is None:
        def_anchor = "def analyze(infile, profile=\"1d\", max_out=20):"
        pos = src_text.find(def_anchor)
        if pos != -1:
            # find the first blank line after def header
            nl = src_text.find("\n", pos)
            inject_after = nl + 1

    override_snippet = """
    # --- AUTOTUNE OVERRIDES: apply if provided in module-level _AUTOTUNE_OVERRIDES ---
    _ov = globals().get("_AUTOTUNE_OVERRIDES", None)
    if _ov:
        try:
            # Override pivot settings if provided
            if "pivotLeft" in _ov:
                pivotLeft = int(_ov["pivotLeft"])
            if "pivotRight" in _ov:
                pivotRight = int(_ov["pivotRight"])
            # Override numeric thresholds if provided
            if "minFlowAbsFrac" in _ov:
                minFlowAbsFrac = float(_ov["minFlowAbsFrac"])
            if "minFlowPct" in _ov:
                minFlowPct = float(_ov["minFlowPct"])
            if "minPriceMovePct" in _ov:
                minPriceMovePct = float(_ov["minPriceMovePct"])
        except Exception:
            # Defensive: if overrides invalid, ignore and continue with defaults
            pass
    # --- end overrides ---
"""

    if inject_after is not None:
        new_src = src_text[:inject_after] + override_snippet + src_text[inject_after:]
    else:
        # fallback: prepend module-level overrides and a small warning comment
        prepend = (
            "# AUTOTUNE: module-level overrides (best-effort prepended)\n"
            f"_AUTOTUNE_OVERRIDES = {json.dumps(overrides)}\n"
            "# If analyze(...) does not respect these overrides, autotune may not take effect\n\n"
        )
        new_src = prepend + src_text
        # We will still write the temp file and rely on module-level variable; weaker approach.

    # Finally, ensure module-level _AUTOTUNE_OVERRIDES is set to the overrides dictionary
    # But we do not hardcode the overrides here; the run function will write them separately
    return new_src


def run_analyze_with_overrides(
    infile: str,
    profile: str,
    overrides: Dict[str, Any],
    max_out: int = 20,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Create a temporary module file that contains the diag_candidates source with
    our injected override-snippet, plus a module-level _AUTOTUNE_OVERRIDES variable set
    to the overrides dict, then run it via runpy.run_path() and call analyze().

    Returns (json_result_dict, error_message). If there was a failure parsing JSON
    or the analyze didn't produce output, error_message will be set.
    """
    try:
        src = _read_source(MODPATH)
    except Exception as e:
        return None, f"Failed to read source {MODPATH}: {e!r}"

    # Prepare the modified source with injection point (no overrides embedded yet)
    modified_src_template = _make_temp_module_with_overrides(src, overrides={})

    # We'll write a temp file with module-level _AUTOTUNE_OVERRIDES and modified source.
    fd, tmp_path = tempfile.mkstemp(prefix="diag_candidates_autotune_", suffix=".py")
    os.close(fd)
    try:
        with open(tmp_path, "w", encoding="utf-8") as tf:
            # write overrides as module-level var so injected snippet can pick them up
            tf.write(f"_AUTOTUNE_OVERRIDES = {json.dumps(overrides)}\n\n")
            tf.write(modified_src_template)
        # Execute the temp module in its own namespace
        ns = runpy.run_path(tmp_path)
        analyze = ns.get("analyze")
        if analyze is None:
            return None, "analyze() not found after executing temporary module"
        # capture printed JSON by calling analyze and reading stdout via runpy is tricky;
        # instead, call analyze (it prints JSON via print in original) and capture stdout by redirecting.
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            analyze(infile, profile, max_out)
        txt = buf.getvalue()
        if not txt:
            return None, "analyze() produced no stdout"
        try:
            j = json.loads(txt)
        except Exception as e:
            return None, f"JSON parse error: {e!r}; stdout snippet: {txt[:200]!r}"
        return j, None
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def score_result(j: Dict[str, Any], target_min: int = 1, target_max: int = 10) -> Tuple[float, Dict[str, Any]]:
    cand = j.get("candidates", [])
    n = len(cand)
    if n == 0:
        return 0.0, {"n": 0}
    avg_flow = statistics.mean([float(c.get("flowAbsChange", 0.0)) for c in cand])
    avg_price = statistics.mean([float(c.get("priceMovePct", 0.0)) for c in cand])
    # penalty if too many or too few
    if n < target_min:
        penalty = 0.5 * (target_min - n)
    elif n > target_max:
        penalty = 0.2 * (n - target_max)
    else:
        penalty = 0.0
    # combine metrics: scale price to comparable magnitude (price in fractions)
    score = (avg_flow * 2.0 + avg_price * 100.0) / (1.0 + penalty)
    return score, {"n": n, "avg_flow": avg_flow, "avg_price": avg_price, "penalty": penalty}


def main():
    p = argparse.ArgumentParser(description="Autotune diag_candidates parameters over a grid")
    p.add_argument("--files", nargs="+", required=True, help="CSV files to tune on (infile paths)")
    p.add_argument("--profiles", nargs="+", default=["1h"], help="profiles to test per file (15m,1h,4h,1d)")
    p.add_argument("--max-out", type=int, default=20)
    args = p.parse_args()

    # Define search grid (coarse)
    pivot_opts = {"15m": [12, 14], "1h": [16, 18], "4h": [18, 20], "1d": [20, 22]}
    minFlowAbsFrac_opts = {"15m": [0.06, 0.08, 0.10], "1h": [0.08, 0.10, 0.12], "4h": [0.10, 0.12], "1d": [0.12, 0.15]}
    minFlowPct_opts = {"15m": [0.25, 0.30], "1h": [0.20, 0.25], "4h": [0.18, 0.20], "1d": [0.15, 0.18]}
    minPriceMovePct_opts = {"15m": [0.01, 0.012], "1h": [0.01, 0.012], "4h": [0.015, 0.018], "1d": [0.02, 0.025]}

    results = defaultdict(list)

    total_runs = 0
    for infile in args.files:
        for prof in args.profiles:
            piv_opts = pivot_opts.get(prof, [12])
            grid = list(product(minFlowAbsFrac_opts.get(prof, [0.1]), minFlowPct_opts.get(prof, [0.2]), minPriceMovePct_opts.get(prof, [0.01]), piv_opts))
            best = None
            for mf_frac, mf_pct, mp_pct, piv in grid:
                params = {
                    "pivotLeft": int(piv),
                    "pivotRight": int(piv),
                    "minFlowAbsFrac": float(mf_frac),
                    "minFlowPct": float(mf_pct),
                    "minPriceMovePct": float(mp_pct),
                }
                total_runs += 1
                print(f"RUN {total_runs}: {os.path.basename(infile)} {prof} params={params}", flush=True)
                j, err = run_analyze_with_overrides(infile, prof, params, args.max_out)
                if err is not None:
                    print(f"  SKIP (error): {err}", flush=True)
                    continue
                if j is None:
                    print("  SKIP (no output)", flush=True)
                    continue
                score, info = score_result(j, target_min=1, target_max=8)
                results[(infile, prof)].append((score, params, info, j))
                if best is None or score > best[0]:
                    best = (score, params, info, j)
            if best is not None:
                print(f"Best for {os.path.basename(infile)} {prof}: score={best[0]:.3f} params={best[1]} info={best[2]}", flush=True)
            else:
                print(f"No successful runs for {os.path.basename(infile)} {prof}.", flush=True)

    # Serialize summary (without dumping full candidate lists to keep file compact)
    serial = {}
    for k, v in results.items():
        serial[str(k)] = []
        # sort by score desc
        v_sorted = sorted(v, key=lambda t: t[0], reverse=True)
        for score, params, info, j in v_sorted:
            serial[str(k)].append({"score": score, "params": params, "info": info})
    outp = os.path.join("results", "autotune_summary.json")
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(serial, f, indent=2)
    print(f"WROTE {outp}")


if __name__ == "__main__":
    main()