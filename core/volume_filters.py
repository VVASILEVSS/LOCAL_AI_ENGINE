# Назначение: расчёт объёмных фильтров A/D, CMF и дивергенций.
# Отвечает за: подтверждение накопления, распределения, силы пробоя и разворотных сигналов объёмом.
# Связано с: auto_chart.py, ollama_client.py, scheduler.py.

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

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


def calculate_ad_line(df: pd.DataFrame) -> pd.Series:
    """
    Классическая A/D line (Chaikin Accumulation/Distribution).
    """
    clean = _prepare_df(df)
    if clean.empty:
        return pd.Series(dtype="float64")

    high = clean["high"]
    low = clean["low"]
    close = clean["close"]
    volume = clean["volume"]

    range_ = high - low
    clv = pd.Series(0.0, index=clean.index)

    non_zero = range_ != 0
    clv.loc[non_zero] = (((close - low) - (high - close)) / range_).loc[non_zero]
    mfv = clv * volume

    ad_line = mfv.cumsum()
    ad_line.name = "ad_line"
    return ad_line


def calculate_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Chaikin Money Flow (CMF).
    """
    clean = _prepare_df(df)
    if clean.empty:
        return pd.Series(dtype="float64")

    high = clean["high"]
    low = clean["low"]
    close = clean["close"]
    volume = clean["volume"]

    range_ = high - low
    clv = pd.Series(0.0, index=clean.index)

    non_zero = range_ != 0
    clv.loc[non_zero] = (((close - low) - (high - close)) / range_).loc[non_zero]

    mfv = clv * volume
    vol_sum = volume.rolling(period, min_periods=1).sum()
    mfv_sum = mfv.rolling(period, min_periods=1).sum()

    cmf = mfv_sum / vol_sum.replace(0, np.nan)
    cmf = cmf.fillna(0.0)
    cmf.name = f"cmf_{period}"
    return cmf


def _trend_from_delta(delta: Optional[float], eps: float = 1e-9) -> str:
    if delta is None:
        return "unknown"
    if delta > eps:
        return "rising"
    if delta < -eps:
        return "falling"
    return "flat"


def _volume_confirmation_from_values(ad_delta: Optional[float], cmf_last: Optional[float]) -> str:
    if ad_delta is None or cmf_last is None:
        return "unknown"

    if ad_delta > 0 and cmf_last > 0:
        return "bullish"
    if ad_delta < 0 and cmf_last < 0:
        return "bearish"
    return "neutral"


def _find_last_two_swing_lows(series: pd.Series, lookback: int = 40) -> Tuple[Optional[int], Optional[int]]:
    clean = series.dropna()
    if len(clean) < 5:
        return None, None

    s = clean.tail(lookback)
    idxs = list(s.index)
    vals = s.to_numpy(dtype=float)

    pivots = []
    for i in range(1, len(vals) - 1):
        if vals[i] <= vals[i - 1] and vals[i] <= vals[i + 1]:
            pivots.append(idxs[i])

    if len(pivots) >= 2:
        return pivots[-2], pivots[-1]
    if len(pivots) == 1:
        return None, pivots[-1]
    return None, None


def _find_last_two_swing_highs(series: pd.Series, lookback: int = 40) -> Tuple[Optional[int], Optional[int]]:
    clean = series.dropna()
    if len(clean) < 5:
        return None, None

    s = clean.tail(lookback)
    idxs = list(s.index)
    vals = s.to_numpy(dtype=float)

    pivots = []
    for i in range(1, len(vals) - 1):
        if vals[i] >= vals[i - 1] and vals[i] >= vals[i + 1]:
            pivots.append(idxs[i])

    if len(pivots) >= 2:
        return pivots[-2], pivots[-1]
    if len(pivots) == 1:
        return None, pivots[-1]
    return None, None


def detect_divergence(
    df: pd.DataFrame,
    price_series: Optional[pd.Series] = None,
    indicator_series: Optional[pd.Series] = None,
    lookback: int = 40,
    mode: str = "bullish_bearish",
) -> Dict[str, Any]:
    """
    Ищет простую дивергенцию между ценой и A/D (или другим индикатором).

    mode:
    - bullish_bearish: ищет оба типа
    - bullish_only: только bullish
    - bearish_only: только bearish
    """
    clean = _prepare_df(df)
    if clean.empty:
        return {
            "divergence": "unknown",
            "divergence_strength": "unknown",
            "divergence_comment": "Недостаточно данных для поиска дивергенции.",
        }

    if price_series is None:
        price_series = clean["close"]
    if indicator_series is None:
        indicator_series = calculate_ad_line(clean)

    if price_series.empty or indicator_series.empty:
        return {
            "divergence": "unknown",
            "divergence_strength": "unknown",
            "divergence_comment": "Недостаточно данных для поиска дивергенции.",
        }

    price_series = price_series.reset_index(drop=True)
    indicator_series = indicator_series.reset_index(drop=True)

    # bullish divergence: price lower low, indicator higher low
    divergence = "none"
    strength = "weak"
    comment = "Дивергенция не обнаружена."

    p_lo1, p_lo2 = _find_last_two_swing_lows(price_series, lookback=lookback)
    i_lo1, i_lo2 = _find_last_two_swing_lows(indicator_series, lookback=lookback)

    if (
        mode in ("bullish_bearish", "bullish_only")
        and p_lo1 is not None
        and p_lo2 is not None
        and i_lo1 is not None
        and i_lo2 is not None
    ):
        p1 = _safe_float(price_series.iloc[p_lo1])
        p2 = _safe_float(price_series.iloc[p_lo2])
        i1 = _safe_float(indicator_series.iloc[i_lo1])
        i2 = _safe_float(indicator_series.iloc[i_lo2])

        if p1 is not None and p2 is not None and i1 is not None and i2 is not None:
            if p2 < p1 and i2 > i1:
                divergence = "bullish"
                strength = "medium" if abs(i2 - i1) > 0 else "weak"
                comment = "Цена обновляет минимум, а A/D формирует более высокий минимум — bullish divergence."

    # bearish divergence: price higher high, indicator lower high
    p_hi1, p_hi2 = _find_last_two_swing_highs(price_series, lookback=lookback)
    i_hi1, i_hi2 = _find_last_two_swing_highs(indicator_series, lookback=lookback)

    if divergence == "none" and (
        mode in ("bullish_bearish", "bearish_only")
        and p_hi1 is not None
        and p_hi2 is not None
        and i_hi1 is not None
        and i_hi2 is not None
    ):
        p1 = _safe_float(price_series.iloc[p_hi1])
        p2 = _safe_float(price_series.iloc[p_hi2])
        i1 = _safe_float(indicator_series.iloc[i_hi1])
        i2 = _safe_float(indicator_series.iloc[i_hi2])

        if p1 is not None and p2 is not None and i1 is not None and i2 is not None:
            if p2 > p1 and i2 < i1:
                divergence = "bearish"
                strength = "medium" if abs(i2 - i1) > 0 else "weak"
                comment = "Цена обновляет максимум, а A/D формирует более низкий максимум — bearish divergence."

    return {
        "divergence": divergence,
        "divergence_strength": strength if divergence != "none" else "weak",
        "divergence_comment": comment,
    }


def analyze_volume_context(
    df: pd.DataFrame,
    cmf_period: int = 20,
    delta_lookback: int = 5,
    divergence_lookback: int = 40,
) -> Dict[str, Any]:
    """
    Считает A/D + CMF + дивергенции и возвращает компактный объёмный контекст.
    """
    clean = _prepare_df(df)
    if clean.empty:
        return {
            "ad_line": None,
            "ad_prev": None,
            "ad_delta": None,
            "ad_trend": "unknown",
            "ad_slope_pct": None,
            "cmf_20": None,
            "cmf_trend": "unknown",
            "volume_confirmation": "unknown",
            "divergence": "unknown",
            "divergence_strength": "unknown",
            "divergence_comment": "Недостаточно данных для расчёта объёма и дивергенции.",
            "volume_comment": "Недостаточно данных для расчёта A/D и CMF.",
        }

    ad_line_series = calculate_ad_line(clean)
    cmf_series = calculate_cmf(clean, period=cmf_period)

    if ad_line_series.empty:
        return {
            "ad_line": None,
            "ad_prev": None,
            "ad_delta": None,
            "ad_trend": "unknown",
            "ad_slope_pct": None,
            "cmf_20": None,
            "cmf_trend": "unknown",
            "volume_confirmation": "unknown",
            "divergence": "unknown",
            "divergence_strength": "unknown",
            "divergence_comment": "Не удалось рассчитать A/D.",
            "volume_comment": "Не удалось рассчитать A/D.",
        }

    ad_last = _safe_float(ad_line_series.iloc[-1])
    ad_prev = _safe_float(ad_line_series.iloc[-1 - delta_lookback]) if len(ad_line_series) > delta_lookback else _safe_float(ad_line_series.iloc[0])

    ad_delta = None
    ad_slope_pct = None
    if ad_last is not None and ad_prev is not None:
        ad_delta = ad_last - ad_prev
        base = max(abs(ad_prev), 1e-9)
        ad_slope_pct = round((ad_delta / base) * 100.0, 4)

    cmf_last = _safe_float(cmf_series.iloc[-1]) if not cmf_series.empty else None
    cmf_prev = _safe_float(cmf_series.iloc[-2]) if len(cmf_series) > 1 else None

    ad_trend = _trend_from_delta(ad_delta)
    cmf_trend = _trend_from_delta((cmf_last - cmf_prev) if (cmf_last is not None and cmf_prev is not None) else None)

    volume_confirmation = _volume_confirmation_from_values(ad_delta, cmf_last)

    divergence_ctx = detect_divergence(
        clean,
        price_series=clean["close"].reset_index(drop=True),
        indicator_series=ad_line_series.reset_index(drop=True),
        lookback=divergence_lookback,
        mode="bullish_bearish",
    )

    if volume_confirmation == "bullish":
        volume_comment = "A/D растёт и CMF положительный: объём поддерживает движение вверх или отскок."
    elif volume_confirmation == "bearish":
        volume_comment = "A/D падает и CMF отрицательный: объём поддерживает движение вниз или распределение."
    elif volume_confirmation == "neutral":
        volume_comment = "Объём нейтрален: A/D и CMF не дают явного подтверждения направления."
    else:
        volume_comment = "Недостаточно данных для надёжного объёмного подтверждения."

    return {
        "ad_line": round(ad_last, 6) if ad_last is not None else None,
        "ad_prev": round(ad_prev, 6) if ad_prev is not None else None,
        "ad_delta": round(ad_delta, 6) if ad_delta is not None else None,
        "ad_trend": ad_trend,
        "ad_slope_pct": round(ad_slope_pct, 4) if ad_slope_pct is not None else None,
        "cmf_20": round(cmf_last, 6) if cmf_last is not None else None,
        "cmf_trend": cmf_trend,
        "volume_confirmation": volume_confirmation,
        "volume_comment": volume_comment,
        "divergence": divergence_ctx.get("divergence", "unknown"),
        "divergence_strength": divergence_ctx.get("divergence_strength", "unknown"),
        "divergence_comment": divergence_ctx.get("divergence_comment", ""),
        "cmf_period": cmf_period,
        "delta_lookback": delta_lookback,
        "divergence_lookback": divergence_lookback,
    }


def build_volume_context_text(volume_ctx: Dict[str, Any]) -> str:
    """
    Короткий текст для prompt LLM.
    """
    if not isinstance(volume_ctx, dict):
        return "Объёмный контекст недоступен."

    return (
        f"A/D: {volume_ctx.get('ad_trend', 'unknown')} | "
        f"ΔA/D: {volume_ctx.get('ad_delta', 'N/A')} | "
        f"CMF(20): {volume_ctx.get('cmf_20', 'N/A')} | "
        f"Confirmation: {volume_ctx.get('volume_confirmation', 'unknown')} | "
        f"Divergence: {volume_ctx.get('divergence', 'unknown')} | "
        f"{volume_ctx.get('volume_comment', '')} "
        f"{volume_ctx.get('divergence_comment', '')}"
    )