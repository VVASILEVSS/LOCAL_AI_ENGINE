# Назначение: интерпретируемый объёмный A/D-контекст для LOCAL_AI_ENGINE.
# Отвечает за: bullish/bearish/neutral bias, volume confirmation, divergence, strength score и краткий комментарий.
# Связан с: auto_chart.py, ollama_client.py, wyckoff/market_phase и liquidity layer.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


EPS = 1e-12


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float, np.integer, np.floating)):
            return float(value)
        s = str(value).strip().replace(",", ".")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _to_numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _normalize_tf(timeframe: str) -> str:
    return str(timeframe).strip().lower()


def _tf_weights(timeframe: str) -> float:
    tf = _normalize_tf(timeframe)
    if tf in ("1d", "d1", "day"):
        return 4.0
    if tf in ("4h", "h4", "240"):
        return 3.0
    if tf in ("1h", "h1", "60"):
        return 2.0
    if tf in ("15m", "m15", "15"):
        return 1.0
    return 1.5


def _regime_from_atr(price: float, atr_val: float) -> str:
    if price <= 0 or atr_val <= 0:
        return "transition"
    ratio = atr_val / price
    if ratio < 0.005:
        return "flat"
    if ratio < 0.015:
        return "transition"
    return "volatile"


def _compute_cmf_like(df: pd.DataFrame, length: int = 20) -> pd.Series:
    high = _to_numeric_series(df, "high")
    low = _to_numeric_series(df, "low")
    close = _to_numeric_series(df, "close")
    volume = _to_numeric_series(df, "volume")

    if high.empty or low.empty or close.empty or volume.empty:
        return pd.Series([0.0] * len(df), index=df.index, dtype=float)

    bar_range = (high - low).replace(0, EPS)
    mfm = ((close - low) - (high - close)) / bar_range
    mfv = mfm * volume

    cum_mfv = mfv.rolling(length, min_periods=1).sum()
    cum_vol = volume.rolling(length, min_periods=1).sum().replace(0, EPS)
    cmf = (cum_mfv / cum_vol).fillna(0.0)
    return cmf.astype(float)


def _compute_flow_context(df: pd.DataFrame, ema_len: int = 21, cmf_len: int = 20) -> Dict[str, Any]:
    high = _to_numeric_series(df, "high")
    low = _to_numeric_series(df, "low")
    close = _to_numeric_series(df, "close")
    volume = _to_numeric_series(df, "volume")

    if high.empty or low.empty or close.empty or volume.empty:
        n = len(df)
        return {
            "raw_flow": pd.Series([0.0] * n, index=df.index, dtype=float),
            "smoothed_flow": pd.Series([0.0] * n, index=df.index, dtype=float),
            "flow_z": pd.Series([0.0] * n, index=df.index, dtype=float),
            "flow_pct": pd.Series([0.5] * n, index=df.index, dtype=float),
            "flow_norm": pd.Series([0.0] * n, index=df.index, dtype=float),
            "cmf": pd.Series([0.0] * n, index=df.index, dtype=float),
            "atr": pd.Series([0.0] * n, index=df.index, dtype=float),
            "vol_ratio": pd.Series([1.0] * n, index=df.index, dtype=float),
        }

    bar_range = (high - low).replace(0, EPS)
    mfm = ((close - low) - (high - close)) / bar_range
    mfv = mfm * volume

    raw_flow = mfv.cumsum().astype(float)
    smoothed_flow = raw_flow.ewm(span=max(1, int(ema_len)), adjust=False).mean().astype(float)

    flow_scale = raw_flow.abs().ewm(span=max(5, int(cmf_len)), adjust=False).mean().replace(0, EPS)
    flow_norm = (smoothed_flow / flow_scale).fillna(0.0).astype(float)

    flow_mean = smoothed_flow.rolling(cmf_len, min_periods=1).mean()
    flow_std = smoothed_flow.rolling(cmf_len, min_periods=1).std().replace(0, EPS)
    flow_z = ((smoothed_flow - flow_mean) / flow_std).fillna(0.0).astype(float)

    flow_hi = smoothed_flow.rolling(cmf_len, min_periods=1).max()
    flow_lo = smoothed_flow.rolling(cmf_len, min_periods=1).min().replace(0, EPS)
    flow_pct = ((smoothed_flow - flow_lo) / (flow_hi - flow_lo).replace(0, EPS)).fillna(0.5).astype(float)

    prev_close = close.shift(1).fillna(close.iloc[0])
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=1).mean().astype(float)

    vol_sma = volume.rolling(cmf_len, min_periods=1).mean().replace(0, EPS)
    vol_ratio = (volume / vol_sma).fillna(1.0).astype(float)

    cmf = _compute_cmf_like(df, cmf_len)

    return {
        "raw_flow": raw_flow,
        "smoothed_flow": smoothed_flow,
        "flow_z": flow_z,
        "flow_pct": flow_pct,
        "flow_norm": flow_norm,
        "cmf": cmf,
        "atr": atr,
        "vol_ratio": vol_ratio,
    }


def _strength_score(
    price_delta: float,
    prev_price: float,
    flow_delta: float,
    prev_flow: float,
    atr_val: float,
    vol_ratio: float,
    price_weight: float = 3.0,
    flow_weight: float = 3.0,
    atr_weight: float = 2.0,
    vol_weight: float = 2.0,
) -> float:
    p_pct = abs(price_delta) / max(abs(prev_price), EPS)
    f_pct = abs(flow_delta) / max(abs(prev_flow), EPS)
    atr_term = abs(price_delta) / max(atr_val, EPS)

    score = 0.0
    score += min((p_pct / 0.05) * price_weight, price_weight)
    score += min((f_pct / 0.10) * flow_weight, flow_weight)
    score += min((atr_term / 0.5) * atr_weight, atr_weight)
    score += min(vol_ratio * 0.5, vol_weight)

    return round(float(min(score, 10.0)), 2)


def _classify_divergence(price_now: float, price_prev: float, flow_now: float, flow_prev: float) -> str:
    if price_now > price_prev and flow_now < flow_prev:
        return "regular_bearish"
    if price_now < price_prev and flow_now > flow_prev:
        return "regular_bullish"
    if price_now > price_prev and flow_now > flow_prev:
        return "hidden_bullish"
    if price_now < price_prev and flow_now < flow_prev:
        return "hidden_bearish"
    return "none"


def _bias_from_context(
    flow_slope: float,
    cmf_val: float,
    vol_ratio: float,
    price_momentum: float,
) -> str:
    score = 0.0
    score += 1.0 if flow_slope > 0 else -1.0 if flow_slope < 0 else 0.0
    score += 1.5 if cmf_val > 0.03 else 0.5 if cmf_val > 0.01 else -1.5 if cmf_val < -0.03 else -0.5 if cmf_val < -0.01 else 0.0
    score += 0.5 if vol_ratio >= 1.05 else -0.25 if vol_ratio <= 0.95 else 0.0
    score += 0.5 if price_momentum > 0 else -0.5 if price_momentum < 0 else 0.0

    if score >= 1.5:
        return "bullish"
    if score <= -1.5:
        return "bearish"
    return "neutral"


def _confirmation_from_context(
    bias: str,
    price_delta: float,
    flow_delta: float,
    cmf_val: float,
    vol_ratio: float,
    atr_ok: bool,
) -> str:
    aligned = (bias == "bullish" and price_delta > 0 and flow_delta > 0) or (bias == "bearish" and price_delta < 0 and flow_delta < 0)
    strong_flow = abs(cmf_val) >= 0.03 or vol_ratio >= 1.2

    if aligned and strong_flow and atr_ok:
        return "strong"
    if aligned and atr_ok:
        return "weak"
    return "none"


def analyze_volume_context(
    df: pd.DataFrame,
    timeframe: str = "1h",
    ema_len: int = 21,
    cmf_len: int = 20,
    atr_len: int = 14,
) -> Dict[str, Any]:
    """
    Возвращает интерпретируемый A/D-контекст по последним закрытым данным.
    Ориентирован на использование в auto_chart.py и ollama_client.py.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {
            "bias": "neutral",
            "volume_confirmation": "none",
            "divergence": "none",
            "divergence_type": "none",
            "strength": 0.0,
            "volume_ratio": 1.0,
            "cmf": 0.0,
            "flow_z": 0.0,
            "flow_pct": 0.5,
            "regime": "transition",
            "atr_ok": False,
            "comment": "Недостаточно данных для A/D анализа.",
            "timeframe": _normalize_tf(timeframe),
            "tf_weight": _tf_weights(timeframe),
            "signals": [],
        }

    work = df.copy().reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=[c for c in ["high", "low", "close", "volume"] if c in work.columns]).reset_index(drop=True)

    if work.empty or len(work) < 5:
        return {
            "bias": "neutral",
            "volume_confirmation": "none",
            "divergence": "none",
            "divergence_type": "none",
            "strength": 0.0,
            "volume_ratio": 1.0,
            "cmf": 0.0,
            "flow_z": 0.0,
            "flow_pct": 0.5,
            "regime": "transition",
            "atr_ok": False,
            "comment": "Недостаточно очищенных данных для A/D анализа.",
            "timeframe": _normalize_tf(timeframe),
            "tf_weight": _tf_weights(timeframe),
            "signals": [],
        }

    ctx = _compute_flow_context(work, ema_len=ema_len, cmf_len=cmf_len)

    last = work.iloc[-1]
    prev = work.iloc[-2]

    last_price = _safe_float(last.get("close")) or 0.0
    prev_price = _safe_float(prev.get("close")) or last_price
    last_flow = _safe_float(ctx["smoothed_flow"].iloc[-1]) or 0.0
    prev_flow = _safe_float(ctx["smoothed_flow"].iloc[-2]) or last_flow
    last_cmf = _safe_float(ctx["cmf"].iloc[-1]) or 0.0
    last_vol_ratio = _safe_float(ctx["vol_ratio"].iloc[-1]) or 1.0
    last_atr = _safe_float(ctx["atr"].iloc[-1]) or 0.0
    last_flow_z = _safe_float(ctx["flow_z"].iloc[-1]) or 0.0
    last_flow_pct = _safe_float(ctx["flow_pct"].iloc[-1]) or 0.5

    price_delta = last_price - prev_price
    flow_delta = last_flow - prev_flow
    price_momentum = price_delta / max(abs(prev_price), EPS)

    regime = _regime_from_atr(last_price, last_atr)

    bias = _bias_from_context(
        flow_slope=flow_delta,
        cmf_val=last_cmf,
        vol_ratio=last_vol_ratio,
        price_momentum=price_momentum,
    )

    atr_ok = abs(price_delta) >= max(last_atr * 0.35, 0.0) if last_atr > 0 else False
    volume_confirmation = _confirmation_from_context(
        bias=bias,
        price_delta=price_delta,
        flow_delta=flow_delta,
        cmf_val=last_cmf,
        vol_ratio=last_vol_ratio,
        atr_ok=atr_ok,
    )

    divergence = "none"
    divergence_type = "none"
    strength = _strength_score(
        price_delta=price_delta,
        prev_price=prev_price,
        flow_delta=flow_delta,
        prev_flow=prev_flow,
        atr_val=last_atr if last_atr > 0 else max(abs(price_delta), 1.0),
        vol_ratio=last_vol_ratio,
    )

    # Локальная дивергенция на последней разворотной паре
    lookback = min(len(work) - 1, 12)
    pivot_candidates = []
    for i in range(max(1, len(work) - lookback), len(work)):
        pivot_candidates.append(i)

    # Простая устойчивая оценка дивергенции:
    # сравниваем последний бар с баром N свечей назад.
    ref_idx = max(0, len(work) - 6)
    ref_price = _safe_float(work["close"].iloc[ref_idx]) or prev_price
    ref_flow = _safe_float(ctx["smoothed_flow"].iloc[ref_idx]) or prev_flow

    div_kind = _classify_divergence(last_price, ref_price, last_flow, ref_flow)
    if div_kind != "none":
        divergence = div_kind
        divergence_type = div_kind

    signals = []
    if bias != "neutral":
        signals.append(f"{bias}_bias")
    if volume_confirmation != "none":
        signals.append(f"volume_{volume_confirmation}")
    if divergence != "none":
        signals.append(divergence)
    if atr_ok:
        signals.append("atr_ok")

    if divergence == "regular_bullish":
        comment = "Бычья регулярная дивергенция: цена слабее, поток объёма сильнее."
    elif divergence == "regular_bearish":
        comment = "Медвежья регулярная дивергенция: цена сильнее, поток объёма слабее."
    elif divergence == "hidden_bullish":
        comment = "Скрытая бычья дивергенция: откат по цене поддержан объёмом."
    elif divergence == "hidden_bearish":
        comment = "Скрытая медвежья дивергенция: восстановление цены не подтверждено объёмом."
    else:
        if bias == "bullish":
            comment = "Бычий объёмный контекст: поток и CMF поддерживают рост."
        elif bias == "bearish":
            comment = "Медвежий объёмный контекст: поток и CMF поддерживают снижение."
        else:
            comment = "Нейтральный объёмный контекст: явного преимущества покупателей или продавцов нет."

    return {
        "bias": bias,
        "volume_confirmation": volume_confirmation,
        "divergence": divergence,
        "divergence_type": divergence_type,
        "strength": strength,
        "volume_ratio": round(float(last_vol_ratio), 2),
        "cmf": round(float(last_cmf), 4),
        "flow_z": round(float(last_flow_z), 3),
        "flow_pct": round(float(last_flow_pct), 3),
        "regime": regime,
        "atr_ok": bool(atr_ok),
        "comment": comment,
        "timeframe": _normalize_tf(timeframe),
        "tf_weight": _tf_weights(timeframe),
        "signals": signals,
        "last_price": round(float(last_price), 8),
        "last_flow": round(float(last_flow), 8),
    }