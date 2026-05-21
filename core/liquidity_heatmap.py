# Назначение: упрощённая тепловая карта ликвидности и кластеризация уровней.
# Отвечает за: поиск плотных зон, оценку притяжения цены, отбой/подход/пробой.
# Связано с: auto_chart.py, ollama_client.py, scheduler.py.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float, np.number)):
            return float(value)
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            if not value:
                return None
            return float(value)
        return None
    except (TypeError, ValueError):
        return None


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = {"high", "low", "close", "volume"}
    if not required_cols.issubset(df.columns):
        return pd.DataFrame()

    out = df.copy()
    out["high"] = pd.to_numeric(out["high"], errors="coerce")
    out["low"] = pd.to_numeric(out["low"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
    out = out.dropna(subset=["high", "low", "close", "volume"]).reset_index(drop=True)
    return out


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1).fillna(close.iloc[0])
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _find_pivots(series: pd.Series, depth: int = 3, mode: str = "high") -> List[int]:
    vals = series.to_numpy(dtype=float)
    n = len(vals)
    if n < depth * 2 + 1:
        return []

    pivots: List[int] = []
    for i in range(depth, n - depth):
        left = vals[i - depth:i]
        right = vals[i + 1:i + depth + 1]
        if mode == "high":
            if vals[i] >= np.max(left) and vals[i] > np.max(right):
                pivots.append(i)
        else:
            if vals[i] <= np.min(left) and vals[i] < np.min(right):
                pivots.append(i)
    return pivots


def _cluster_levels(levels: List[float], tolerance: float) -> List[List[float]]:
    if not levels:
        return []

    levels = sorted(levels)
    clusters: List[List[float]] = [[levels[0]]]

    for level in levels[1:]:
        if abs(level - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    return clusters


def _cluster_center(cluster: List[float]) -> float:
    return float(np.mean(cluster)) if cluster else 0.0


def _cluster_spread(cluster: List[float]) -> float:
    if len(cluster) <= 1:
        return 0.0
    return float(max(cluster) - min(cluster))


def _cluster_strength(cluster: List[float]) -> float:
    """
    Простая сила зоны:
    - больше касаний = сильнее
    - меньше spread = сильнее
    """
    touches = len(cluster)
    spread = _cluster_spread(cluster)
    return round(touches * 1.0 + max(0.0, 4.0 - spread), 4)


def _nearest_distance(price: float, level: float) -> float:
    return abs(price - level)


def _zone_state(price: float, zone_low: float, zone_high: float, atr: float) -> str:
    if zone_low <= price <= zone_high:
        return "inside_zone"

    buffer_ = max(atr * 0.35, abs(price) * 0.001)
    if price < zone_low and (zone_low - price) <= buffer_:
        return "approach_from_below"
    if price > zone_high and (price - zone_high) <= buffer_:
        return "approach_from_above"

    if price < zone_low:
        return "below_zone"
    return "above_zone"


def _rejection_score(price: float, zone_low: float, zone_high: float, atr: float) -> float:
    """
    Чем ближе цена к зоне и чем дальше от неё после касания — тем выше score.
    """
    zone_mid = (zone_low + zone_high) / 2.0
    dist = abs(price - zone_mid)
    denom = max(atr * 2.5, abs(zone_mid) * 0.0025, 1e-9)
    raw = 1.0 - min(1.0, dist / denom)
    return round(max(0.0, raw), 4)


def _extract_swing_levels(df: pd.DataFrame, lookback: int = 60) -> Tuple[List[float], List[float]]:
    clean = df.tail(min(len(df), lookback)).reset_index(drop=True)
    highs = clean["high"]
    lows = clean["low"]

    high_pivots = _find_pivots(highs, depth=3, mode="high")
    low_pivots = _find_pivots(lows, depth=3, mode="low")

    swing_highs = [float(highs.iloc[i]) for i in high_pivots]
    swing_lows = [float(lows.iloc[i]) for i in low_pivots]

    # fallback на экстремумы, если pivots мало
    if not swing_highs:
        swing_highs = [float(highs.max())]
    if not swing_lows:
        swing_lows = [float(lows.min())]

    return swing_highs, swing_lows


def build_liquidity_heatmap(
    df: pd.DataFrame,
    current_price: Optional[float] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    lookback: int = 120,
    pivot_lookback: int = 60,
    cluster_tolerance_ratio: float = 0.0025,
    max_levels: int = 12,
) -> Dict[str, Any]:
    """
    Строит упрощённую heatmap-логику по уровням ликвидности.
    Подходит как отдельный context layer для будущей интеграции.

    Возвращает:
    - levels: сгруппированные зоны
    - dominant_bias: buy_side_liquidity / sell_side_liquidity / balanced
    - heatmap_comment: короткий человеческий комментарий
    """
    clean = _prepare_df(df)
    if clean.empty:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "current_price": _safe_float(current_price),
            "levels": [],
            "dominant_bias": "unknown",
            "heatmap_comment": "Недостаточно данных для построения heatmap.",
            "liquidity_density": 0.0,
            "liquidity_context": "unknown",
        }

    if current_price is None:
        current_price = _safe_float(clean["close"].iloc[-1])
    if current_price is None:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "current_price": None,
            "levels": [],
            "dominant_bias": "unknown",
            "heatmap_comment": "Не удалось определить текущую цену.",
            "liquidity_density": 0.0,
            "liquidity_context": "unknown",
        }

    work = clean.tail(min(len(clean), lookback)).reset_index(drop=True)
    atr_series = _compute_atr(work, period=14)
    atr_last = float(atr_series.iloc[-1]) if not atr_series.empty else max(current_price * 0.002, 1e-9)

    swing_highs, swing_lows = _extract_swing_levels(work, lookback=pivot_lookback)

    # Дополнительно добавим локальные high/low из последних свечей
    recent_highs = work["high"].tail(min(20, len(work))).tolist()
    recent_lows = work["low"].tail(min(20, len(work))).tolist()

    candidates = [*swing_highs, *swing_lows, *recent_highs, *recent_lows]
    candidates = [x for x in candidates if x is not None and np.isfinite(x)]

    if not candidates:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "current_price": round(float(current_price), 6),
            "levels": [],
            "dominant_bias": "unknown",
            "heatmap_comment": "Не удалось выделить уровни ликвидности.",
            "liquidity_density": 0.0,
            "liquidity_context": "unknown",
        }

    tolerance = max(current_price * cluster_tolerance_ratio, atr_last * 0.35, 1e-9)
    clusters = _cluster_levels(sorted(set(round(float(x), 6) for x in candidates)), tolerance=tolerance)

    zones: List[Dict[str, Any]] = []
    for cluster in clusters:
        center = _cluster_center(cluster)
        spread = _cluster_spread(cluster)
        strength = _cluster_strength(cluster)
        touches = len(cluster)

        zone_low = min(cluster)
        zone_high = max(cluster)

        # если кластер слишком узкий, расширим его до видимой зоны
        if spread < tolerance * 0.35:
            zone_low = center - tolerance * 0.5
            zone_high = center + tolerance * 0.5

        state = _zone_state(current_price, zone_low, zone_high, atr_last)
        rejection = _rejection_score(current_price, zone_low, zone_high, atr_last)

        kind = "mixed"
        if center > current_price:
            kind = "resistance"
        elif center < current_price:
            kind = "support"

        distance = _nearest_distance(current_price, center)
        density = round((touches * 1.0) + max(0.0, (atr_last / max(spread, 1e-9))), 4)

        zones.append(
            {
                "level": round(center, 6),
                "zone_low": round(zone_low, 6),
                "zone_high": round(zone_high, 6),
                "kind": kind,
                "touches": touches,
                "spread": round(spread, 6),
                "strength": strength,
                "distance": round(distance, 6),
                "state": state,
                "rejection_score": rejection,
                "density": density,
            }
        )

    zones.sort(
        key=lambda z: (
            z["rejection_score"],
            z["strength"],
            -z["distance"],
        ),
        reverse=True,
    )

    zones = zones[:max_levels]

    above_density = sum(z["density"] for z in zones if z["level"] > current_price)
    below_density = sum(z["density"] for z in zones if z["level"] < current_price)

    if above_density > below_density * 1.15:
        dominant_bias = "sell_side_liquidity"
        heatmap_comment = "Плотность ликвидности выше цены: вероятно притяжение к верхним уровням."
    elif below_density > above_density * 1.15:
        dominant_bias = "buy_side_liquidity"
        heatmap_comment = "Плотность ликвидности ниже цены: вероятно притяжение к нижним уровням."
    else:
        dominant_bias = "balanced"
        heatmap_comment = "Ликвидность распределена относительно сбалансированно."

    # Выделим ближайшую зону как основной контекст
    nearest_zone = min(zones, key=lambda z: z["distance"]) if zones else None
    if nearest_zone:
        if nearest_zone["state"] == "inside_zone":
            liquidity_context = "inside_pool"
        elif "approach" in nearest_zone["state"]:
            liquidity_context = "approach_pool"
        elif nearest_zone["kind"] == "support":
            liquidity_context = "support_pool"
        elif nearest_zone["kind"] == "resistance":
            liquidity_context = "resistance_pool"
        else:
            liquidity_context = "mixed_pool"
    else:
        liquidity_context = "unknown"

    density_total = sum(z["density"] for z in zones)
    liquidity_density = round(density_total / max(len(zones), 1), 4) if zones else 0.0

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": round(float(current_price), 6),
        "atr_last": round(float(atr_last), 6),
        "levels": zones,
        "dominant_bias": dominant_bias,
        "liquidity_density": liquidity_density,
        "liquidity_context": liquidity_context,
        "heatmap_comment": heatmap_comment,
        "nearest_zone": nearest_zone,
    }


def build_liquidity_context_text(heatmap: Dict[str, Any]) -> str:
    """
    Короткий текстовый контекст для LLM.
    """
    if not isinstance(heatmap, dict):
        return "Liquidity heatmap недоступна."

    current_price = heatmap.get("current_price", "N/A")
    dominant_bias = heatmap.get("dominant_bias", "unknown")
    comment = heatmap.get("heatmap_comment", "")

    nearest = heatmap.get("nearest_zone") or {}
    if isinstance(nearest, dict) and nearest:
        nearest_text = (
            f"Nearest {nearest.get('kind', 'mixed')} "
            f"@ {nearest.get('level', 'N/A')} "
            f"(state={nearest.get('state', 'N/A')}, strength={nearest.get('strength', 'N/A')})"
        )
    else:
        nearest_text = "Nearest zone: N/A"

    return (
        f"Price: {current_price} | "
        f"Dominant bias: {dominant_bias} | "
        f"Liquidity context: {heatmap.get('liquidity_context', 'unknown')} | "
        f"{nearest_text} | "
        f"{comment}"
    )