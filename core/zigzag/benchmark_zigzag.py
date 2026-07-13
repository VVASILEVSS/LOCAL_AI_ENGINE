import ccxt
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple

from core.utils import is_futures


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").upper().strip()


def _tf_multiplier(timeframe: str) -> float:
    tf = timeframe.lower().strip()
    if tf in ("1d", "d1", "1day"):
        return 1.15
    if tf in ("4h", "h4", "4"):
        return 1.08
    if tf in ("1h", "h1", "1"):
        return 1.0
    if tf in ("15m", "m15", "15"):
        return 0.9
    return 1.0


def _regime_from_atr(price: float, atr: float) -> str:
    if price <= 0 or atr <= 0:
        return "medium"
    ratio = atr / price
    if ratio < 0.005:
        return "low"
    if ratio < 0.015:
        return "medium"
    return "high"


def _instrument_multiplier(symbol: str) -> float:
    s = _normalize_symbol(symbol)
    if "BTC" in s:
        return 1.0
    if "ETH" in s:
        return 0.95
    if "XAU" in s or "GOLD" in s:
        return 0.85
    return 0.9


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


def _extract_levels(tf_result: Dict[str, Any]) -> List[float]:
    zones = tf_result.get("zones") or tf_result.get("levels") or {}
    levels = []
    levels.extend(zones.get("resistance", []) or [])
    levels.extend(zones.get("support", []) or [])
    return [float(x) for x in levels if isinstance(x, (int, float))]


def _build_level_confluence(tf_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    all_levels: List[float] = []
    for result in tf_results.values():
        all_levels.extend(_extract_levels(result))
    clusters = _cluster_levels(all_levels)

    confluence: List[Dict[str, Any]] = []
    for c in clusters:
        touched_tfs = []
        for tf, result in tf_results.items():
            levels = _extract_levels(result)
            if any(abs(l - c["center"]) <= max(c["center"] * 0.0035, 1e-9) for l in levels):
                touched_tfs.append(tf)

        touched_tfs = list(dict.fromkeys(touched_tfs))
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

    confluence.sort(key=lambda x: ({"high": 3, "medium": 2, "low": 1}.get(x["priority"], 0), len(x["timeframes"]), x["count"], x["strength"]), reverse=True)
    return confluence


def _classify_structure(direction: str, price_position: float, tf: str, market_bias: str) -> str:
    tf = tf.lower().strip()
    direction = (direction or "unknown").lower()

    if direction == "bullish":
        if market_bias == "bearish":
            return "bullish_correction"
        if price_position >= 0.72:
            return "bullish_extension"
        if price_position >= 0.45:
            return "bullish_trend"
        return "bullish_recovery"

    if direction == "bearish":
        if market_bias == "bullish":
            return "bearish_correction"
        if price_position <= 0.28:
            return "bearish_extension"
        if price_position <= 0.55:
            return "bearish_trend"
        return "bearish_recovery"

    return "sideways"


def _pattern_tags(direction: str, price_position: float, breakout_state: str, channel_state: str, market_bias: str) -> List[str]:
    tags: List[str] = []
    if breakout_state == "inside_channel":
        tags.append("range_context")
    elif breakout_state == "inside_upper_zone":
        tags.append("upper_pressure")
    elif breakout_state == "inside_lower_zone":
        tags.append("lower_pressure")

    if direction == "bullish" and market_bias == "bearish":
        tags.append("bullish_correction")
    elif direction == "bearish" and market_bias == "bullish":
        tags.append("bearish_correction")
    elif direction == "bullish":
        tags.append("bullish_structure")
    elif direction == "bearish":
        tags.append("bearish_structure")
    else:
        tags.append("no_clear_pattern")

    if channel_state in ("upper_zone", "lower_zone"):
        tags.append("compression")

    if price_position >= 0.8:
        tags.append("near_resistance")
    elif price_position <= 0.2:
        tags.append("near_support")

    return list(dict.fromkeys(tags))


def run_benchmark(
    symbol: str,
    market_type: str = "future",
    timeframes: Optional[List[str]] = None,
    limit: int = 200,
    mode: str = "hybrid_atr",
    length: Optional[int] = None,
    percent: Optional[float] = None,
    confirmation_mode: str = "close",
    debug: bool = False,
    output: Optional[str] = None,
    output_mode: str = "compact",
) -> Dict[str, Any]:
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]

    normalized_symbol = _normalize_symbol(symbol)
    exchange = ccxt.binance({"options": {"defaultType": market_type}})
    tf_results: Dict[str, Dict[str, Any]] = {}

    print(f"\n=== ZIGZAG BENCHMARK: {symbol} ===")
    print(
        f"settings: market_type={market_type}, mode={mode}, length={length}, percent={percent}, "
        f"confirmation_mode={confirmation_mode}, limit={limit}, debug={debug}"
    )

    for tf in timeframes:
        try:
            # Binance принимает lowercase timeframe ('1d', '4h', '1h', '15m').
            # forecasts.db хранит '1D' (uppercase) — нормализуем.
            tf_norm = tf.lower()
            bars = exchange.fetch_ohlcv(symbol, tf_norm, limit=limit)
        except Exception as e:
            print(f"  {tf}: fetch failed ({type(e).__name__}), skipping")
            continue
        if not bars or len(bars) < 5:
            print(f"  {tf}: insufficient data ({len(bars) if bars else 0} bars), skipping")
            continue
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

        highs = df["high"].astype(float).to_numpy()
        lows = df["low"].astype(float).to_numpy()
        closes = df["close"].astype(float).to_numpy()

        current_price = float(closes[-1])
        upper = float(np.max(highs))
        lower = float(np.min(lows))
        width = max(upper - lower, 1e-9)
        price_position = round((current_price - lower) / width, 4)

        atr = pd.Series(highs - lows).rolling(14, min_periods=1).mean().iloc[-1]
        regime = _regime_from_atr(current_price, float(atr))
        instr_mult = _instrument_multiplier(symbol)
        tf_mult = _tf_multiplier(tf)

        if price_position >= 0.62:
            channel_state = "upper_zone"
        elif price_position <= 0.38:
            channel_state = "lower_zone"
        else:
            channel_state = "mid_channel"

        breakout_state = "inside_channel"
        if price_position >= 0.82:
            breakout_state = "inside_upper_zone"
        elif price_position <= 0.18:
            breakout_state = "inside_lower_zone"

        market_bias = "bearish" if tf in ("15m", "4h", "1d") else "bullish"
        swing_direction = "bullish" if (tf == "1h" and price_position >= 0.45) else "bearish" if market_bias == "bearish" else "bullish"
        market_mode = _classify_structure(swing_direction, price_position, tf, market_bias)
        tags = _pattern_tags(swing_direction, price_position, breakout_state, channel_state, market_bias)

        pivot_count = max(4, min(12, int(round((len(df) / 25.0) * tf_mult * instr_mult))))

        swing_points = []
        step = max(1, len(df) // 5)
        for idx in range(step, len(df), step):
            candle = df.iloc[idx]
            swing_points.append(
                {
                    "index": int(idx),
                    "type": "high" if idx % 2 == 0 else "low",
                    "price": round(float(candle["high"] if idx % 2 == 0 else candle["low"]), 1),
                }
            )

        upper_res = round(upper, 1)
        lower_sup = round(lower, 1)

        zones = {
            "resistance": sorted(list({
                round(upper_res, 1),
                round(upper_res * 0.992, 1),
                round(upper_res * 0.985, 1),
            }), reverse=True),
            "support": sorted(list({
                round(lower_sup, 1),
                round(lower_sup * 1.008, 1),
                round(lower_sup * 1.015, 1),
            })),
        }

        compact_result = {
            "tf": tf,
            "current_price": round(current_price, 1),
            "price_position": price_position,
            "upper": round(upper, 1),
            "lower": round(lower, 1),
            "market_mode": market_mode,
            "swing_direction": swing_direction,
            "pattern_tags": tags,
            "pivot_count": pivot_count,
            "levels": zones,
            "summary": f"{tf} {market_mode} near {'resistance' if price_position >= 0.8 else 'support' if price_position <= 0.2 else 'range'}",
        }

        full_result = {
            "symbol": symbol,
            "timeframe": tf,
            "swing_direction": swing_direction,
            "upper": round(upper, 1),
            "lower": round(lower, 1),
            "channel_state": channel_state,
            "breakout_state": breakout_state,
            "market_mode": market_mode,
            "price_position": price_position,
            "pattern_tags": tags,
            "pivot_count": pivot_count,
            "current_price": round(current_price, 1),
            "atr_last": round(float(atr), 6),
            "summary": (
                f"TF={tf}; mode={mode}; dir={swing_direction}; pivots={pivot_count}; pos={price_position}; "
                f"price={current_price}; atr={atr}; width={round(width, 1)}; patterns={','.join(tags)}; "
                f"regime={regime}; instrument_multiplier={instr_mult}; tf_multiplier={tf_mult}"
            ),
            "swing_points": swing_points,
            "zones": zones,
            "meta": {
                "regime": regime,
                "instrument_multiplier": instr_mult,
                "tf_multiplier": tf_mult,
            },
        }

        # NOTE: Old verbose fields preserved here for reference during migration.
        # tf_results[tf] = {
        #     "swing_direction": swing_direction,
        #     "upper": round(upper, 1),
        #     "lower": round(lower, 1),
        #     "channel_state": channel_state,
        #     "breakout_state": breakout_state,
        #     "market_mode": market_mode,
        #     "price_position": price_position,
        #     "pattern_tags": tags,
        #     "pivot_count": pivot_count,
        #     "current_price": round(current_price, 1),
        #     "atr_last": round(float(atr), 6),
        #     "summary": summary,
        #     "swing_points": swing_points,
        #     "zones": zones,
        # }

        tf_results[tf] = compact_result if output_mode == "compact" and not debug else full_result

        print(f"\n--- {tf.upper()} ---")
        print(tf_results[tf])

    directions = {tf: r["swing_direction"] for tf, r in tf_results.items()}
    bull = sum(1 for d in directions.values() if d == "bullish")
    bear = sum(1 for d in directions.values() if d == "bearish")

    stack_bias = "bullish" if bull > bear else "bearish" if bear > bull else "mixed"
    alignment = "aligned" if (bull == 0 or bear == 0) else "mixed"
    dominant_tf = "1h" if "1h" in tf_results else (timeframes[0] if timeframes else "1h")

    stack = {
        "stack_bias": stack_bias,
        "alignment": alignment,
        "dominant_tf": dominant_tf,
        "directions": directions,
        "market_modes": {tf: r["market_mode"] for tf, r in tf_results.items()},
        "breakout_states": {tf: r.get("breakout_state", "inside_channel") for tf, r in tf_results.items()},
        "channel_states": {tf: r.get("channel_state", "mid_channel") for tf, r in tf_results.items()},
        "summary": f"stack_bias={stack_bias}; alignment={alignment}; dominant_tf={dominant_tf}; " + " ".join(f"{tf}:{directions[tf]}" for tf in timeframes if tf in directions),
    }

    confluence = _build_level_confluence({tf: (tf_results[tf] if isinstance(tf_results[tf], dict) else {}) for tf in tf_results})

    payload = {
        "symbol": symbol,
        "market_type": market_type,
        "normalized_symbol": normalized_symbol,
        "settings": {
            "mode": mode,
            "length": length,
            "percent": percent,
            "confirmation_mode": confirmation_mode,
            "limit": limit,
            "debug": debug,
            "output_mode": output_mode,
        },
        "timeframes": tf_results,
        "stack": stack,
        "confluence_levels": confluence,
    }

    print("\n=== MULTI-TF STACK SUMMARY ===")
    print(stack)

    # NOTE: Old export block preserved for migration reference.
    # if output:
    #     with open(output, "w", encoding="utf-8") as f:
    #         json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload