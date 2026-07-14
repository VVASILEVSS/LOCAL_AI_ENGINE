import ccxt
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple

# Adaptive pivot depth per timeframe — larger TF = wider window
_PIVOT_DEPTH: Dict[str, int] = {
    "1d": 3, "4h": 3, "1h": 3, "15m": 3, "5m": 4,
}
# Minimum inter-pivot distance as ATR multiplier
_PIVOT_ATR_K: float = 0.5

# Structural window per TF for pivot detection (T2: top-down).
# Younger TFs use a window to focus on ~2 structural movements.
_STRUCT_WINDOW: Dict[str, Optional[int]] = {"5m": 50, "15m": 50, "1h": 80, "4h": None, "1d": None}

# Top-down TF order (oldest to youngest)
_TOPDOWN_TF_ORDER: List[str] = ["1d", "4h", "1h", "15m", "5m"]

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


def _find_real_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    depth: int = 3,
    min_atr_distance: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Реальные swing highs/lows через локальный экстремум.
    Candle[i] = pivot high если highs[i] = max(highs[i-depth:i+depth+1]).
    Аналогично для pivot lows.

    Args:
        highs: массив high цен
        lows: массив low цен
        depth: окно поиска (адаптивно по ТФ)
        min_atr_distance: минимальное расстояние между пивотами (в ценах).
            Фильтрует шум в боковике. 0 = без фильтра.

    Returns:
        Список пивотов [{"index": int, "type": "high"|"low", "price": float}]
        отсортированных по index.
    """
    pivots_h: List[Tuple[int, float]] = []
    pivots_l: List[Tuple[int, float]] = []
    n = len(highs)

    for i in range(depth, n - depth):
        window_h = highs[i - depth : i + depth + 1]
        window_l = lows[i - depth : i + depth + 1]
        if highs[i] == np.max(window_h):
            pivots_h.append((i, float(highs[i])))
        if lows[i] == np.min(window_l):
            pivots_l.append((i, float(lows[i])))

    # Merge + sort by index
    all_pivots: List[Dict[str, Any]] = []
    for idx, price in pivots_h:
        all_pivots.append({"index": int(idx), "type": "high", "price": round(price, 2)})
    for idx, price in pivots_l:
        all_pivots.append({"index": int(idx), "type": "low", "price": round(price, 2)})
    all_pivots.sort(key=lambda p: p["index"])

    # Dedup: подряд идущие пивоты одного типа с разницей < 0.1% → оставляем экстремум
    deduped: List[Dict[str, Any]] = []
    for p in all_pivots:
        if deduped:
            last = deduped[-1]
            if (last["type"] == p["type"]
                    and last["price"] > 0
                    and abs(p["price"] - last["price"]) / last["price"] < 0.001):
                # Оставляем тот, что экстремальнее
                if p["type"] == "high" and p["price"] > last["price"]:
                    deduped[-1] = p
                elif p["type"] == "low" and p["price"] < last["price"]:
                    deduped[-1] = p
                continue
        deduped.append(p)

    # ATR distance filter: если два соседних пивота ближе чем min_atr_distance →
    # удаляем менее значимый (ближе к центру диапазона)
    if min_atr_distance > 0 and len(deduped) > 2:
        filtered: List[Dict[str, Any]] = [deduped[0]]
        for i in range(1, len(deduped)):
            prev = filtered[-1]
            curr = deduped[i]
            dist = abs(curr["price"] - prev["price"])
            if dist < min_atr_distance:
                # Удаляем тот, чья цена ближе к среднему между prev-prev и curr-next
                # (если есть соседи)
                if len(filtered) >= 2:
                    mid = (filtered[-2]["price"] + curr["price"]) / 2
                else:
                    mid = curr["price"]
                if abs(prev["price"] - mid) < abs(curr["price"] - mid):
                    filtered[-1] = curr  # prev ближе к центру → заменяем
                # else: curr ближе к центру → skip curr
            else:
                filtered.append(curr)
        deduped = filtered

    return deduped


def _derive_swing_direction(pivots: List[Dict[str, Any]], current_price: float) -> str:
    """Определяет направление на основе последних 2-3 реальных пивотов.

    Если последний pivot = high и цена ниже него → bearish (откат от сопротивления).
    Если последний pivot = low и цена выше него → bullish (отскок от поддержки).
    """
    if len(pivots) < 2:
        return "sideways"

    last = pivots[-1]
    prev = pivots[-2]

    # Последний пивот = high → цена под ним = медвежий откат
    if last["type"] == "high":
        return "bearish" if current_price < last["price"] else "bullish"
    # Последний пивот = low → цена над ним = бычий отскок
    if last["type"] == "low":
        return "bullish" if current_price > last["price"] else "bearish"

    # Fallback: по тренду последних пивотов
    if len(pivots) >= 3:
        if (pivots[-1]["price"] > pivots[-2]["price"] > pivots[-3]["price"]):
            return "bullish"
        if (pivots[-1]["price"] < pivots[-2]["price"] < pivots[-3]["price"]):
            return "bearish"

    return "sideways"


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

    # ── Phase 1: Fetch data + detect pivots for all TFs ──
    # Собираем сырые данные: пивоты, цены, closes, ATR.
    # Структура анализа (BOS, zone, accumulation) — в Phase 2 через analyze_topdown().
    tf_raw: Dict[str, Dict[str, Any]] = {}  # tf → {swing_points, current_price, closes, atr, ...}

    for tf in timeframes:
        try:
            tf_norm = tf.lower().strip()
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
        atr = pd.Series(highs - lows).rolling(14, min_periods=1).mean().iloc[-1]
        regime = _regime_from_atr(current_price, float(atr))
        instr_mult = _instrument_multiplier(symbol)
        tf_mult = _tf_multiplier(tf)

        depth = _PIVOT_DEPTH.get(tf_norm, 3)
        min_atr_dist = float(atr) * _PIVOT_ATR_K if atr > 0 else 0.0

        # Структурное окно: для младших ТФ — последние N свечей
        window = _STRUCT_WINDOW.get(tf_norm)
        if window and len(df) > window:
            pivot_highs_arr = highs[-window:]
            pivot_lows_arr = lows[-window:]
            pivot_closes = list(closes[-window:])
        else:
            pivot_highs_arr = highs
            pivot_lows_arr = lows
            pivot_closes = list(closes)

        swing_points = _find_real_pivots(pivot_highs_arr, pivot_lows_arr, depth=depth, min_atr_distance=min_atr_dist)

        tf_raw[tf] = {
            "swing_points": swing_points,
            "current_price": current_price,
            "closes": pivot_closes,
            "total_candles": window if window else len(df),
            "atr": float(atr),
            "regime": regime,
            "instr_mult": instr_mult,
            "tf_mult": tf_mult,
            "highs": highs,
            "lows": lows,
            "df_len": len(df),
        }

    # ── Phase 2: Top-down structural analysis (T2) ──
    # analyze_topdown() вызывается ОДИН раз, передаёт parent_zone по цепочке D1→H4→H1→M15→5M.
    from core.structure import analyze_topdown, format_structure_narrative

    # Подготавливаем данные в формате для analyze_topdown
    topdown_input = {}
    for tf, raw in tf_raw.items():
        topdown_input[tf.lower()] = {
            "swing_points": raw["swing_points"],
            "current_price": raw["current_price"],
            "closes": raw["closes"],
            "total_candles": raw["total_candles"],
        }

    # Определяем порядок: только те ТФ что есть в данных, в topdown порядке
    available_tfs = [t for t in _TOPDOWN_TF_ORDER if t in topdown_input]
    # Добавляем ТФ которых нет в стандартном порядке (если пользователь передал кастомные)
    for tf in timeframes:
        if tf.lower() not in available_tfs and tf.lower() in topdown_input:
            available_tfs.append(tf.lower())

    struct_results = analyze_topdown(topdown_input, tf_order=available_tfs)

    # ── Phase 3: Build tf_results from top-down analysis ──
    tf_results: Dict[str, Dict[str, Any]] = {}

    for tf in timeframes:
        raw = tf_raw.get(tf)
        if not raw:
            continue

        tf_lower = tf.lower().strip()
        struct = struct_results.get(tf_lower)
        current_price = raw["current_price"]
        atr = raw["atr"]
        instr_mult = raw["instr_mult"]
        tf_mult = raw["tf_mult"]
        regime = raw["regime"]

        # Zone из top-down structure (или fallback)
        if struct and struct.zone_high and struct.zone_low:
            upper = struct.zone_high
            lower = struct.zone_low
            swing_direction = struct.swing_direction
        else:
            # Fallback: raw extremes
            upper = float(np.max(raw["highs"]))
            lower = float(np.min(raw["lows"]))
            swing_points_fb = raw["swing_points"]
            swing_direction = _derive_swing_direction(swing_points_fb, current_price) if len(swing_points_fb) >= 2 else "sideways"

        pivot_count = len(raw["swing_points"])
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

        market_bias = swing_direction
        market_mode = _classify_structure(swing_direction, price_position, tf, market_bias)
        tags = _pattern_tags(swing_direction, price_position, breakout_state, channel_state, market_bias)

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

        # Build structure_info from StructureAnalysis dataclass
        structure_info = None
        structure_narrative = ""
        if struct:
            structure_narrative = format_structure_narrative(struct, current_price)
            structure_info = {
                "bos": {
                    "direction": struct.bos.direction,
                    "price": round(struct.bos.broken_level, 1),
                } if struct.bos else None,
                "prev_structure": {
                    "direction": struct.prev_structure.direction,
                    "high": round(struct.prev_structure.high, 1),
                    "low": round(struct.prev_structure.low, 1),
                    "pivot_count": struct.prev_structure.pivot_count,
                    "candle_count": struct.prev_structure.candle_count,
                } if struct.prev_structure else None,
                "curr_structure": {
                    "direction": struct.curr_structure.direction,
                    "high": round(struct.curr_structure.high, 1),
                    "low": round(struct.curr_structure.low, 1),
                    "pivot_count": struct.curr_structure.pivot_count,
                    "candle_count": struct.curr_structure.candle_count,
                } if struct.curr_structure else None,
                "narrative": structure_narrative,
                "is_accumulation": struct.is_accumulation,
                "accumulation_pivot_count": struct.accumulation_pivot_count,
                "targets": struct.targets,
                "parent_tf": struct.parent_tf,
                "chain_broken": struct.chain_broken,
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
            "structure": structure_info,
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
            "swing_points": raw["swing_points"],
            "zones": zones,
            "structure": structure_info,
            "structure_narrative": structure_narrative,
            "meta": {
                "regime": regime,
                "instrument_multiplier": instr_mult,
                "tf_multiplier": tf_mult,
            },
        }

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