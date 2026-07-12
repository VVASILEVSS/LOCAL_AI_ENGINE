from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


TF_WEIGHTS = {
    "1d": 4.0,
    "4h": 3.0,
    "1h": 2.0,
    "15m": 1.0,
}


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").upper().strip()


def _instrument_multiplier(symbol: str) -> float:
    s = _normalize_symbol(symbol)
    if "BTC" in s:
        return 1.0
    if "ETH" in s:
        return 0.95
    if "XAU" in s or "GOLD" in s:
        return 0.85
    return 0.9


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


@dataclass
class ZigZagPoint:
    index: int
    type: str
    price: float


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_preserve_order(values: List[float]) -> List[float]:
    seen = set()
    out: List[float] = []
    for v in values:
        key = round(float(v), 8)
        if key not in seen:
            seen.add(key)
            out.append(float(v))
    return out


def _classify_structure(direction: str, price_position: float, market_bias: str) -> str:
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


def get_structural_extremums_zigzag(
    symbol: str,
    timeframe: str,
    current_price: float,
    upper: float,
    lower: float,
    swing_direction: str,
    swing_points: List[Dict[str, Any]],
    zones: Optional[Dict[str, List[float]]] = None,
    atr_last: Optional[float] = None,
) -> Dict[str, Any]:
    zones = zones or {"resistance": [], "support": []}

    width = max(upper - lower, 1e-9)
    price_position = round((current_price - lower) / width, 4)

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

    market_bias = "bearish" if timeframe.lower() in ("15m", "4h", "1d") else "bullish"
    market_mode = _classify_structure(swing_direction, price_position, market_bias)
    pattern_tags = _pattern_tags(swing_direction, price_position, breakout_state, channel_state, market_bias)

    regime = _regime_from_atr(current_price, atr_last or 0.0)
    instr_mult = _instrument_multiplier(symbol)
    tf_mult = _tf_multiplier(timeframe)

    pivot_count = len(swing_points) if swing_points else 0

    resistance = _dedupe_preserve_order([_safe_float(x) for x in zones.get("resistance", []) if _safe_float(x) is not None])  # type: ignore[arg-type]
    support = _dedupe_preserve_order([_safe_float(x) for x in zones.get("support", []) if _safe_float(x) is not None])  # type: ignore[arg-type]

    summary = (
        f"TF={timeframe}; dir={swing_direction}; pivots={pivot_count}; pos={price_position}; "
        f"price={current_price}; atr={atr_last}; width={round(width, 1)}; patterns={','.join(pattern_tags)}; "
        f"regime={regime}; instrument_multiplier={instr_mult}; tf_multiplier={tf_mult}"
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "swing_direction": swing_direction,
        "upper": round(upper, 1),
        "lower": round(lower, 1),
        "channel_state": channel_state,
        "breakout_state": breakout_state,
        "market_mode": market_mode,
        "price_position": price_position,
        "pattern_tags": pattern_tags,
        "pivot_count": pivot_count,
        "current_price": round(current_price, 1),
        "atr_last": round(float(atr_last), 6) if atr_last is not None else None,
        "summary": summary,
        "swing_points": swing_points,
        "zones": {
            "resistance": resistance,
            "support": support,
        },
        "meta": {
            "regime": regime,
            "instrument_multiplier": instr_mult,
            "tf_multiplier": tf_mult,
        },
    }