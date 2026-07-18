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

    # A/D trend: slope of smoothed_flow over last 20 bars
    sf_len = len(smoothed_flow)
    if sf_len >= 20:
        recent_slope = float(smoothed_flow.iloc[-1]) - float(smoothed_flow.iloc[-20])
        sf_range = smoothed_flow.iloc[-20:].max() - smoothed_flow.iloc[-20:].min()
        ad_slope_norm = float(np.clip(recent_slope / max(abs(sf_range), EPS), -1.0, 1.0))
    else:
        ad_slope_norm = 0.0
    ad_trend = "rising" if ad_slope_norm > 0.05 else ("falling" if ad_slope_norm < -0.05 else "flat")

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
        "ad_trend": ad_trend,
        "ad_slope_norm": ad_slope_norm,
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
            "ad_trend": "flat",
            "ad_slope": 0.0,
            "cmf_20": 0.0,
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
            "ad_trend": "flat",
            "ad_slope": 0.0,
            "cmf_20": 0.0,
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
        "ad_trend": ctx.get("ad_trend", "flat"),
        "ad_slope": round(float(ctx.get("ad_slope_norm", 0.0)), 4),
        "cmf_20": round(float(last_cmf), 4),
    }


# ---------------------------------------------------------------------------
#  A/D Hierarchy: local / medium / senior bias across multiple timeframes
# ---------------------------------------------------------------------------

_TF_ORDER = ["5m", "15m", "1h", "4h", "1d"]


def _empty_hierarchy() -> Dict[str, Any]:
    return {
        "local_tf": "unknown",
        "medium_tf": "unknown",
        "senior_tf": "unknown",
        "local_bias": "neutral",
        "senior_bias": "neutral",
        "overall_bias": "neutral",
        "hierarchy_score": 0.0,
        "alignment": "neutral",
        "senior_local_aligned": True,
        "overall_confirmation": "none",
        "timeframes": {},
        "comment": "Нет данных для A/D hierarchy.",
        "signals": [],
        # enforce_risk_rules compatibility
        "ad_trend": "flat",
        "cmf_20": 0.0,
        "volume_confirmation": "none",
        "divergence": "none",
    }


def analyze_volume_hierarchy(
    tf_volume_contexts: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    TF-aware A/D hierarchy: local / medium / senior bias.

    Принимает словарь {tf: volume_context_dict} от analyze_volume_context()
    по нескольким таймфреймам. Возвращает объединённый иерархический контекст
    для LLM-интерпретации и для enforce_risk_rules().

    Логика:
      - local  = самый мелкий TF (15m)
      - medium = средний TF (1h)
      - senior = самый крупный TF (4h / 1d)
      - hierarchy_score: взвешенный bias (-1..+1)
      - alignment: all_bullish / mostly_bullish / conflicting / neutral / mostly_bearish / all_bearish
      - senior_local_aligned: согласованность senior и local
    """
    if not tf_volume_contexts:
        return _empty_hierarchy()

    # Сортировка TF по длительности (короткие → длинные)
    sorted_tfs = sorted(
        tf_volume_contexts.keys(),
        key=lambda tf: _TF_ORDER.index(tf) if tf in _TF_ORDER else len(_TF_ORDER),
    )
    if not sorted_tfs:
        return _empty_hierarchy()

    # Назначение ролей
    local_tf = sorted_tfs[0]
    senior_tf = sorted_tfs[-1]
    medium_tf = sorted_tfs[len(sorted_tfs) // 2] if len(sorted_tfs) >= 3 else local_tf

    local_ctx = tf_volume_contexts.get(local_tf, {})
    senior_ctx = tf_volume_contexts.get(senior_tf, {})
    medium_ctx = tf_volume_contexts.get(medium_tf, {})

    local_bias = str(local_ctx.get("bias", "neutral"))
    senior_bias = str(senior_ctx.get("bias", "neutral"))

    # --- Взвешенный hierarchy_score ---
    score = 0.0
    weight_sum = 0.0
    tf_details: Dict[str, Any] = {}

    for tf in sorted_tfs:
        ctx = tf_volume_contexts.get(tf, {})
        w = _tf_weights(tf)
        b = str(ctx.get("bias", "neutral"))
        b_val = 1.0 if b == "bullish" else (-1.0 if b == "bearish" else 0.0)
        score += b_val * w
        weight_sum += w

        tf_details[tf] = {
            "bias": b,
            "weight": w,
            "confirmation": ctx.get("volume_confirmation", "none"),
            "cmf": ctx.get("cmf", 0.0),
            "cmf_20": ctx.get("cmf_20", 0.0),
            "flow_z": ctx.get("flow_z", 0.0),
            "ad_trend": ctx.get("ad_trend", "flat"),
            "ad_slope": ctx.get("ad_slope", 0.0),
        }

    hierarchy_score = round(score / max(weight_sum, EPS), 3)

    # --- Alignment classification ---
    all_biases = [tf_details[tf]["bias"] for tf in sorted_tfs]
    bull_count = sum(1 for b in all_biases if b == "bullish")
    bear_count = sum(1 for b in all_biases if b == "bearish")
    neutral_count = sum(1 for b in all_biases if b == "neutral")
    n = len(all_biases)

    if bull_count == n:
        alignment = "all_bullish"
    elif bear_count == n:
        alignment = "all_bearish"
    elif bull_count > bear_count and bear_count == 0:
        alignment = "mostly_bullish"
    elif bear_count > bull_count and bull_count == 0:
        alignment = "mostly_bearish"
    elif bull_count > 0 and bear_count > 0:
        alignment = "conflicting"
    else:
        alignment = "neutral"

    # --- Overall bias ---
    if hierarchy_score >= 0.5:
        overall_bias = "bullish"
    elif hierarchy_score <= -0.5:
        overall_bias = "bearish"
    else:
        overall_bias = "neutral"

    # --- Senior-Local agreement ---
    senior_local_aligned = (
        senior_bias == local_bias
        or "neutral" in (senior_bias, local_bias)
    )

    # --- Overall volume confirmation ---
    strong_tfs = [tf for tf in sorted_tfs if tf_details[tf].get("confirmation") == "strong"]
    weak_tfs = [tf for tf in sorted_tfs if tf_details[tf].get("confirmation") == "weak"]

    if strong_tfs and len(strong_tfs) >= (n // 2 + 1):
        overall_confirmation = "strong"
    elif weak_tfs and len(weak_tfs) >= (n // 2 + 1):
        overall_confirmation = "weak"
    elif strong_tfs or weak_tfs:
        overall_confirmation = "mixed"
    else:
        overall_confirmation = "none"

    # --- Interpretable comment for LLM ---
    if alignment == "all_bullish":
        comment = (
            f"Все ТФ ({', '.join(sorted_tfs)}) показывают бычий A/D bias. "
            f"Сильное объёмное подтверждение роста на всех горизонтах."
        )
    elif alignment == "all_bearish":
        comment = (
            f"Все ТФ ({', '.join(sorted_tfs)}) показывают медвежий A/D bias. "
            f"Сильное объёмное подтверждение снижения на всех горизонтах."
        )
    elif alignment == "mostly_bullish":
        comment = (
            f"Большинство ТФ показывают бычий A/D bias ({bull_count}/{n}). "
            f"Senior ({senior_tf})={senior_bias}, Local ({local_tf})={local_bias}. "
            f"Общий confirmation={overall_confirmation}."
        )
    elif alignment == "mostly_bearish":
        comment = (
            f"Большинство ТФ показывают медвежий A/D bias ({bear_count}/{n}). "
            f"Senior ({senior_tf})={senior_bias}, Local ({local_tf})={local_bias}. "
            f"Общий confirmation={overall_confirmation}."
        )
    elif alignment == "conflicting":
        comment = (
            f"КОНФЛИКТ A/D bias между ТФ! Senior ({senior_tf})={senior_bias}, "
            f"Local ({local_tf})={local_bias}. Бычих: {bull_count}, Медвежьих: {bear_count}. "
            f"Ожидайте ложных пробоев — ориентируйтесь на senior TF."
        )
    else:
        comment = (
            f"A/D контекст нейтральный на всех ТФ ({', '.join(sorted_tfs)}). "
            f"Явного преимущества покупателей или продавцов нет."
        )

    # --- LTF-specific keys for enforce_risk_rules compatibility ---
    ltf_volume = tf_volume_contexts.get(sorted_tfs[-1], local_ctx)

    return {
        "local_tf": local_tf,
        "medium_tf": medium_tf,
        "senior_tf": senior_tf,
        "local_bias": local_bias,
        "senior_bias": senior_bias,
        "overall_bias": overall_bias,
        "hierarchy_score": hierarchy_score,
        "alignment": alignment,
        "senior_local_aligned": senior_local_aligned,
        "overall_confirmation": overall_confirmation,
        "timeframes": tf_details,
        "comment": comment,
        "signals": [f"hierarchy_{alignment}", f"overall_{overall_bias}"],
        # enforce_risk_keys — берём с LTF (последнего в списке = самого мелкого)
        "ad_trend": str(ltf_volume.get("ad_trend", "flat")),
        "cmf_20": _safe_float(ltf_volume.get("cmf_20")) or 0.0,
        "volume_confirmation": str(ltf_volume.get("volume_confirmation", "none")),
        "divergence": str(ltf_volume.get("divergence", "none")),
    }


# ---------------------------------------------------------------------------
#  Signal Score: mirrors Get-CandidateMetrics from v11generate_unified_dataset.ps1
#  Used in live analysis to provide a quality score for LLM filtering.
#  Score 0-100, Quality: weak (<40), medium (40-70), strong (70+)
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_signal_score(
    volume_context: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute a quality score for the current signal, mirroring the PS1
    Get-CandidateMetrics formula calibrated to produce real distribution.

    Available fields from volume_context:
      - volume_ratio, flow_pct, flow_z, strength (0-10), divergence,
        volume_confirmation, bias, cmf_20, ad_trend, ad_slope, atr_ok,
        last_price, last_flow

    Available fields from metrics (auto_chart get_technical_metrics):
      - atr, vol_ratio, rsi, current_price, last_closed_price

    Score components (same dividers as PS1 v2):
      priceComp: price delta relative to ATR → /0.06
      flowComp: |flow_pct change| → /80.0  (mapped from flow_pct)
      scaleComp: log10(flow_scale proxy) → /8.0  (mapped from strength/10)
      atrComp: atr / price → /0.025

    Returns dict with score, quality, strength, per-component breakdown.
    """
    price = _safe_float(metrics.get("current_price")) or _safe_float(metrics.get("last_closed_price")) or 0.0
    atr_val = _safe_float(metrics.get("atr")) or 0.0
    vol_ratio = _safe_float(volume_context.get("volume_ratio")) or _safe_float(metrics.get("vol_ratio")) or 1.0
    flow_pct = _safe_float(volume_context.get("flow_pct")) or 0.5
    flow_z = _safe_float(volume_context.get("flow_z")) or 0.0
    cmf_val = _safe_float(volume_context.get("cmf_20")) or _safe_float(volume_context.get("cmf")) or 0.0
    strength = _safe_float(volume_context.get("strength")) or 0.0
    bias = str(volume_context.get("bias", "neutral"))
    divergence = str(volume_context.get("divergence", "none"))
    confirmation = str(volume_context.get("volume_confirmation", "none"))
    ad_trend = str(volume_context.get("ad_trend", "flat"))

    # --- Component 1: Price move relative to ATR ---
    # Proxy: use strength as indicator of price-flow divergence magnitude
    # strength 0-10 maps to approximate priceMovePct
    price_move_proxy = strength / 10.0  # normalize 0-10 to 0-1
    price_comp = _clamp01(price_move_proxy / 0.06)

    # --- Component 2: Flow magnitude ---
    # flow_z indicates how many std devs the flow is from mean
    # Typical range -2 to +2, absolute value
    flow_magnitude = abs(flow_z) * 10.0  # scale to approximate flowRatio
    flow_comp = _clamp01(flow_magnitude / 80.0)

    # --- Component 3: Flow scale (money flow volume) ---
    # Proxied by strength (higher strength = more volume involvement)
    # log10(strength_proxy) where strength 0-10 → scale 1-10
    scale_proxy = max(strength, 0.1)  # avoid log(0)
    scale_comp = _clamp01((np.log10(scale_proxy + 1.0)) / 8.0) if scale_proxy > 0 else 0.0

    # --- Component 4: ATR ratio ---
    atr_ratio = atr_val / price if price > 0 and atr_val > 0 else 0.0
    atr_comp = _clamp01(atr_ratio / 0.025)

    # --- Divergence bonus ---
    div_bonus = 0.0
    if divergence in ("regular_bullish", "regular_bearish"):
        div_bonus = 0.08
    elif divergence in ("hidden_bullish", "hidden_bearish"):
        div_bonus = 0.05

    # --- Volume confirmation bonus ---
    conf_bonus = 0.0
    if confirmation == "strong":
        conf_bonus = 0.06
    elif confirmation == "weak":
        conf_bonus = 0.03

    # --- AD trend alignment bonus ---
    trend_bonus = 0.0
    if ad_trend == "rising" and bias in ("bullish",):
        trend_bonus = 0.04
    elif ad_trend == "falling" and bias in ("bearish",):
        trend_bonus = 0.04

    # --- CMF extreme bonus ---
    cmf_bonus = 0.0
    if abs(cmf_val) > 0.2:
        cmf_bonus = 0.04
    elif abs(cmf_val) > 0.1:
        cmf_bonus = 0.02

    # --- Final score ---
    score01 = (
        (0.35 * price_comp) +
        (0.30 * flow_comp) +
        (0.15 * scale_comp) +
        (0.10 * atr_comp) +
        div_bonus + conf_bonus + trend_bonus + cmf_bonus
    )
    score01 = min(score01, 1.0)
    score = round(score01 * 100.0, 2)

    if score >= 70:
        quality = "strong"
    elif score >= 40:
        quality = "medium"
    else:
        quality = "weak"

    return {
        "signal_score": score,
        "signal_quality": quality,
        "signal_strength": round(score01, 4),
        "components": {
            "price_comp": round(price_comp, 4),
            "flow_comp": round(flow_comp, 4),
            "scale_comp": round(float(scale_comp), 4),
            "atr_comp": round(atr_comp, 4),
            "div_bonus": round(div_bonus, 4),
            "conf_bonus": round(conf_bonus, 4),
            "trend_bonus": round(trend_bonus, 4),
            "cmf_bonus": round(cmf_bonus, 4),
        },
        "inputs": {
            "price": price,
            "atr": atr_val,
            "vol_ratio": vol_ratio,
            "flow_z": flow_z,
            "flow_pct": flow_pct,
            "strength": strength,
            "cmf": cmf_val,
            "bias": bias,
            "divergence": divergence,
            "confirmation": confirmation,
            "ad_trend": ad_trend,
        },
    }