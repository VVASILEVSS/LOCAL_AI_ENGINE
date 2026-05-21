import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.zigzag.benchmark_zigzag import run_benchmark


TF_WEIGHTS = {
    "1d": 4.0,
    "4h": 3.0,
    "1h": 2.0,
    "15m": 1.0,
}

CONFLUENCE_PRIORITY_ORDER = {"high": 3, "medium": 2, "low": 1}
SUMMARY_CONFLUENCE_MAX = 5


def _tf_weight(tf: str) -> float:
    return TF_WEIGHTS.get(tf.lower(), 1.0)


def _score_stack_bias(stack_bias: str) -> int:
    if stack_bias == "bullish":
        return 2
    if stack_bias == "bearish":
        return 1
    return 0


def _score_alignment(alignment: str) -> int:
    if alignment == "aligned":
        return 2
    if alignment == "mixed":
        return 1
    return 0


def _count_directions(directions: Dict[str, str]) -> Dict[str, int]:
    result = {"bullish": 0, "bearish": 0, "mixed": 0, "unknown": 0}
    for v in directions.values():
        if v in result:
            result[v] += 1
        else:
            result["unknown"] += 1
    return result


def _dominant_bias_weighted(directions: Dict[str, str]) -> Tuple[str, float, Dict[str, float]]:
    totals = {"bullish": 0.0, "bearish": 0.0, "mixed": 0.0, "unknown": 0.0}
    for tf, d in directions.items():
        totals[d if d in totals else "unknown"] += _tf_weight(tf)

    dominant = max(["bullish", "bearish", "mixed", "unknown"], key=lambda x: totals[x])
    total_weight = sum(totals.values()) or 1.0
    dominance_ratio = totals[dominant] / total_weight
    return dominant, dominance_ratio, totals


def _extract_levels(tf_result: Dict[str, Any]) -> List[float]:
    levels = []
    zones = tf_result.get("zones", {})
    levels.extend(zones.get("resistance", []) or [])
    levels.extend(zones.get("support", []) or [])
    return [float(x) for x in levels if isinstance(x, (int, float))]


def _cluster_levels(levels: List[float], tolerance_ratio: float = 0.0035) -> List[Dict[str, Any]]:
    if not levels:
        return []

    levels = sorted(levels)
    clusters: List[Dict[str, Any]] = []

    for price in levels:
        placed = False
        for c in clusters:
            center = c["center"]
            tol = max(center * tolerance_ratio, 1e-9)
            if abs(price - center) <= tol:
                c["levels"].append(price)
                c["center"] = sum(c["levels"]) / len(c["levels"])
                placed = True
                break
        if not placed:
            clusters.append({"center": price, "levels": [price]})

    for c in clusters:
        c["count"] = len(c["levels"])
        c["spread"] = round(max(c["levels"]) - min(c["levels"]), 6) if len(c["levels"]) > 1 else 0.0
        c["strength"] = round(c["count"] + max(0.0, 3.0 - c["spread"]), 3)

    clusters.sort(key=lambda x: (x["count"], x["strength"]), reverse=True)
    return clusters


def _build_level_confluence(tf_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    all_levels: List[float] = []
    for tf, result in tf_results.items():
        all_levels.extend(_extract_levels(result))

    clusters = _cluster_levels(all_levels)

    confluence: List[Dict[str, Any]] = []
    for c in clusters:
        touched_tfs = []
        for tf, result in tf_results.items():
            levels = _extract_levels(result)
            if any(abs(l - c["center"]) <= max(c["center"] * 0.0035, 1e-9) for l in levels):
                touched_tfs.append(tf)

        priority = "high" if len(touched_tfs) >= 3 else "medium" if len(touched_tfs) == 2 else "low"
        if priority == "low" and c["count"] < 2:
            continue

        confluence.append(
            {
                "level": round(c["center"], 2),
                "count": c["count"],
                "spread": round(c["spread"], 2),
                "strength": c["strength"],
                "timeframes": touched_tfs,
                "priority": priority,
            }
        )

    confluence.sort(
        key=lambda x: (
            CONFLUENCE_PRIORITY_ORDER.get(x["priority"], 0),
            len(x["timeframes"]),
            x["count"],
            x["strength"],
        ),
        reverse=True,
    )
    return confluence


def _detect_early_reversal(tf_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    directions = {tf: r.get("swing_direction", "unknown") for tf, r in tf_results.items()}
    weighted_bias, _, _ = _dominant_bias_weighted(directions)

    lower_signals = []
    for tf in ["15m", "1h"]:
        if tf in directions and directions[tf] != weighted_bias:
            lower_signals.append({"timeframe": tf, "direction": directions[tf]})

    disagreement_count = len(lower_signals)
    leading_timeframe = lower_signals[0]["timeframe"] if lower_signals else None

    if disagreement_count == 0:
        strength = 0.0
        reversal_type = "none"
    elif disagreement_count == 1:
        strength = 0.5
        reversal_type = "soft"
    else:
        strength = 1.0
        reversal_type = "strong"

    return {
        "weighted_bias": weighted_bias,
        "early_reversal": disagreement_count > 0,
        "strength": strength,
        "reversal_type": reversal_type,
        "leading_timeframe": leading_timeframe,
        "disagreement_count": disagreement_count,
        "signals": lower_signals,
        "reversal_context": (
            "Lower TF disagrees with weighted bias"
            if lower_signals
            else "No early reversal disagreement detected"
        ),
    }


def _detect_pattern_issue(tf_result: Dict[str, Any]) -> Dict[str, Any]:
    swing_direction = tf_result.get("swing_direction")
    pattern_tags = tf_result.get("pattern_tags", []) or []

    bullish_like = any("bullish" in p for p in pattern_tags)
    bearish_like = any("bearish" in p for p in pattern_tags)

    mismatch = False
    severity = "none"

    if swing_direction == "bullish" and bearish_like:
        mismatch = True
        severity = "soft"
    elif swing_direction == "bearish" and bullish_like:
        mismatch = True
        severity = "soft"

    if len(pattern_tags) >= 2 and bullish_like and bearish_like:
        mismatch = True
        severity = "hard"

    return {
        "mismatch": mismatch,
        "severity": severity,
        "swing_direction": swing_direction,
        "pattern_tags": pattern_tags,
    }


def _build_pattern_conflict(pattern_by_tf: Dict[str, Dict[str, Any]], stack: Dict[str, Any]) -> Dict[str, Any]:
    issue_tfs = [tf for tf, item in pattern_by_tf.items() if item["mismatch"]]
    weighted_score = sum(_tf_weight(tf) for tf in issue_tfs)
    hard_count = sum(1 for item in pattern_by_tf.values() if item["severity"] == "hard")
    dominant_tf = stack.get("dominant_tf", "1h")

    dominant_tf_issue = dominant_tf in issue_tfs
    multi_tf_issue = len(issue_tfs) >= 2
    strong_weight = weighted_score >= 3.0
    bias_relevant = dominant_tf_issue or multi_tf_issue

    conflict = (bias_relevant and strong_weight) or hard_count > 0 or (dominant_tf_issue and weighted_score >= 1.0)

    if not issue_tfs:
        severity = "none"
    elif hard_count > 0:
        severity = "hard"
    elif dominant_tf_issue and (multi_tf_issue or weighted_score >= 2.0):
        severity = "hard"
    elif bias_relevant and weighted_score >= 3.0:
        severity = "medium"
    else:
        severity = "soft"

    leading_tfs = sorted(issue_tfs, key=_tf_weight, reverse=True)

    return {
        "conflict": conflict,
        "severity": severity,
        "issue_timeframes": issue_tfs,
        "leading_timeframes": leading_tfs,
        "weighted_score": round(weighted_score, 2),
        "hard_count": hard_count,
    }


def _compute_quality_scores(
    stack: Dict[str, Any],
    directions: Dict[str, str],
    pivot_counts: Dict[str, int],
    early_reversal: Dict[str, Any],
    pattern_conflict: Dict[str, Any],
    confluence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    counts = _count_directions(directions)
    total = max(len(directions), 1)

    dominant_bias, dominance_ratio, totals = _dominant_bias_weighted(directions)
    alignment = stack.get("alignment", "mixed")
    stack_bias = stack.get("stack_bias", "mixed")
    dominant_tf = stack.get("dominant_tf", "1h")

    weighted_agreement = round(dominance_ratio * 100, 2)
    consistency_score = weighted_agreement

    dominant_weight = totals.get(dominant_bias, 0.0)
    second_best = sorted([v for k, v in totals.items() if k != dominant_bias], reverse=True)[0] if len(totals) > 1 else 0.0
    separation_bonus = 0.0 if dominant_weight <= 0 else max(0.0, (dominant_weight - second_best) / dominant_weight) * 20.0

    alignment_bonus = 20.0 if alignment == "aligned" else 10.0 if alignment == "mixed" else 0.0
    stack_match_bonus = 10.0 if stack_bias == dominant_bias else 0.0
    dominant_tf_bonus = 5.0 if dominant_tf in {"1d", "4h", "1h"} else 0.0

    confidence_raw = (
        weighted_agreement * 0.55
        + alignment_bonus
        + stack_match_bonus
        + dominant_tf_bonus
        + separation_bonus
    )

    if early_reversal.get("early_reversal"):
        confidence_raw -= 6.0 if early_reversal.get("reversal_type") == "soft" else 12.0

    if pattern_conflict.get("conflict"):
        confidence_raw -= 8.0 if pattern_conflict.get("severity") == "soft" else 14.0 if pattern_conflict.get("severity") == "medium" else 20.0

    confidence_score = round(max(0.0, min(100.0, confidence_raw)), 2)

    pivot_avg = sum(pivot_counts.values()) / max(len(pivot_counts), 1)
    pivot_score = min(pivot_avg / 12.0, 1.0) * 100.0

    confluence_strength = 0.0
    if confluence:
        top = confluence[:3]
        confluence_strength = sum(x["strength"] for x in top) / len(top)

    confluence_bonus = min(confluence_strength * 1.5, 12.0)
    if not confluence:
        confluence_bonus = -8.0

    quality_raw = (
        consistency_score * 0.34
        + confidence_score * 0.30
        + pivot_score * 0.14
        + separation_bonus * 0.10
        + confluence_bonus
    )

    if early_reversal.get("early_reversal"):
        quality_raw -= 4.0 if early_reversal.get("reversal_type") == "soft" else 8.0

    if alignment == "mixed":
        quality_raw -= 3.0
    if pattern_conflict.get("conflict"):
        quality_raw -= 6.0 if pattern_conflict.get("severity") == "soft" else 10.0 if pattern_conflict.get("severity") == "medium" else 14.0

    quality_score = round(max(0.0, min(100.0, quality_raw)), 2)

    return {
        "global_bias": dominant_bias,
        "stack_bias": stack_bias,
        "dominant_bias": dominant_bias,
        "alignment": alignment,
        "direction_counts": counts,
        "bullish_ratio": round(counts["bullish"] / total, 4),
        "bearish_ratio": round(counts["bearish"] / total, 4),
        "mixed_ratio": round(counts["mixed"] / total, 4),
        "unknown_ratio": round(counts["unknown"] / total, 4),
        "weighted_agreement": weighted_agreement,
        "consistency_score": consistency_score,
        "confidence_score": confidence_score,
        "quality_score": quality_score,
    }


def _signal_quality_state(scores: Dict[str, Any], early_reversal: Dict[str, Any], pattern_conflict: Dict[str, Any]) -> str:
    if pattern_conflict.get("conflict") and scores["confidence_score"] < 65:
        return "avoid"
    if scores["alignment"] == "aligned" and scores["quality_score"] >= 80 and not early_reversal.get("early_reversal") and not pattern_conflict.get("conflict"):
        return "strong"
    if scores["quality_score"] >= 65 and scores["confidence_score"] >= 55 and not pattern_conflict.get("conflict"):
        return "moderate"
    return "weak"


def _human_verdict(signal_state: str, scores: Dict[str, Any], early_reversal: Dict[str, Any], pattern_conflict: Dict[str, Any]) -> str:
    if signal_state == "strong":
        return f"clean {scores['global_bias']} continuation"
    if signal_state == "moderate":
        if early_reversal.get("early_reversal"):
            return f"{scores['global_bias']} bias, watch lower-TF reversal"
        return f"mixed but tradable {scores['global_bias']} context"
    if signal_state == "avoid":
        return "avoid: conflict and weak structure"
    return "weak / noisy setup"


def _fmt_bool(value: bool) -> str:
    return "yes" if value else "no"


def _render_summary_rows(summary_rows: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for row in summary_rows:
        lines.append(
            f"| {row['symbol']} | {row['global_bias']} | {row['stack_bias']} | {row['dominant_bias']} | {row['alignment']} | "
            f"{row['quality_score']} | {row['confidence_score']} | {row['consistency_score']} | {_fmt_bool(row['early_reversal'])} |"
        )
    return "\n".join(lines)


def _render_global_bias_sections(results: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for symbol, data in results.items():
        stack = data["stack"]
        scores = data["scores"]
        blocks.append(f"### {symbol}")
        blocks.append(f"- **Global Bias**: {scores['global_bias']}")
        blocks.append(f"- **Stack Bias**: {stack['stack_bias']}")
        blocks.append(f"- **Dominant Bias**: {scores['dominant_bias']}")
        blocks.append(f"- **Alignment**: {stack['alignment']}")
        blocks.append(f"- **Dominant TF**: {stack['dominant_tf']}")
        blocks.append(f"- **Signal Quality State**: {data['signal_quality_state']}")
        blocks.append(f"- **Verdict**: {data['human_verdict']}")
        blocks.append("")
    return "\n".join(blocks)


def _render_early_reversal_sections(results: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for symbol, data in results.items():
        er = data["early_reversal"]
        blocks.append(f"### {symbol}")
        blocks.append(f"- **Early Reversal**: {_fmt_bool(er['early_reversal'])}")
        blocks.append(f"- **Strength**: {er['strength']}")
        blocks.append(f"- **Type**: {er['reversal_type']}")
        blocks.append(f"- **Leading TF**: {er['leading_timeframe'] or 'none'}")
        blocks.append(f"- **Weighted Bias**: {er['weighted_bias']}")
        blocks.append(f"- **Disagreement Count**: {er['disagreement_count']}")
        if er["signals"]:
            blocks.append("- **Signals**:")
            for s in er["signals"]:
                blocks.append(f"  - {s['timeframe']}: {s['direction']}")
        else:
            blocks.append("- **Signals**: none")
        blocks.append(f"- **Context**: {er['reversal_context']}")
        blocks.append("")
    return "\n".join(blocks)


def _render_confluence_sections(results: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for symbol, data in results.items():
        blocks.append(f"### {symbol}")
        for lvl in data.get("telegram_confluence", []):
            blocks.append(
                f"- **{lvl['level']}** | TFs={', '.join(lvl['timeframes'])} | "
                f"count={lvl['count']} | priority={lvl['priority']} | spread={lvl['spread']}"
            )
        blocks.append("")
    return "\n".join(blocks)


def _render_detailed_sections(results: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for symbol, data in results.items():
        blocks.append(f"### {symbol}")
        blocks.append(f"- global_bias: {data['scores']['global_bias']}")
        blocks.append(f"- stack_bias: {data['stack']['stack_bias']}")
        blocks.append(f"- dominant_bias: {data['scores']['dominant_bias']}")
        blocks.append(f"- alignment: {data['stack']['alignment']}")
        blocks.append(f"- quality_score: {data['scores']['quality_score']}")
        blocks.append(f"- confidence_score: {data['scores']['confidence_score']}")
        blocks.append(f"- consistency_score: {data['scores']['consistency_score']}")
        blocks.append(f"- signal_quality_state: {data['signal_quality_state']}")
        blocks.append(f"- early_reversal: {data['early_reversal']['early_reversal']}")
        blocks.append(f"- pattern_mismatch: {data['pattern_mismatch']['mismatch']}")
        blocks.append(f"- pattern_mismatch_severity: {data['pattern_mismatch']['severity']}")
        blocks.append(f"- pattern_conflict: {data['pattern_conflict']['conflict']}")
        blocks.append(f"- pattern_conflict_severity: {data['pattern_conflict']['severity']}")
        blocks.append("")
    return "\n".join(blocks)


def _render_telegram_summary(payload: Dict[str, Any]) -> str:
    rows = payload["summary_rows"]
    lines: List[str] = []
    for row in rows:
        lines.append(
            f"{row['symbol']}: {row['human_verdict']} | bias={row['global_bias']} | "
            f"quality={row['quality_score']} | conf={row['confidence_score']} | early_rev={_fmt_bool(row['early_reversal'])}"
        )
    return "\n".join(lines)


def _render_telegram_human_summary(results: Dict[str, Any]) -> str:
    lines: List[str] = []
    for symbol, data in results.items():
        lines.append(f"{symbol}: {data['human_verdict']} ({data['signal_quality_state']})")
    return "\n".join(lines)


def _build_markdown_report(payload: Dict[str, Any]) -> str:
    settings = payload["settings"]
    summary_rows = payload["summary_rows"]
    results = payload["results"]

    lines: List[str] = []
    lines.append("# ZigZag Comparison Report")
    lines.append("")
    lines.append("## Settings")
    for k, v in settings.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Symbol | Global Bias | Stack Bias | Dominant Bias | Alignment | Quality | Confidence | Consistency | Early Reversal |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(_render_summary_rows(summary_rows))
    lines.append("")
    lines.append("## Global Bias Overview")
    lines.append("")
    lines.append("This section shows the main higher-timeframe context for each symbol.")
    lines.append("")
    lines.append(_render_global_bias_sections(results))
    lines.append("## Early Reversal Signals")
    lines.append("")
    lines.append("This section highlights lower-timeframe disagreement with the weighted higher-timeframe bias.")
    lines.append("")
    lines.append(_render_early_reversal_sections(results))
    lines.append("## Key Level Confluence")
    lines.append("")
    lines.append("This section shows important price zones confirmed by multiple timeframes.")
    lines.append("")
    lines.append(_render_confluence_sections(results))
    lines.append("## Detailed Results")
    lines.append("")
    lines.append(_render_detailed_sections(results))
    lines.append("## Notes")
    lines.append("")
    lines.append("- **Global Bias** is weighted toward higher timeframes.")
    lines.append("- **Early Reversal** means lower TFs are starting to disagree with the weighted higher-TF bias.")
    lines.append("- **Key Level Confluence** keeps only useful clusters and filters low-signal noise.")
    lines.append("- **pattern_mismatch** flags local structure/pattern disagreement.")
    lines.append("- **pattern_conflict** is a stronger flag when mismatches cluster across multiple or higher TFs.")
    lines.append("- **signal_quality_state** provides a quick operational verdict.")
    return "\n".join(lines)


def run_compare(
    symbols: List[str],
    market_type: str = "future",
    timeframes: Optional[List[str]] = None,
    limit: int = 300,
    mode: str = "hybrid_atr",
    length: Optional[int] = None,
    percent: Optional[float] = None,
    confirmation_mode: str = "close",
    debug: bool = False,
    output: Optional[str] = None,
) -> Dict[str, Any]:
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]

    results: Dict[str, Any] = {}
    summary_rows: List[Dict[str, Any]] = []

    print("\n=== ZIGZAG COMPARISON RUN ===")
    print(
        f"settings: market_type={market_type}, mode={mode}, length={length}, percent={percent}, "
        f"confirmation_mode={confirmation_mode}, limit={limit}, debug={debug}"
    )
    print(f"symbols: {symbols}")

    for symbol in symbols:
        print(f"\n############################")
        print(f"# SYMBOL: {symbol}")
        print(f"############################")

        benchmark = run_benchmark(
            symbol=symbol,
            market_type=market_type,
            timeframes=timeframes,
            limit=limit,
            mode=mode,
            length=length,
            percent=percent,
            confirmation_mode=confirmation_mode,
            debug=debug,
            output=None,
        )

        tf_results = benchmark.get("timeframes", {})
        stack = benchmark.get("stack", {})
        directions = stack.get("directions", {})
        pivot_counts = {tf: tf_results.get(tf, {}).get("pivot_count", 0) for tf in timeframes}

        early_reversal = _detect_early_reversal(tf_results)
        confluence = _build_level_confluence(tf_results)
        pattern_by_tf = {tf: _detect_pattern_issue(r) for tf, r in tf_results.items()}
        pattern_mismatch = {
            "mismatch": any(v["mismatch"] for v in pattern_by_tf.values()),
            "severity": (
                "hard"
                if any(v["severity"] == "hard" for v in pattern_by_tf.values())
                else "soft"
                if any(v["severity"] == "soft" for v in pattern_by_tf.values())
                else "none"
            ),
            "by_timeframe": pattern_by_tf,
        }
        pattern_conflict = _build_pattern_conflict(pattern_by_tf, stack)

        scores = _compute_quality_scores(
            stack=stack,
            directions=directions,
            pivot_counts=pivot_counts,
            early_reversal=early_reversal,
            pattern_conflict=pattern_conflict,
            confluence=confluence,
        )
        signal_state = _signal_quality_state(scores, early_reversal, pattern_conflict)
        human_verdict = _human_verdict(signal_state, scores, early_reversal, pattern_conflict)

        benchmark["scores"] = scores
        benchmark["early_reversal"] = early_reversal
        benchmark["confluence"] = confluence
        benchmark["telegram_confluence"] = confluence[:SUMMARY_CONFLUENCE_MAX]
        benchmark["pattern_mismatch"] = pattern_mismatch
        benchmark["pattern_conflict"] = pattern_conflict
        benchmark["signal_quality_state"] = signal_state
        benchmark["human_verdict"] = human_verdict
        benchmark["telegram_summary"] = {
            "symbol": symbol,
            "global_bias": scores["global_bias"],
            "stack_bias": stack.get("stack_bias", "mixed"),
            "dominant_bias": scores["dominant_bias"],
            "alignment": stack.get("alignment", "mixed"),
            "quality_score": scores["quality_score"],
            "confidence_score": scores["confidence_score"],
            "consistency_score": scores["consistency_score"],
            "early_reversal": early_reversal["early_reversal"],
            "early_reversal_type": early_reversal["reversal_type"],
            "pattern_conflict": pattern_conflict["conflict"],
            "signal_quality_state": signal_state,
            "verdict": human_verdict,
            "confluence": confluence[:SUMMARY_CONFLUENCE_MAX],
        }
        results[symbol] = benchmark

        summary_rows.append(
            {
                "symbol": symbol,
                "global_bias": scores["global_bias"],
                "stack_bias": stack.get("stack_bias", "mixed"),
                "dominant_bias": scores["dominant_bias"],
                "alignment": stack.get("alignment", "mixed"),
                "quality_score": scores["quality_score"],
                "confidence_score": scores["confidence_score"],
                "consistency_score": scores["consistency_score"],
                "early_reversal": early_reversal["early_reversal"],
                "human_verdict": human_verdict,
            }
        )

    summary_rows.sort(
        key=lambda r: (
            r["quality_score"],
            r["confidence_score"],
            r["consistency_score"],
            _score_stack_bias(r["stack_bias"]),
            _score_alignment(r["alignment"]),
        ),
        reverse=True,
    )

    payload = {
        "settings": {
            "market_type": market_type,
            "mode": mode,
            "length": length,
            "percent": percent,
            "confirmation_mode": confirmation_mode,
            "limit": limit,
            "debug": debug,
            "timeframes": timeframes,
        },
        "summary_rows": summary_rows,
        "results": results,
        "telegram_summary": _render_telegram_summary({"summary_rows": summary_rows}),
        "telegram_human_summary": _render_telegram_human_summary(results),
    }

    if output:
        out_path = Path(output)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved comparison JSON to: {out_path}")
        md_path = out_path.with_suffix(".md")
        md_path.write_text(_build_markdown_report(payload), encoding="utf-8")
        print(f"Saved comparison MD to: {md_path}")

    print("\n=== COMPARISON COMPLETE ===")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ZigZag across multiple symbols")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols, e.g. BTC/USDT XAUT/USDT ETH/USDT")
    parser.add_argument("--market-type", default="future", choices=["spot", "future"])
    parser.add_argument("--mode", default="hybrid_atr", choices=["lux_channel", "reversal", "hybrid_atr"])
    parser.add_argument("--length", type=int, default=None)
    parser.add_argument("--percent", type=float, default=None)
    parser.add_argument("--confirmation-mode", default="close", choices=["close", "wick"])
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--output", default=None, help="Optional path to save combined JSON")
    args = parser.parse_args()

    run_compare(
        symbols=args.symbols,
        market_type=args.market_type,
        mode=args.mode,
        length=args.length,
        percent=args.percent,
        confirmation_mode=args.confirmation_mode,
        limit=args.limit,
        debug=args.debug,
        output=args.output,
    )


if __name__ == "__main__":
    main()