# core/liquidity_magnet.py
# Назначение: строит liquidity pools, shared extremum и магнит вероятности для зон ликвидности.
# Отвечает за: поиск pivot high/low, clustering equal highs/lows, оценку притяжения цены к магнитным зонам.
# Связан с: core/auto_chart.py, core/state_tracker.py, core/ollama_client.py, core/handlers.py.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class LiquidityPool:
    price: float
    bar_index: int
    is_high: bool
    is_equal: bool = False
    volume_at: float = 0.0
    touches: int = 1
    score: float = 0.0
    probability: float = 0.0
    tf: str = ""
    tfs: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.tfs is None:
            self.tfs = []


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            if math.isnan(float(value)):
                return None
            return float(value)
        if isinstance(value, str):
            s = value.strip().replace(",", ".")
            if not s:
                return None
            return float(s)
        return None
    except (TypeError, ValueError):
        return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "").upper().strip()


def _normalize_tf(tf: str) -> str:
    t = str(tf).strip().upper()
    mapping = {
        "M15": "15M",
        "15": "15M",
        "15MIN": "15M",
        "15M": "15M",
        "H1": "1H",
        "1": "1H",
        "1H": "1H",
        "H4": "4H",
        "4": "4H",
        "4H": "4H",
        "D1": "1D",
        "1D": "1D",
        "DAY1": "1D",
    }
    return mapping.get(t, t)


def _tf_order(tf: str) -> int:
    order = {"15M": 0, "1H": 1, "4H": 2, "1D": 3}
    return order.get(_normalize_tf(tf), 99)


def _log_distance(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 1e9
    return abs(math.log(a / b))


def _cluster_levels(levels: List[float], tolerance_pct: float) -> List[Dict[str, Any]]:
    if not levels:
        return []

    levels = sorted(float(x) for x in levels if x is not None)
    clusters: List[Dict[str, Any]] = []

    for level in levels:
        placed = False
        for c in clusters:
            center = float(c["center"])
            tol = max(center * tolerance_pct, 1e-9)
            if abs(level - center) <= tol:
                c["levels"].append(level)
                c["center"] = sum(c["levels"]) / len(c["levels"])
                placed = True
                break
        if not placed:
            clusters.append({"center": level, "levels": [level]})

    for c in clusters:
        c["count"] = len(c["levels"])
        c["spread"] = round(max(c["levels"]) - min(c["levels"]), 6) if len(c["levels"]) > 1 else 0.0
        c["strength"] = round(c["count"] + max(0.0, 3.0 - c["spread"]), 3)

    clusters.sort(key=lambda x: (x["count"], x["strength"]), reverse=True)
    return clusters


def _extract_ohlc(df: pd.DataFrame) -> Tuple[List[float], List[float], List[float], List[float]]:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas.DataFrame")

    req = {"high", "low", "close", "volume"}
    if not req.issubset(df.columns):
        missing = ", ".join(sorted(req - set(df.columns)))
        raise ValueError(f"Missing required columns: {missing}")

    clean = df.copy()
    clean["high"] = pd.to_numeric(clean["high"], errors="coerce")
    clean["low"] = pd.to_numeric(clean["low"], errors="coerce")
    clean["close"] = pd.to_numeric(clean["close"], errors="coerce")
    clean["volume"] = pd.to_numeric(clean["volume"], errors="coerce")
    clean = clean.dropna(subset=["high", "low", "close"]).reset_index(drop=True)

    highs = clean["high"].astype(float).tolist()
    lows = clean["low"].astype(float).tolist()
    closes = clean["close"].astype(float).tolist()
    volumes = clean["volume"].fillna(0.0).astype(float).tolist()
    return highs, lows, closes, volumes


def _pick_tf_auto_params(timeframe: str) -> Tuple[int, float, float]:
    tf = _normalize_tf(timeframe)
    if tf == "1D":
        return 7, 0.08, 0.65
    if tf == "4H":
        return 5, 0.05, 0.45
    if tf == "1H":
        return 4, 0.008, 0.30
    if tf == "15M":
        return 3, 0.005, 0.18
    return 4, 0.008, 0.30


def _find_pivots(highs: List[float], lows: List[float], depth: int) -> Tuple[List[int], List[int]]:
    ph: List[int] = []
    pl: List[int] = []
    n = min(len(highs), len(lows))
    if n < depth * 2 + 1:
        return ph, pl

    for i in range(depth, n - depth):
        if highs[i] > max(highs[i - depth:i]) and highs[i] >= max(highs[i + 1:i + depth + 1]):
            ph.append(i)
        if lows[i] < min(lows[i - depth:i]) and lows[i] <= min(lows[i + 1:i + depth + 1]):
            pl.append(i)
    return ph, pl


def _build_pool_dict(
    price: float,
    bar_index: int,
    is_high: bool,
    is_equal: bool,
    volume_at: float,
    touches: int,
    score: float,
    probability: float,
    tf: str,
    shared_extremum: bool = False,
) -> Dict[str, Any]:
    return {
        "price": round(price, 6),
        "bar_index": int(bar_index),
        "is_high": bool(is_high),
        "is_equal": bool(is_equal),
        "shared_extremum": bool(shared_extremum),
        "volume_at": round(float(volume_at), 6),
        "touches": int(touches),
        "score": round(float(score), 6),
        "probability": round(float(probability), 4),
        "tf": _normalize_tf(tf),
    }


def _log_debug(debug: bool, message: str, *args: Any) -> None:
    if debug:
        logger.info(message, *args)


def build_liquidity_magnet(
    df: pd.DataFrame,
    timeframe: str = "1H",
    symbol: str = "",
    pivot_len: Optional[int] = None,
    eq_tolerance_pct: Optional[float] = None,
    max_pools: int = 25,
    min_age: int = 3,
    max_age: int = 500,
    log_distance_cap: Optional[float] = None,
    w_strength: float = 1.0,
    w_proximity: float = 1.2,
    w_age: float = 0.6,
    w_momentum: float = 0.9,
    volume_weight: float = 1.0,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Builds liquidity pools and magnetic targets from OHLCV.
    Returns a compact context suitable for prompt/rules/UI.
    """
    highs, lows, closes, volumes = _extract_ohlc(df)
    tf = _normalize_tf(timeframe)
    symbol_norm = _normalize_symbol(symbol) if symbol else ""

    _log_debug(debug, "[LM] start symbol=%s tf=%s candles=%s", symbol_norm, tf, len(closes))

    if len(closes) < 10:
        last_price = closes[-1] if closes else None
        return {
            "symbol": symbol_norm,
            "timeframe": tf,
            "last_price": last_price,
            "magnet_pull": "neutral",
            "top_target": None,
            "top_probability": None,
            "distance_pct": None,
            "proximity_pct": None,
            "active_pools": 0,
            "equal_total_pct": 0.0,
            "historical_touch_pct": None,
            "shared_highs": [],
            "shared_lows": [],
            "pools": [],
            "top3": [],
            "hierarchy": {},
            "debug_info": {
                "pivot_highs": 0,
                "pivot_lows": 0,
                "high_clusters": 0,
                "low_clusters": 0,
                "candidate_pools": 0,
                "merged_pools": 0,
                "pruned_pools": 0,
                "score_up": 0.0,
                "score_dn": 0.0,
            },
            "summary": "Недостаточно данных.",
        }

    depth_default, eq_default, dist_default = _pick_tf_auto_params(tf)
    depth = int(pivot_len or depth_default)
    eq_pct = float(eq_tolerance_pct if eq_tolerance_pct is not None else eq_default)
    dist_cap = float(log_distance_cap if log_distance_cap is not None else dist_default)

    pivot_highs, pivot_lows = _find_pivots(highs, lows, depth)
    if not pivot_highs:
        pivot_highs = [int(max(range(len(highs)), key=lambda i: highs[i]))]
    if not pivot_lows:
        pivot_lows = [int(min(range(len(lows)), key=lambda i: lows[i]))]

    _log_debug(debug, "[LM] pivots highs=%s lows=%s depth=%s", len(pivot_highs), len(pivot_lows), depth)

    high_clusters = _cluster_levels([highs[i] for i in pivot_highs], eq_pct)
    low_clusters = _cluster_levels([lows[i] for i in pivot_lows], eq_pct)

    shared_highs: List[float] = []
    shared_lows: List[float] = []
    for c in high_clusters:
        if c["count"] >= 2:
            shared_highs.append(round(float(c["center"]), 6))
    for c in low_clusters:
        if c["count"] >= 2:
            shared_lows.append(round(float(c["center"]), 6))

    _log_debug(
        debug,
        "[LM] clusters high=%s low=%s shared_highs=%s shared_lows=%s",
        len(high_clusters),
        len(low_clusters),
        shared_highs,
        shared_lows,
    )

    last_price = float(closes[-1])
    vol_ma = sum(volumes[-50:]) / max(min(len(volumes), 50), 1)
    mom_raw = (sum(closes[-8:]) / min(len(closes), 8)) - (sum(closes[-21:]) / min(len(closes), 21))
    mom_norm = mom_raw / max(last_price, 1e-9)

    candidate_pools: List[LiquidityPool] = []

    def add_pool(price: float, bar_index: int, is_high: bool, volume_at: float, touches: int = 1, is_equal: bool = False):
        candidate_pools.append(
            LiquidityPool(
                price=price,
                bar_index=bar_index,
                is_high=is_high,
                is_equal=is_equal,
                volume_at=volume_at,
                touches=touches,
                tf=tf,
            )
        )

    for i in pivot_highs:
        add_pool(highs[i], i, True, volumes[i], touches=1, is_equal=False)
    for i in pivot_lows:
        add_pool(lows[i], i, False, volumes[i], touches=1, is_equal=False)

    _log_debug(debug, "[LM] candidate_pools=%s", len(candidate_pools))

    merged: List[LiquidityPool] = []
    for p in sorted(candidate_pools, key=lambda x: (_tf_order(x.tf), x.is_high, x.price, x.bar_index)):
        matched = False
        for m in merged:
            if m.is_high != p.is_high:
                continue
            tol = max(m.price * eq_pct, 1e-9)
            if abs(m.price - p.price) <= tol:
                m.price = (m.price * m.touches + p.price) / (m.touches + 1)
                m.bar_index = min(m.bar_index, p.bar_index)
                m.volume_at += p.volume_at
                m.touches += 1
                m.is_equal = True
                matched = True
                break
        if not matched:
            merged.append(p)

    _log_debug(debug, "[LM] merged_pools=%s", len(merged))

    pruned: List[LiquidityPool] = []
    for p in merged:
        age = len(closes) - 1 - p.bar_index
        if age < min_age or age > max_age:
            _log_debug(debug, "[LM] prune age price=%s age=%s", p.price, age)
            continue
        if _log_distance(p.price, last_price) > dist_cap:
            _log_debug(debug, "[LM] prune distance price=%s last=%s", p.price, last_price)
            continue
        pruned.append(p)

    if not pruned:
        pruned = merged[:]

    for p in pruned:
        age = len(closes) - 1 - p.bar_index
        ldist = _log_distance(p.price, last_price)

        vol_factor = min((p.volume_at / max(vol_ma, 1e-9)), 3.0) if vol_ma > 0 else 1.0
        eq_mult = 1.7 if p.is_equal else 1.0
        strength = vol_factor * eq_mult * volume_weight

        prox = 0.0
        if ldist < 0.002:
            prox = 0.3
        elif ldist < 0.05:
            prox = max(0.5, 1.0 - abs(ldist - 0.02) * 10.0)
        else:
            prox = max(0.05, 1.0 - (ldist - 0.05) * 3.0)

        if age < min_age:
            age_score = 0.5
        elif age < 50:
            age_score = 1.0
        else:
            age_score = max(0.1, 1.0 - (age - 50) / 450.0)

        dir_to_pool = 1.0 if p.price > last_price else -1.0
        mom_align = dir_to_pool * mom_norm * 100.0
        mom_score = 1.0 + max(-0.5, min(0.5, mom_align))

        raw_score = strength * w_strength + prox * w_proximity + age_score * w_age + mom_score * w_momentum
        p.score = max(raw_score, 0.01)

        _log_debug(
            debug,
            "[LM] score price=%s is_high=%s equal=%s age=%s vol_factor=%.4f prox=%.4f age_score=%.4f mom_score=%.4f raw=%.4f",
            p.price,
            p.is_high,
            p.is_equal,
            age,
            vol_factor,
            prox,
            age_score,
            mom_score,
            p.score,
        )

    total_score = sum(p.score for p in pruned) or 1.0
    for p in pruned:
        p.probability = (p.score / total_score) * 100.0

    score_up = sum(p.score for p in pruned if p.is_high)
    score_dn = sum(p.score for p in pruned if not p.is_high)

    magnet_pull = "neutral"
    if score_up > score_dn * 1.05:
        magnet_pull = "bullish"
    elif score_dn > score_up * 1.05:
        magnet_pull = "bearish"

    top_pool = max(pruned, key=lambda x: x.score) if pruned else None
    top_target = top_pool.price if top_pool else None
    top_probability = top_pool.probability if top_pool else None
    top3_sorted = sorted(pruned, key=lambda x: x.score, reverse=True)[:3]

    hierarchy: Dict[str, Any] = {
        "tf": tf,
        "parent_tf": None,
        "child_tfs": [],
        "shared_highs": shared_highs,
        "shared_lows": shared_lows,
    }

    if tf == "15M":
        hierarchy["parent_tf"] = "1H"
    elif tf == "1H":
        hierarchy["parent_tf"] = "4H"
    elif tf == "4H":
        hierarchy["parent_tf"] = "1D"

    active_pools = len(pruned)
    equal_total = sum(1 for p in pruned if p.is_equal)
    equal_total_pct = round((equal_total / active_pools) * 100.0, 2) if active_pools else 0.0

    touch_like = sum(1 for p in pruned if p.is_equal or p.touches > 1)
    historical_touch_pct = round((touch_like / active_pools) * 100.0, 2) if active_pools else None

    if top_target is not None:
        distance_pct = round((top_target - last_price) / last_price * 100.0, 2)
        proximity_pct = round(max(0.0, min(100.0, 100.0 * (1 - _log_distance(last_price, top_target) / max(dist_cap, 1e-9)))), 2)
    else:
        distance_pct = None
        proximity_pct = None

    pools_out = [
        _build_pool_dict(
            price=p.price,
            bar_index=p.bar_index,
            is_high=p.is_high,
            is_equal=p.is_equal,
            volume_at=p.volume_at,
            touches=p.touches,
            score=p.score,
            probability=p.probability,
            tf=tf,
            shared_extremum=False,
        )
        for p in pruned
    ]

    for p in pools_out:
        if p["is_high"] and any(abs(p["price"] - x) <= max(p["price"] * eq_pct, 1e-9) for x in shared_highs):
            p["shared_extremum"] = True
        elif (not p["is_high"]) and any(abs(p["price"] - x) <= max(p["price"] * eq_pct, 1e-9) for x in shared_lows):
            p["shared_extremum"] = True
        else:
            p["shared_extremum"] = False

    _log_debug(
        debug,
        "[LM] top_target=%s top_probability=%s pull=%s active_pools=%s score_up=%.4f score_dn=%.4f",
        top_target,
        top_probability,
        magnet_pull,
        active_pools,
        score_up,
        score_dn,
    )

    summary = (
        f"{tf}: pull={magnet_pull}; top={top_target}; prob={top_probability}; "
        f"pools={active_pools}; eq={equal_total_pct}%"
    )

    debug_info = {
        "pivot_highs": len(pivot_highs),
        "pivot_lows": len(pivot_lows),
        "high_clusters": len(high_clusters),
        "low_clusters": len(low_clusters),
        "candidate_pools": len(candidate_pools),
        "merged_pools": len(merged),
        "pruned_pools": len(pruned),
        "score_up": round(score_up, 6),
        "score_dn": round(score_dn, 6),
        "top_idx": int(pruned.index(top_pool)) if top_pool in pruned else -1,
        "depth": depth,
        "eq_pct": eq_pct,
        "dist_cap": dist_cap,
    }

    return {
        "symbol": symbol_norm,
        "timeframe": tf,
        "last_price": round(last_price, 6),
        "magnet_pull": magnet_pull,
        "top_target": round(top_target, 6) if top_target is not None else None,
        "top_probability": round(top_probability, 4) if top_probability is not None else None,
        "distance_pct": distance_pct,
        "proximity_pct": proximity_pct,
        "active_pools": active_pools,
        "equal_total_pct": equal_total_pct,
        "historical_touch_pct": historical_touch_pct,
        "shared_highs": shared_highs,
        "shared_lows": shared_lows,
        "pools": pools_out,
        "top3": [
            {
                "price": round(p.price, 6),
                "probability": round(p.probability, 4),
                "is_high": p.is_high,
                "is_equal": p.is_equal,
                "shared_extremum": True if p.is_equal else False,
            }
            for p in top3_sorted
        ],
        "hierarchy": hierarchy,
        "debug_info": debug_info,
        "summary": summary,
    }


def build_liquidity_magnet_from_zones(
    zones_by_tf: Dict[str, Dict[str, Any]],
    current_price: float,
    symbol: str = "",
    eq_tolerance_pct: float = 0.15,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Optional helper: build liquidity context from precomputed tf zones.
    Expected input:
      {
        "15M": {"upper": ..., "lower": ...},
        "1H": {"upper": ..., "lower": ...},
        "4H": {"upper": ..., "lower": ...},
      }
    """
    pools: List[Dict[str, Any]] = []
    shared_highs: List[float] = []
    shared_lows: List[float] = []

    norm_items: List[Tuple[str, Dict[str, Any]]] = []
    for tf, z in zones_by_tf.items():
        if not isinstance(z, dict):
            continue
        tf_n = _normalize_tf(tf)
        upper = _safe_float(z.get("upper"))
        lower = _safe_float(z.get("lower"))
        if upper is None and lower is None:
            continue
        if upper is not None and lower is not None and lower > upper:
            lower, upper = upper, lower
        norm_items.append((tf_n, {"upper": upper, "lower": lower}))

    lows = [z["lower"] for _, z in norm_items if z.get("lower") is not None]
    highs = [z["upper"] for _, z in norm_items if z.get("upper") is not None]

    low_clusters = _cluster_levels([x for x in lows if x is not None], eq_tolerance_pct / 100.0)
    high_clusters = _cluster_levels([x for x in highs if x is not None], eq_tolerance_pct / 100.0)

    for c in low_clusters:
        if c["count"] >= 2:
            shared_lows.append(round(float(c["center"]), 6))
    for c in high_clusters:
        if c["count"] >= 2:
            shared_highs.append(round(float(c["center"]), 6))

    for tf, z in norm_items:
        if z.get("lower") is not None:
            pools.append(
                {
                    "tf": tf,
                    "price": z["lower"],
                    "is_high": False,
                    "is_equal": any(abs(z["lower"] - s) <= max(z["lower"] * (eq_tolerance_pct / 100.0), 1e-9) for s in shared_lows),
                    "shared_extremum": any(abs(z["lower"] - s) <= max(z["lower"] * (eq_tolerance_pct / 100.0), 1e-9) for s in shared_lows),
                }
            )
        if z.get("upper") is not None:
            pools.append(
                {
                    "tf": tf,
                    "price": z["upper"],
                    "is_high": True,
                    "is_equal": any(abs(z["upper"] - s) <= max(z["upper"] * (eq_tolerance_pct / 100.0), 1e-9) for s in shared_highs),
                    "shared_extremum": any(abs(z["upper"] - s) <= max(z["upper"] * (eq_tolerance_pct / 100.0), 1e-9) for s in shared_highs),
                }
            )

    if debug:
        _log_debug(debug, "[LM] zones shared_highs=%s shared_lows=%s pools=%s", shared_highs, shared_lows, len(pools))

    return {
        "symbol": _normalize_symbol(symbol) if symbol else "",
        "current_price": current_price,
        "shared_highs": shared_highs,
        "shared_lows": shared_lows,
        "pools": pools,
        "summary": "Liquidity pools built from zones.",
    }