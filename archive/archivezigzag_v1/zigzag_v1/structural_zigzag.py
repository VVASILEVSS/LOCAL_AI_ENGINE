import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any


# -----------------------------------------------------------------------------
# ZigZag core
# Modes:
#   - lux_channel: lagged channel/state-switch style, close to LuxAlgo visual logic
#   - reversal: percent-based reversal detector
#   - hybrid_atr: auto-tuned reversal threshold using percent + ATR + instrument regime
# -----------------------------------------------------------------------------


def _log(enabled: bool, msg: str) -> None:
    if enabled:
        print(f"[ZZ] {msg}")


def _compute_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")

    prev_close = close.shift(1).fillna(close.iloc[0])

    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(period, min_periods=1).mean()
    return atr.to_numpy(dtype=float)


def _tf_params(timeframe: str) -> Dict[str, float]:
    tf = timeframe.lower()
    if tf in ("15m", "15", "m15"):
        return {"percent": 0.0025, "atr_mult": 3.0, "length": 30}
    if tf in ("1h", "1", "h1"):
        return {"percent": 0.0030, "atr_mult": 3.5, "length": 45}
    if tf in ("4h", "4", "h4"):
        return {"percent": 0.0040, "atr_mult": 4.0, "length": 60}
    if tf in ("1d", "d", "1D"):
        return {"percent": 0.0060, "atr_mult": 4.5, "length": 100}
    return {"percent": 0.0030, "atr_mult": 3.5, "length": 45}


def _autotune_percent(
    timeframe: str,
    current_price: float,
    atr_last: float,
    base_percent: float,
    atr_mult: float,
) -> float:
    atr_ratio = (atr_last / current_price) if current_price > 0 else 0.0
    adaptive_percent = atr_ratio * atr_mult

    tf = timeframe.lower()
    if tf in ("15m", "15", "m15"):
        adaptive_percent *= 0.90
    elif tf in ("1h", "1", "h1"):
        adaptive_percent *= 1.00
    elif tf in ("4h", "4", "h4"):
        adaptive_percent *= 1.08
    elif tf in ("1d", "d", "1D"):
        adaptive_percent *= 1.15

    return float(max(base_percent, adaptive_percent))


def _volatility_regime(atr_last: float, current_price: float) -> str:
    if current_price <= 0:
        return "unknown"
    ratio = atr_last / current_price
    if ratio < 0.0025:
        return "low"
    if ratio < 0.0060:
        return "medium"
    return "high"


def _instrument_multiplier(symbol: Optional[str], regime: str) -> float:
    """
    Lightweight heuristic for instrument-specific adaptation.
    BTC/ETH tend to need slightly lower sensitivity than many alts.
    High volatility regime increases threshold to avoid noise.
    """
    s = (symbol or "").upper()
    base = 1.0

    if any(x in s for x in ["BTC", "ETH", "SOL"]):
        base *= 0.95
    elif any(x in s for x in ["DOGE", "SHIB", "PEPE", "BONK"]):
        base *= 1.15
    elif s:
        base *= 1.05

    if regime == "low":
        base *= 0.90
    elif regime == "medium":
        base *= 1.00
    elif regime == "high":
        base *= 1.15

    return float(base)


def _dedupe_pivots(pivots: List[Tuple[int, str, float]]) -> List[Tuple[int, str, float]]:
    if not pivots:
        return []

    pivots = sorted(pivots, key=lambda x: x[0])
    cleaned: List[Tuple[int, str, float]] = []

    for idx, typ, price in pivots:
        idx = int(idx)
        price = float(price)

        if not cleaned:
            cleaned.append((idx, typ, price))
            continue

        last_idx, last_typ, last_price = cleaned[-1]

        if idx == last_idx:
            if typ == last_typ:
                if typ == "high" and price >= last_price:
                    cleaned[-1] = (idx, typ, price)
                elif typ == "low" and price <= last_price:
                    cleaned[-1] = (idx, typ, price)
            else:
                if abs(price - last_price) > 0:
                    cleaned.append((idx, typ, price))
            continue

        if typ == last_typ:
            if typ == "high" and price >= last_price:
                cleaned[-1] = (idx, typ, price)
            elif typ == "low" and price <= last_price:
                cleaned[-1] = (idx, typ, price)
        else:
            cleaned.append((idx, typ, price))

    return cleaned


def _lux_channel_zigzag(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    length: int,
    debug: bool = False,
) -> List[Tuple[int, str, float]]:
    n = len(closes)
    if n < max(length + 2, 5):
        return []

    src = closes
    pivots: List[Tuple[int, str, float]] = []
    os_state: Optional[int] = None

    _log(debug, f"lux start | bars={n} | length={length}")

    for i in range(length, n):
        upper = float(np.max(src[i - length:i + 1]))
        lower = float(np.min(src[i - length:i + 1]))
        probe = float(src[i - length])

        prev_state = os_state

        if probe > upper:
            os_state = 0
        elif probe < lower:
            os_state = 1
        elif os_state is None:
            os_state = 0 if src[i] >= src[i - length] else 1

        if prev_state is None:
            continue

        btm = os_state == 1 and prev_state != 1
        top = os_state == 0 and prev_state != 0

        pivot_idx = i - length
        if pivot_idx < 0 or pivot_idx >= n:
            continue

        if btm:
            price = float(lows[pivot_idx])
            pivots.append((pivot_idx, "low", price))
            _log(debug, f"lux pivot LOW | idx={pivot_idx} | price={price:.2f}")

        elif top:
            price = float(highs[pivot_idx])
            pivots.append((pivot_idx, "high", price))
            _log(debug, f"lux pivot HIGH | idx={pivot_idx} | price={price:.2f}")

    cleaned = _dedupe_pivots(pivots)

    if not cleaned:
        hi_idx = int(np.argmax(highs))
        lo_idx = int(np.argmin(lows))
        if hi_idx < lo_idx:
            cleaned = [(hi_idx, "high", float(highs[hi_idx])), (lo_idx, "low", float(lows[lo_idx]))]
        else:
            cleaned = [(lo_idx, "low", float(lows[lo_idx])), (hi_idx, "high", float(highs[hi_idx]))]
        _log(debug, f"lux fallback pivots | {cleaned}")

    _log(debug, f"lux finished | pivots={len(cleaned)} | last={cleaned[-4:]}")
    return cleaned


def _reversal_zigzag(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    percent: float,
    confirmation_mode: str = "close",
    debug: bool = False,
) -> List[Tuple[int, str, float]]:
    n = len(highs)
    if n < 2:
        return []

    pivots: List[Tuple[int, str, float]] = []

    if closes[1] >= closes[0]:
        direction = "up"
        candidate_price = float(highs[1] if highs[1] >= highs[0] else highs[0])
        candidate_idx = 1 if highs[1] >= highs[0] else 0
    else:
        direction = "down"
        candidate_price = float(lows[1] if lows[1] <= lows[0] else lows[0])
        candidate_idx = 1 if lows[1] <= lows[0] else 0

    _log(debug, f"reversal start | bars={n} | percent={percent:.6f} | mode={confirmation_mode} | direction={direction}")

    for i in range(1, n):
        if direction == "up":
            if highs[i] >= candidate_price:
                candidate_price = float(highs[i])
                candidate_idx = i

            reversal_price = candidate_price * (1.0 - percent)
            confirmed = closes[i] <= reversal_price if confirmation_mode == "close" else lows[i] <= reversal_price

            if confirmed:
                pivots.append((candidate_idx, "high", candidate_price))
                _log(debug, f"reversal pivot HIGH | idx={candidate_idx} | price={candidate_price:.2f} | confirm_i={i}")
                direction = "down"
                candidate_price = float(lows[i])
                candidate_idx = i

        else:
            if lows[i] <= candidate_price:
                candidate_price = float(lows[i])
                candidate_idx = i

            reversal_price = candidate_price * (1.0 + percent)
            confirmed = closes[i] >= reversal_price if confirmation_mode == "close" else highs[i] >= reversal_price

            if confirmed:
                pivots.append((candidate_idx, "low", candidate_price))
                _log(debug, f"reversal pivot LOW | idx={candidate_idx} | price={candidate_price:.2f} | confirm_i={i}")
                direction = "up"
                candidate_price = float(highs[i])
                candidate_idx = i

    cleaned = _dedupe_pivots(pivots)

    if not cleaned:
        hi_idx = int(np.argmax(highs))
        lo_idx = int(np.argmin(lows))
        if hi_idx < lo_idx:
            cleaned = [(hi_idx, "high", float(highs[hi_idx])), (lo_idx, "low", float(lows[lo_idx]))]
        else:
            cleaned = [(lo_idx, "low", float(lows[lo_idx])), (hi_idx, "high", float(highs[hi_idx]))]
        _log(debug, f"reversal fallback pivots | {cleaned}")

    _log(debug, f"reversal finished | pivots={len(cleaned)} | last={cleaned[-4:]}")
    return cleaned


def _hybrid_atr_zigzag(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    current_price: float,
    atr_last: float,
    timeframe: str,
    symbol: Optional[str],
    base_percent: float,
    base_atr_mult: float,
    confirmation_mode: str = "close",
    debug: bool = False,
) -> Tuple[List[Tuple[int, str, float]], Dict[str, Any]]:
    n = len(highs)
    if n < 2:
        return [], {}

    regime = _volatility_regime(atr_last, current_price)
    inst_mult = _instrument_multiplier(symbol, regime)

    tf = timeframe.lower()
    tf_mult = 1.0
    if tf in ("15m", "15", "m15"):
        tf_mult = 0.90
    elif tf in ("1h", "1", "h1"):
        tf_mult = 1.00
    elif tf in ("4h", "4", "h4"):
        tf_mult = 1.08
    elif tf in ("1d", "d", "1D"):
        tf_mult = 1.15

    percent = _autotune_percent(timeframe, current_price, atr_last, base_percent, base_atr_mult)
    atr_mult = base_atr_mult * tf_mult * inst_mult

    reversal_from_percent = current_price * percent
    reversal_from_atr = atr_last * atr_mult

    # Weighted hybrid threshold. ATR dominates when market is volatile.
    if atr_last > 0 and current_price > 0:
        atr_weight = min(max((atr_last / current_price) * 50.0, 0.35), 0.80)
    else:
        atr_weight = 0.50

    reversal_range = (reversal_from_percent * (1.0 - atr_weight)) + (reversal_from_atr * atr_weight)

    _log(
        debug,
        f"hybrid setup | regime={regime} | symbol={symbol or 'n/a'} | "
        f"percent={percent:.6f} | atr_mult={atr_mult:.3f} | "
        f"rev_percent={reversal_from_percent:.2f} | rev_atr={reversal_from_atr:.2f} | "
        f"weight_atr={atr_weight:.2f} | rev_range={reversal_range:.2f}"
    )

    pivots: List[Tuple[int, str, float]] = []

    if closes[1] >= closes[0]:
        direction = "up"
        candidate_price = float(highs[1] if highs[1] >= highs[0] else highs[0])
        candidate_idx = 1 if highs[1] >= highs[0] else 0
    else:
        direction = "down"
        candidate_price = float(lows[1] if lows[1] <= lows[0] else lows[0])
        candidate_idx = 1 if lows[1] <= lows[0] else 0

    for i in range(1, n):
        if direction == "up":
            if highs[i] >= candidate_price:
                candidate_price = float(highs[i])
                candidate_idx = i

            reversal_price = candidate_price - reversal_range
            confirmed = closes[i] <= reversal_price if confirmation_mode == "close" else lows[i] <= reversal_price

            if confirmed:
                pivots.append((candidate_idx, "high", candidate_price))
                _log(debug, f"hybrid pivot HIGH | idx={candidate_idx} | price={candidate_price:.2f} | confirm_i={i}")
                direction = "down"
                candidate_price = float(lows[i])
                candidate_idx = i

        else:
            if lows[i] <= candidate_price:
                candidate_price = float(lows[i])
                candidate_idx = i

            reversal_price = candidate_price + reversal_range
            confirmed = closes[i] >= reversal_price if confirmation_mode == "close" else highs[i] >= reversal_price

            if confirmed:
                pivots.append((candidate_idx, "low", candidate_price))
                _log(debug, f"hybrid pivot LOW | idx={candidate_idx} | price={candidate_price:.2f} | confirm_i={i}")
                direction = "up"
                candidate_price = float(highs[i])
                candidate_idx = i

    cleaned = _dedupe_pivots(pivots)

    if not cleaned:
        hi_idx = int(np.argmax(highs))
        lo_idx = int(np.argmin(lows))
        if hi_idx < lo_idx:
            cleaned = [(hi_idx, "high", float(highs[hi_idx])), (lo_idx, "low", float(lows[lo_idx]))]
        else:
            cleaned = [(lo_idx, "low", float(lows[lo_idx])), (hi_idx, "high", float(highs[hi_idx]))]
        _log(debug, f"hybrid fallback pivots | {cleaned}")

    meta = {
        "regime": regime,
        "instrument_multiplier": round(inst_mult, 4),
        "tf_multiplier": round(tf_mult, 4),
        "reversal_from_percent": round(reversal_from_percent, 4),
        "reversal_from_atr": round(reversal_from_atr, 4),
        "atr_weight": round(atr_weight, 4),
        "reversal_range": round(reversal_range, 4),
        "resolved_percent": round(percent, 6),
        "resolved_atr_mult": round(atr_mult, 4),
    }

    _log(debug, f"hybrid finished | pivots={len(cleaned)} | last={cleaned[-4:]}")
    return cleaned, meta


def _build_zigzag_legs(pivots: List[Tuple[int, str, float]]) -> List[Dict[str, Any]]:
    legs: List[Dict[str, Any]] = []
    if len(pivots) < 2:
        return legs

    for i in range(1, len(pivots)):
        a = pivots[i - 1]
        b = pivots[i]
        legs.append(
            {
                "from_index": int(a[0]),
                "to_index": int(b[0]),
                "from_type": a[1],
                "to_type": b[1],
                "from_price": round(float(a[2]), 2),
                "to_price": round(float(b[2]), 2),
                "amplitude": round(abs(float(b[2]) - float(a[2])), 2),
                "bars": int(abs(int(b[0]) - int(a[0]))),
                "direction": "bullish" if a[1] == "low" and b[1] == "high" else "bearish",
            }
        )
    return legs


def _swing_direction_from_pivots(pivots: List[Tuple[int, str, float]]) -> str:
    if len(pivots) < 2:
        return "unknown"
    if pivots[-2][1] == "low" and pivots[-1][1] == "high":
        return "bullish"
    if pivots[-2][1] == "high" and pivots[-1][1] == "low":
        return "bearish"
    return "unknown"


def _infer_pattern_tags(legs: List[Dict[str, Any]], pivots: List[Tuple[int, str, float]]) -> List[str]:
    if len(pivots) < 4:
        return ["insufficient_pivots"]

    tags: List[str] = []

    if len(legs) >= 2:
        a1 = legs[-1]["amplitude"]
        a2 = legs[-2]["amplitude"]
        if a2 > 0 and abs(a1 - a2) / a2 <= 0.05:
            tags.append("ab_equal_cd")

    highs = [x for x in pivots if x[1] == "high"]
    lows = [x for x in pivots if x[1] == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1][2] > highs[-2][2] and lows[-1][2] > lows[-2][2]:
            tags.append("bullish_structure")
        elif highs[-1][2] < highs[-2][2] and lows[-1][2] < lows[-2][2]:
            tags.append("bearish_structure")

    if not tags:
        tags.append("no_clear_pattern")

    return tags


def _build_forecast(
    pivots: List[Tuple[int, str, float]],
    current_price: float,
    atr_last: float,
    swing_direction: str,
) -> Dict[str, Any]:
    if len(pivots) < 2:
        return {}

    last_swing_range = abs(float(pivots[-1][2]) - float(pivots[-2][2]))
    if last_swing_range <= 0:
        return {}

    bias = 1.0 if swing_direction == "bullish" else -1.0 if swing_direction == "bearish" else 0.0
    mid_target = round(current_price + bias * last_swing_range, 2) if bias != 0 else round(current_price, 2)

    return {
        "last_swing_range": round(last_swing_range, 2),
        "mid_target": mid_target,
        "range_low": round(mid_target - atr_last * 0.8, 2),
        "range_high": round(mid_target + atr_last * 0.8, 2),
    }


def _build_summary(
    timeframe: str,
    mode: str,
    swing_direction: str,
    price_position: Optional[float],
    current_price: float,
    atr_last: float,
    channel_width: Optional[float],
    pattern_tags: List[str],
    pivot_count: int,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    extra_part = ""
    if extra:
        extra_part = "; " + "; ".join(f"{k}={v}" for k, v in extra.items())

    return (
        f"TF={timeframe}; mode={mode}; dir={swing_direction}; pivots={pivot_count}; "
        f"pos={price_position}; price={round(current_price, 2)}; atr={round(atr_last, 6)}; "
        f"width={channel_width}; patterns={','.join(pattern_tags)}{extra_part}"
    )


def get_structural_extremums_zigzag(
    df: pd.DataFrame,
    timeframe: str = "1h",
    mode: str = "hybrid_atr",
    length: Optional[int] = None,
    percent: Optional[float] = None,
    confirmation_mode: str = "close",
    symbol: Optional[str] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    required_cols = {"high", "low", "close"}
    if not required_cols.issubset(df.columns):
        return {
            "upper": None,
            "lower": None,
            "global_channel": {"upper": None, "lower": None},
            "working_channel": {"upper": None, "lower": None},
            "zones": {"resistance": [], "support": []},
            "pivots": {"highs": [], "lows": []},
            "swing_direction": "unknown",
            "swing_points": [],
            "swing_legs": [],
            "channel_state": "unknown",
            "breakout_state": "unknown",
            "market_mode": "unknown",
            "price_position": None,
            "pattern_tags": [],
            "forecast": {},
            "params": {},
            "summary": "",
        }

    clean_df = df.copy()
    clean_df["high"] = pd.to_numeric(clean_df["high"], errors="coerce")
    clean_df["low"] = pd.to_numeric(clean_df["low"], errors="coerce")
    clean_df["close"] = pd.to_numeric(clean_df["close"], errors="coerce")
    clean_df = clean_df.dropna(subset=["high", "low", "close"]).reset_index(drop=True)

    if clean_df.empty:
        return {
            "upper": None,
            "lower": None,
            "global_channel": {"upper": None, "lower": None},
            "working_channel": {"upper": None, "lower": None},
            "zones": {"resistance": [], "support": []},
            "pivots": {"highs": [], "lows": []},
            "swing_direction": "unknown",
            "swing_points": [],
            "swing_legs": [],
            "channel_state": "unknown",
            "breakout_state": "unknown",
            "market_mode": "unknown",
            "price_position": None,
            "pattern_tags": [],
            "forecast": {},
            "params": {},
            "summary": "",
        }

    highs = clean_df["high"].to_numpy(dtype=float)
    lows = clean_df["low"].to_numpy(dtype=float)
    closes = clean_df["close"].to_numpy(dtype=float)

    atr = _compute_atr(clean_df, period=14)
    atr_last = float(atr[-1]) if len(atr) else 0.0
    current_price = float(closes[-1])

    base = _tf_params(timeframe)
    resolved_length = int(length if length is not None else base["length"])
    resolved_percent = float(
        percent if percent is not None else _autotune_percent(
            timeframe=timeframe,
            current_price=current_price,
            atr_last=atr_last,
            base_percent=float(base["percent"]),
            atr_mult=float(base["atr_mult"]),
        )
    )

    _log(
        debug,
        f"TF={timeframe} | mode={mode} | current={current_price:.2f} | ATR={atr_last:.4f} | "
        f"length={resolved_length} | percent={resolved_percent:.6f}"
    )

    extra_meta: Dict[str, Any] = {}

    if mode == "lux_channel":
        pivots = _lux_channel_zigzag(highs=highs, lows=lows, closes=closes, length=resolved_length, debug=debug)
    elif mode == "reversal":
        pivots = _reversal_zigzag(
            highs=highs,
            lows=lows,
            closes=closes,
            percent=resolved_percent,
            confirmation_mode=confirmation_mode,
            debug=debug,
        )
    elif mode == "hybrid_atr":
        pivots, extra_meta = _hybrid_atr_zigzag(
            highs=highs,
            lows=lows,
            closes=closes,
            current_price=current_price,
            atr_last=atr_last,
            timeframe=timeframe,
            symbol=symbol,
            base_percent=resolved_percent,
            base_atr_mult=float(base["atr_mult"]),
            confirmation_mode=confirmation_mode,
            debug=debug,
        )
    else:
        raise ValueError(f"Unsupported zigzag mode: {mode}")

    legs = _build_zigzag_legs(pivots)
    swing_direction = _swing_direction_from_pivots(pivots)

    global_upper = round(float(np.max(highs)), 2)
    global_lower = round(float(np.min(lows)), 2)

    if pivots:
        recent = pivots[-4:] if len(pivots) >= 4 else pivots
        recent_highs = [p[2] for p in recent if p[1] == "high"]
        recent_lows = [p[2] for p in recent if p[1] == "low"]

        if recent_highs and recent_lows:
            upper = float(max(recent_highs))
            lower = float(min(recent_lows))
        else:
            upper = float(max(p[2] for p in recent))
            lower = float(min(p[2] for p in recent))
    else:
        upper = float(global_upper)
        lower = float(global_lower)

    channel_width = round(upper - lower, 2) if upper > lower else None
    price_position = round((current_price - lower) / channel_width, 4) if channel_width and channel_width > 0 else None

    if price_position is None:
        channel_state = "unknown"
        breakout_state = "unknown"
    else:
        if price_position < 0:
            channel_state, breakout_state = "broken_down", "below_channel"
        elif price_position > 1:
            channel_state, breakout_state = "broken_up", "above_channel"
        elif price_position >= 0.7:
            channel_state, breakout_state = "upper_zone", "inside_upper_zone"
        elif price_position <= 0.3:
            channel_state, breakout_state = "lower_zone", "inside_lower_zone"
        else:
            channel_state, breakout_state = "mid_channel", "inside_channel"

    market_mode = "unknown"
    if swing_direction == "bullish":
        market_mode = (
            "bullish_breakout" if breakout_state == "above_channel"
            else "bullish_extension" if breakout_state == "inside_upper_zone"
            else "bullish_trend" if breakout_state == "inside_channel"
            else "bullish_context"
        )
    elif swing_direction == "bearish":
        market_mode = (
            "bearish_breakdown" if breakout_state == "below_channel"
            else "bearish_extension" if breakout_state == "inside_lower_zone"
            else "bearish_trend" if breakout_state == "inside_channel"
            else "bearish_context"
        )

    pattern_tags = _infer_pattern_tags(legs, pivots)
    forecast = _build_forecast(pivots, current_price, atr_last, swing_direction)

    pivot_highs = [idx for idx, typ, _ in pivots if typ == "high"]
    pivot_lows = [idx for idx, typ, _ in pivots if typ == "low"]

    swing_points = [{"index": int(i), "type": t, "price": round(float(p), 2)} for i, t, p in pivots[-12:]]

    summary = _build_summary(
        timeframe=timeframe,
        mode=mode,
        swing_direction=swing_direction,
        price_position=price_position,
        current_price=current_price,
        atr_last=atr_last,
        channel_width=channel_width,
        pattern_tags=pattern_tags,
        pivot_count=len(pivots),
        extra=extra_meta if extra_meta else None,
    )

    _log(debug, f"result | mode={mode} | dir={swing_direction} | pivots={len(pivots)} | channel={channel_state} | breakout={breakout_state} | market={market_mode}")
    _log(debug, f"last pivots={swing_points[-4:]}")
    _log(debug, f"patterns={pattern_tags} | forecast={forecast}")

    return {
        "upper": round(float(upper), 2),
        "lower": round(float(lower), 2),
        "global_channel": {"upper": global_upper, "lower": global_lower},
        "working_channel": {"upper": round(float(upper), 2), "lower": round(float(lower), 2)},
        "zones": {
            "resistance": [round(float(highs[i]), 2) for i in pivot_highs][-10:] or [global_upper],
            "support": [round(float(lows[i]), 2) for i in pivot_lows][-10:] or [global_lower],
        },
        "pivots": {
            "highs": pivot_highs[-10:],
            "lows": pivot_lows[-10:],
        },
        "swing_direction": swing_direction,
        "swing_points": swing_points,
        "swing_legs": legs[-12:],
        "channel_state": channel_state,
        "breakout_state": breakout_state,
        "market_mode": market_mode,
        "price_position": price_position,
        "pattern_tags": pattern_tags,
        "forecast": forecast,
        "params": {
            "mode": mode,
            "length": resolved_length,
            "percent": round(resolved_percent, 6),
            "confirmation_mode": confirmation_mode,
            "atr_mult": float(base["atr_mult"]),
            "atr_last": round(atr_last, 6),
            "current_price": round(current_price, 2),
            "global_upper": global_upper,
            "global_lower": global_lower,
            "pivot_count": len(pivots),
            **extra_meta,
        },
        "summary": summary,
    }


def analyze_multitimeframe(layers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ordered_tfs = [tf for tf in ["15m", "1h", "4h", "1d"] if tf in layers]
    directions = [layers[tf].get("swing_direction", "unknown") for tf in ordered_tfs]

    bullish = sum(1 for d in directions if d == "bullish")
    bearish = sum(1 for d in directions if d == "bearish")

    if bullish > bearish:
        stack_bias = "bullish"
    elif bearish > bullish:
        stack_bias = "bearish"
    else:
        stack_bias = "mixed"

    dominant_tf = None
    for candidate in ["1h", "4h", "15m", "1d"]:
        if candidate in layers:
            dominant_tf = candidate
            break

    alignment = "aligned" if bullish == 0 or bearish == 0 else "mixed"

    return {
        "stack_bias": stack_bias,
        "alignment": alignment,
        "dominant_tf": dominant_tf,
        "directions": {tf: layers[tf].get("swing_direction", "unknown") for tf in ordered_tfs},
        "market_modes": {tf: layers[tf].get("market_mode", "unknown") for tf in ordered_tfs},
        "breakout_states": {tf: layers[tf].get("breakout_state", "unknown") for tf in ordered_tfs},
        "channel_states": {tf: layers[tf].get("channel_state", "unknown") for tf in ordered_tfs},
        "summary": (
            f"stack_bias={stack_bias}; alignment={alignment}; dominant_tf={dominant_tf}; "
            + " ".join(f"{tf}:{layers[tf].get('swing_direction', 'unknown')}" for tf in ordered_tfs)
        ),
    }