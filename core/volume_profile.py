"""
Volume Profile POC — настоящие зоны консолидации по объёму на уровне цены.

POC (Point of Control) = цена с максимальным объёмом (зона справедливой стоимости).
VAH/VAL (Value Area High/Low) = границы зоны, содержащей 70% объёма вокруг POC.

В отличие от get_structural_extremums (raw max/min за N свечей) и "ZigZag" benchmark
(тоже raw max/min), Volume Profile находит реальные зоны, где цена проводила время
с максимальным объёмом — это истинные зоны консолидации.

Интеграция:
    from core.volume_profile import run_volume_profile
    vp = run_volume_profile(symbol="BTCUSDT", timeframes=["15m","1h","4h","1d"])
    # vp["timeframes"]["4h"] = {"upper": VAH, "lower": VAL, "poc": POC, ...}

Используется как fallback-0 в _fill_missing_tf_zones (до ZigZag).
Если LLM вернул зону — она приоритетнее. Если нет — берём POC.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
import ccxt

logger = logging.getLogger(__name__)


def build_volume_profile(
    symbol: str,
    timeframe: str,
    limit: int = 200,
    bins: int = 50,
    value_area_pct: float = 0.70,
    market_type: str = "future",
) -> dict:
    """
    Построить Volume Profile для одного таймфрейма.

    Алгоритм:
    1. Загрузить OHLCV (limit свечей)
    2. Разбить ценовой диапазон на N бинов
    3. Распределить объём каждой свечи по бинам (пропорционально overlap)
    4. POC = бин с макс. объёмом
    5. Value Area = 70% объёма вокруг POC, расширяя вниз/вверх

    Returns:
        {"upper": VAH, "lower": VAL, "poc": POC, "total_volume": float, "bins": int}
        или {"upper": None, "lower": None, "poc": None} при ошибке/недостатке данных
    """
    try:
        exchange = ccxt.binance({"options": {"defaultType": market_type}})
        # Binance принимает lowercase timeframe ('1d', '4h', '1h', '15m').
        # forecasts.db хранит '1D' (uppercase) — нормализуем.
        tf_normalized = timeframe.lower()
        bars = exchange.fetch_ohlcv(symbol, tf_normalized, limit=limit)
    except Exception as e:
        logger.warning("VP: fetch_ohlcv failed for %s %s: %s", symbol, timeframe, e)
        return {"upper": None, "lower": None, "poc": None, "total_volume": 0.0, "bins": bins}

    if not bars or len(bars) < 10:
        logger.warning("VP: insufficient data for %s %s (%d bars)", symbol, timeframe, len(bars) if bars else 0)
        return {"upper": None, "lower": None, "poc": None, "total_volume": 0.0, "bins": bins}

    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df[df["volume"] > 0].reset_index(drop=True)  # skip 0-volume candles

    if len(df) < 5:
        logger.warning("VP: all volumes are 0 for %s %s, falling back to TPO", symbol, timeframe)
        return _build_tpo_profile(df, bins, value_area_pct)

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    price_range = max(price_max - price_min, 1e-9)

    # Бины по цене
    bin_edges = np.linspace(price_min, price_max, bins + 1)
    volume_by_bin = np.zeros(bins, dtype=np.float64)

    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    vols = df["volume"].to_numpy(dtype=np.float64)

    # Распределить объём каждой свечи по бинам (векторизованно)
    for i in range(len(df)):
        c_low = lows[i]
        c_high = highs[i]
        c_vol = vols[i]
        c_range = max(c_high - c_low, 1e-9)

        # Найти индексы бинов, которые пересекает свеча
        lo_idx = max(0, int(np.searchsorted(bin_edges, c_low, side="right") - 1))
        hi_idx = min(bins - 1, int(np.searchsorted(bin_edges, c_high, side="right") - 1))

        for b in range(lo_idx, hi_idx + 1):
            overlap_low = max(c_low, bin_edges[b])
            overlap_high = min(c_high, bin_edges[b + 1])
            overlap = max(overlap_high - overlap_low, 0.0)
            volume_by_bin[b] += c_vol * (overlap / c_range)

    total_vol = float(volume_by_bin.sum())
    if total_vol <= 0:
        return _build_tpo_profile(df, bins, value_area_pct)

    # POC = бин с макс. объёмом
    poc_idx = int(np.argmax(volume_by_bin))
    poc = round(float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2), 2)

    # Value Area: расширяем от POC, пока не наберём value_area_pct (70%)
    target_va = total_vol * value_area_pct
    va_vol = float(volume_by_bin[poc_idx])
    lower_idx = poc_idx
    upper_idx = poc_idx

    while va_vol < target_va and (lower_idx > 0 or upper_idx < bins - 1):
        down_vol = float(volume_by_bin[lower_idx - 1]) if lower_idx > 0 else -1.0
        up_vol = float(volume_by_bin[upper_idx + 1]) if upper_idx < bins - 1 else -1.0

        if down_vol >= up_vol and lower_idx > 0:
            lower_idx -= 1
            va_vol += float(volume_by_bin[lower_idx])
        elif upper_idx < bins - 1:
            upper_idx += 1
            va_vol += float(volume_by_bin[upper_idx])
        else:
            break  # упёрлись в край

    vah = round(float(bin_edges[upper_idx + 1]), 2)  # Value Area High
    val = round(float(bin_edges[lower_idx]), 2)      # Value Area Low

    logger.info(
        "VP: %s %s — POC=%.2f, VAH=%.2f, VAL=%.2f, vol=%.0f, bins=%d",
        symbol, timeframe, poc, vah, val, total_vol, bins,
    )

    return {
        "upper": vah,
        "lower": val,
        "poc": poc,
        "total_volume": total_vol,
        "bins": bins,
    }


def _build_tpo_profile(df: pd.DataFrame, bins: int, value_area_pct: float) -> dict:
    """
    TPO (Time Price Opportunity) — fallback когда volume=0 для всех свечей.
    Считает сколько свечей коснулось каждого ценового бина (без объёма).
    """
    if len(df) < 5:
        return {"upper": None, "lower": None, "poc": None, "total_volume": 0.0, "bins": bins}

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    price_range = max(price_max - price_min, 1e-9)

    bin_edges = np.linspace(price_min, price_max, bins + 1)
    tpo_by_bin = np.zeros(bins, dtype=np.float64)

    for _, row in df.iterrows():
        c_low = float(row["low"])
        c_high = float(row["high"])
        lo_idx = max(0, int(np.searchsorted(bin_edges, c_low, side="right") - 1))
        hi_idx = min(bins - 1, int(np.searchsorted(bin_edges, c_high, side="right") - 1))
        for b in range(lo_idx, hi_idx + 1):
            tpo_by_bin[b] += 1.0

    total_tpo = float(tpo_by_bin.sum())
    if total_tpo <= 0:
        return {"upper": None, "lower": None, "poc": None, "total_volume": 0.0, "bins": bins}

    poc_idx = int(np.argmax(tpo_by_bin))
    poc = round(float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2), 2)

    target_va = total_tpo * value_area_pct
    va_tpo = float(tpo_by_bin[poc_idx])
    lower_idx = poc_idx
    upper_idx = poc_idx

    while va_tpo < target_va and (lower_idx > 0 or upper_idx < bins - 1):
        down = float(tpo_by_bin[lower_idx - 1]) if lower_idx > 0 else -1.0
        up = float(tpo_by_bin[upper_idx + 1]) if upper_idx < bins - 1 else -1.0

        if down >= up and lower_idx > 0:
            lower_idx -= 1
            va_tpo += float(tpo_by_bin[lower_idx])
        elif upper_idx < bins - 1:
            upper_idx += 1
            va_tpo += float(tpo_by_bin[upper_idx])
        else:
            break

    vah = round(float(bin_edges[upper_idx + 1]), 2)
    val = round(float(bin_edges[lower_idx]), 2)

    return {
        "upper": vah,
        "lower": val,
        "poc": poc,
        "total_volume": total_tpo,
        "bins": bins,
    }


def run_volume_profile(
    symbol: str,
    timeframes: list[str] | None = None,
    limit: int = 200,
    bins: int = 50,
    value_area_pct: float = 0.70,
    market_type: str = "future",
) -> dict:
    """
    Запустить Volume Profile для всех таймфреймов.

    Returns:
        {
            "symbol": str,
            "timeframes": {tf: {upper, lower, poc, ...}, ...},
        }
    """
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]

    tf_results: dict[str, dict] = {}

    for tf in timeframes:
        try:
            vp = build_volume_profile(
                symbol=symbol,
                timeframe=tf,
                limit=limit,
                bins=bins,
                value_area_pct=value_area_pct,
                market_type=market_type,
            )
            tf_results[tf] = vp
        except Exception as e:
            logger.warning("VP: failed for %s %s: %s", symbol, tf, e)
            tf_results[tf] = {"upper": None, "lower": None, "poc": None, "total_volume": 0.0, "bins": bins}

    return {
        "symbol": symbol,
        "timeframes": tf_results,
    }
