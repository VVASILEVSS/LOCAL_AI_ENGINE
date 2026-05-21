from __future__ import annotations

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


def _tf_weight(tf: str) -> float:
    return TF_WEIGHTS.get(tf.lower(), 1.0)


def _weighted_bias(directions: Dict[str, str]) -> Tuple[str, float]:
    totals = {"bullish": 0.0, "bearish": 0.0, "mixed": 0.0, "unknown": 0.0}
    for tf, direction in directions.items():
        key = direction if direction in totals else "unknown"
        totals[key] += _tf_weight(tf)

    dominant = max(totals, key=lambda k: totals[k])
    total_weight = sum(totals.values()) or 1.0
    return dominant, round(totals[dominant] / total_weight, 4)


def _count_tags(tag_map: Dict[str, List[str]], needle: str) -> int:
    return sum(1 for tags in tag_map.values() if needle in tags)


def _closest_zone_pressure(tf_result: Dict[str, Any]) -> float:
    pos = float(tf_result.get("price_position", 0.5))
    if pos >= 0.8 or pos <= 0.2:
        return 1.0
    if pos >= 0.65 or pos <= 0.35:
        return 0.6
    return 0.2


def _human_verdict(stack_bias: str, alignment: str, dominance_ratio: float, bullish_count: int, bearish_count: int, correction_count: int) -> str:
    if bullish_count > 0 and bearish_count > 0:
        if correction_count > 0:
            return "mixed transition with correction"
        return "mixed transition"

    if stack_bias == "bearish" and dominance_ratio >= 0.65:
        return "bearish dominant context"
    if stack_bias == "bullish" and dominance_ratio >= 0.65:
        return "bullish dominant context"

    if alignment == "aligned" and stack_bias == "bearish":
        return "bearish aligned context"
    if alignment == "aligned" and stack_bias == "bullish":
        return "bullish aligned context"

    return "unclear"


def _build_markdown_report(payload: Dict[str, Any]) -> str:
    lines = ["# ZigZag Comparison Report", ""]
    settings = payload.get("settings", {})
    lines.append("## Settings")
    for k, v in settings.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")

    lines.append("## Summary")
    for row in payload.get("summary_rows", []):
        lines.append(
            f"- **{row['symbol']}**: bias={row['global_bias']} | dominant={row['dominant_bias']} | "
            f"confidence={row['confidence_score']} | quality={row['quality_score']} | verdict={row['human_verdict']}"
        )
    lines.append("")
    return "\n".join(lines)


def _score_symbol(benchmark: Dict[str, Any]) -> Dict[str, Any]:
    stack = benchmark.get("stack", {})
    directions = stack.get("directions", {})
    timeframes = benchmark.get("timeframes", {})

    dominant_bias, dominance_ratio = _weighted_bias(directions)
    global_bias = stack.get("stack_bias", "mixed")
    alignment = stack.get("alignment", "mixed")

    bullish_count = sum(1 for v in directions.values() if v == "bullish")
    bearish_count = sum(1 for v in directions.values() if v == "bearish")

    tf_tags = {tf: list((data or {}).get("pattern_tags", [])) for tf, data in timeframes.items()}
    correction_count = _count_tags(tf_tags, "bullish_correction") + _count_tags(tf_tags, "bearish_correction")
    extension_count = _count_tags(tf_tags, "bullish_extension") + _count_tags(tf_tags, "bearish_extension")
    recovery_count = _count_tags(tf_tags, "bullish_recovery") + _count_tags(tf_tags, "bearish_recovery")

    resistance_pressure = sum(_closest_zone_pressure(data) for data in timeframes.values())
    pressure_bonus = round(resistance_pressure / max(len(timeframes), 1), 4)

    quality_score = round(
        48.0
        + dominance_ratio * 24.0
        + min(8.0, correction_count * 2.0)
        + min(6.0, recovery_count * 1.5)
        + min(6.0, extension_count * 1.5)
        + (4.0 if alignment == "aligned" else 0.0)
        + (3.0 if bullish_count > 0 and bearish_count > 0 else 0.0)
        + pressure_bonus * 5.0,
        2,
    )

    confidence_score = round(
        50.0
        + dominance_ratio * 26.0
        + (4.0 if global_bias != "mixed" else 0.0)
        + (4.0 if alignment == "aligned" else 0.0)
        + min(6.0, extension_count * 1.5)
        + min(4.0, correction_count * 1.0),
        2,
    )

    verdict = _human_verdict(
        stack_bias=global_bias,
        alignment=alignment,
        dominance_ratio=dominance_ratio,
        bullish_count=bullish_count,
        bearish_count=bearish_count,
        correction_count=correction_count,
    )

    benchmark["dominant_bias"] = dominant_bias
    benchmark["dominance_ratio"] = dominance_ratio
    benchmark["quality_score"] = quality_score
    benchmark["confidence_score"] = confidence_score
    benchmark["early_reversal"] = "yes" if bullish_count > 0 and bearish_count > 0 else "no"
    benchmark["human_verdict"] = verdict
    benchmark["structure_stats"] = {
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "correction_count": correction_count,
        "extension_count": extension_count,
        "recovery_count": recovery_count,
        "pressure_bonus": pressure_bonus,
    }

    return benchmark


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

    for symbol in symbols:
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
            output_mode="compact",
        )

        benchmark = _score_symbol(benchmark)
        stack = benchmark.get("stack", {})

        results[symbol] = benchmark
        summary_rows.append(
            {
                "symbol": symbol,
                "global_bias": stack.get("stack_bias", "mixed"),
                "dominant_bias": benchmark.get("dominant_bias", "mixed"),
                "dominance_ratio": benchmark.get("dominance_ratio", 0.0),
                "quality_score": benchmark.get("quality_score", 0.0),
                "confidence_score": benchmark.get("confidence_score", 0.0),
                "early_reversal": benchmark.get("early_reversal", "no"),
                "human_verdict": benchmark.get("human_verdict", "unclear"),
            }
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
    }

    if output:
        out_path = Path(output)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out_path.with_suffix(".md").write_text(_build_markdown_report(payload), encoding="utf-8")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ZigZag across multiple symbols")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--market-type", default="future", choices=["spot", "future"])
    parser.add_argument("--mode", default="hybrid_atr", choices=["lux_channel", "reversal", "hybrid_atr"])
    parser.add_argument("--length", type=int, default=None)
    parser.add_argument("--percent", type=float, default=None)
    parser.add_argument("--confirmation-mode", default="close", choices=["close", "wick"])
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    run_compare(
        symbols=args.symbols,
        market_type=args.market_type,
        timeframes=["15m", "1h", "4h", "1d"],
        limit=args.limit,
        mode=args.mode,
        length=args.length,
        percent=args.percent,
        confirmation_mode=args.confirmation_mode,
        debug=args.debug,
        output=args.output,
    )


if __name__ == "__main__":
    main()