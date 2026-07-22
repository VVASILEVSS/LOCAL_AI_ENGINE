"""
Imbalance Detector (Fair Value Gap — FVG).

SMC-концепция: Fair Value Gap = 3-свечной паттерн, где свеча №2 оставляет
"вакуум" (gap) между свечой №1 и свечой №3. Цена стремится вернуться и
заполнить этот gap (rebalance liquidity).

    Bullish FVG:  candle[i-1].high < candle[i+1].low
                  gap_low  = candle[i-1].high
                  gap_high = candle[i+1].low

    Bearish FVG:  candle[i-1].low > candle[i+1].high
                  gap_low  = candle[i+1].high
                  gap_high = candle[i-1].low

Дополнительно: body-imbalance — упрощённый вариант по телу свечи
(body_ratio = |close - open| / (high - low) > threshold).

Модуль НЕ трогает structure.py / zones / BOS — это liquidity-концепт,
отдельная секция в zigzag_context.

Согласовано с Super Z (2026-07-17, exchange/inbox/
2026-07-17_ответ-z-fvg-принято-делаю-nesting-accumulation.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Defaults (согласовано с Z, Q3) ──
_DEFAULT_MIN_GAP_ATR = 0.3   # gap >= 0.3 * ATR — отсекаем микро-гэпы
_DEFAULT_LOOKBACK = 50       # сколько свечей назад сканировать
_DEFAULT_BODY_THRESHOLD = 0.6  # body/range ratio для body-imbalance


# ── Dataclasses ──

@dataclass
class FVG:
    """Один Fair Value Gap (3-свечной паттерн)."""
    tf: str
    direction: str            # "bullish" | "bearish"
    gap_low: float            # нижняя граница gap-зоны
    gap_high: float           # верхняя граница gap-зоны
    index: int                # индекс средней свечи (i) в DataFrame
    age_bars: int             # сколько свечей назад (0 = последняя)
    gap_size: float           # gap_high - gap_low (абсолютный размер)
    gap_size_atr: float       # gap_size / ATR (нормализованный размер)
    filled: bool = False      # закрыт ли gap последующими свечами
    fill_pct: float = 0.0     # 0..1 — насколько заполнен (цена в зоне)
    fill_price: Optional[float] = None  # цена на момент "закрытия"
    current_price_in_zone: bool = False  # текущая цена внутри gap

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tf": self.tf,
            "type": self.direction,
            "low": round(self.gap_low, 2),
            "high": round(self.gap_high, 2),
            "index": self.index,
            "age_bars": self.age_bars,
            "gap_size_atr": round(self.gap_size_atr, 2),
            "filled": self.filled,
            "fill_pct": round(self.fill_pct, 3),
            "current_price_in_zone": self.current_price_in_zone,
        }


@dataclass
class ImbalanceZone:
    """Упрощённый имбаланс по телу свечи (body-imbalance)."""
    tf: str
    direction: str            # "bullish" | "bearish"
    low: float               # min(open, close)
    high: float              # max(open, close)
    index: int
    age_bars: int
    body_ratio: float        # |close - open| / (high - low)
    body_size_atr: float     # body / ATR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tf": self.tf,
            "type": self.direction,
            "low": round(self.low, 2),
            "high": round(self.high, 2),
            "index": self.index,
            "age_bars": self.age_bars,
            "body_ratio": round(self.body_ratio, 3),
            "body_size_atr": round(self.body_size_atr, 2),
        }


# ── Core detection ──

def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR (Average True Range) за period свечей."""
    if len(df) < 2:
        return 0.0
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    close = df["close"].astype(float).to_numpy()
    tr = high[1:] - low[1:]
    tr_prev_close = abs(high[1:] - close[:-1])
    tr_prev_low = abs(low[1:] - close[:-1])
    import numpy as np
    tr_full = np.maximum(tr, np.maximum(tr_prev_close, tr_prev_low))
    period = min(period, len(tr_full))
    return float(pd.Series(tr_full).rolling(period, min_periods=1).mean().iloc[-1])


def detect_fvg(
    candles: pd.DataFrame,
    tf: str = "",
    min_gap_atr: float = _DEFAULT_MIN_GAP_ATR,
    lookback: int = _DEFAULT_LOOKBACK,
    current_price: Optional[float] = None,
) -> List[FVG]:
    """
    Детект Fair Value Gaps (3-свечной паттерн).

    Bullish FVG: candle[i-1].high < candle[i+1].low
        gap_low  = candle[i-1].high
        gap_high = candle[i+1].low

    Bearish FVG: candle[i-1].low > candle[i+1].high
        gap_low  = candle[i+1].high
        gap_high = candle[i-1].low

    Фильтр: gap_size >= min_gap_atr * ATR (отсекаем микро-гэпы).

    Args:
        candles: OHLCV DataFrame (columns: open, high, low, close, volume)
        tf: timeframe label для записи в FVG.tf
        min_gap_atr: минимальный размер gap в ATR (default 0.3)
        lookback: сколько свечей назад сканировать (default 50)
        current_price: текущая цена (для current_price_in_zone).
                       Если None — берётся из candles.close.iloc[-1].

    Returns:
        Список FVG, отсортированный от старых к новым.
        Каждый FVG проверяется на fill (закрытие) последующими свечами.
    """
    if candles is None or len(candles) < 3:
        return []

    o = candles["open"].astype(float).to_numpy()
    h = candles["high"].astype(float).to_numpy()
    l = candles["low"].astype(float).to_numpy()
    c = candles["close"].astype(float).to_numpy()
    n = len(candles)

    atr = _compute_atr(candles)
    if atr <= 0:
        return []

    if current_price is None:
        current_price = float(c[-1])

    fvgs: List[FVG] = []
    # Сканируем свечи от i=1 до n-2 (нужны i-1 и i+1)
    start = max(1, n - lookback)
    end = n - 1  # не включаем последнюю свечу как "среднюю" (нет i+1)

    for i in range(start, end):
        # Bullish FVG: candle[i-1].high < candle[i+1].low
        if h[i - 1] < l[i + 1]:
            gap_low = float(h[i - 1])
            gap_high = float(l[i + 1])
            gap_size = gap_high - gap_low
            gap_size_atr = gap_size / atr
            if gap_size_atr < min_gap_atr:
                continue
            direction = "bullish"
        # Bearish FVG: candle[i-1].low > candle[i+1].high
        elif l[i - 1] > h[i + 1]:
            gap_low = float(h[i + 1])
            gap_high = float(l[i - 1])
            gap_size = gap_high - gap_low
            gap_size_atr = gap_size / atr
            if gap_size_atr < min_gap_atr:
                continue
            direction = "bearish"
        else:
            continue

        age_bars = n - 1 - i  # 0 = последняя свеча-кандидат
        # Проверяем fill: пересекла ли любая последующая свеча gap-зону
        filled, fill_pct, fill_price = _check_fill(
            gap_low, gap_high, candles, i + 1
        )
        in_zone = bool(gap_low <= current_price <= gap_high)

        fvgs.append(FVG(
            tf=tf,
            direction=direction,
            gap_low=gap_low,
            gap_high=gap_high,
            index=i,
            age_bars=age_bars,
            gap_size=gap_size,
            gap_size_atr=gap_size_atr,
            filled=filled,
            fill_pct=fill_pct,
            fill_price=fill_price,
            current_price_in_zone=in_zone,
        ))

    return fvgs


def _check_fill(
    gap_low: float,
    gap_high: float,
    candles: pd.DataFrame,
    start_idx: int,
) -> tuple:
    """
    Проверяет, закрыт ли gap (цена вернулась в зону).

    filled = True если любая свеча после start_idx пересекла зону
             (low <= gap_high AND high >= gap_low).
    fill_pct = насколько глубоко цена вошла в зону (0..1).
               Если цена прошла насквозь — 1.0.
    fill_price = цена закрытия свечи которая закрыла gap (или None).
    """
    h = candles["high"].astype(float).to_numpy()
    l = candles["low"].astype(float).to_numpy()
    c = candles["close"].astype(float).to_numpy()
    n = len(candles)

    for j in range(start_idx + 1, n):
        # Свеча пересекла зону
        if l[j] <= gap_high and h[j] >= gap_low:
            # Насколько глубоко вошла
            overlap_low = max(l[j], gap_low)
            overlap_high = min(h[j], gap_high)
            gap_size = gap_high - gap_low
            fill_pct = (overlap_high - overlap_low) / gap_size if gap_size > 0 else 0
            return True, float(min(fill_pct, 1.0)), float(c[j])

    # Не заполнен — но считаем partial если последняя цена в зоне
    last_close = float(c[-1]) if len(c) > 0 else None
    if last_close is not None and gap_low <= last_close <= gap_high:
        # Частичное заполнение — цена в зоне но не пересекла
        return False, 0.0, None

    return False, 0.0, None


def detect_imbalance_zones(
    candles: pd.DataFrame,
    tf: str = "",
    body_threshold: float = _DEFAULT_BODY_THRESHOLD,
    lookback: int = _DEFAULT_LOOKBACK,
) -> List[ImbalanceZone]:
    """
    Упрощённый детект имбалансов по телу свечи.

    body_ratio = |close - open| / (high - low)
    body_ratio > threshold = имбаланс (сильный импульс).

    Returns:
        Список ImbalanceZone, отсортированный от старых к новым.
    """
    if candles is None or len(candles) < 1:
        return []

    o = candles["open"].astype(float).to_numpy()
    h = candles["high"].astype(float).to_numpy()
    l = candles["low"].astype(float).to_numpy()
    c = candles["close"].astype(float).to_numpy()
    n = len(candles)

    atr = _compute_atr(candles)
    if atr <= 0:
        return []

    zones: List[ImbalanceZone] = []
    start = max(0, n - lookback)

    for i in range(start, n):
        candle_range = h[i] - l[i]
        if candle_range <= 0:
            continue
        body = abs(c[i] - o[i])
        body_ratio = body / candle_range
        if body_ratio < body_threshold:
            continue

        direction = "bullish" if c[i] > o[i] else "bearish"
        body_size_atr = body / atr
        age_bars = n - 1 - i

        zones.append(ImbalanceZone(
            tf=tf,
            direction=direction,
            low=float(min(o[i], c[i])),
            high=float(max(o[i], c[i])),
            index=i,
            age_bars=age_bars,
            body_ratio=float(body_ratio),
            body_size_atr=float(body_size_atr),
        ))

    return zones


def get_active_imbalances(
    candles: pd.DataFrame,
    tf: str = "",
    current_price: Optional[float] = None,
    min_gap_atr: float = _DEFAULT_MIN_GAP_ATR,
    lookback: int = _DEFAULT_LOOKBACK,
    body_threshold: float = _DEFAULT_BODY_THRESHOLD,
    max_fvg: int = 5,
    max_body: int = 3,
) -> Dict[str, Any]:
    """
    Главная функция для интеграции в benchmark_zigzag.py.

    Возвращает dict с двумя ключами:
        "fvgs": List[FVG.to_dict()] — строгие 3-свчные FVG (приоритет)
        "body_imbalances": List[ImbalanceZone.to_dict()] — упрощённые body-imbalance

    FVG сортируются: незаполненные (filled=False) первыми, потом самые свежие.
    Body-imbalance: только последние N.
    """
    fvgs = detect_fvg(
        candles, tf=tf, min_gap_atr=min_gap_atr,
        lookback=lookback, current_price=current_price,
    )
    body_zones = detect_imbalance_zones(
        candles, tf=tf, body_threshold=body_threshold, lookback=lookback,
    )

    # FVG: незаполненные + в зоне текущей цены — приоритет
    fvgs_sorted = sorted(
        fvgs,
        key=lambda f: (not (not f.filled and f.current_price_in_zone), f.age_bars),
    )
    fvgs_top = fvgs_sorted[:max_fvg]

    body_top = body_zones[-max_body:] if body_zones else []

    return {
        "fvgs": [f.to_dict() for f in fvgs_top],
        "body_imbalances": [z.to_dict() for z in body_top],
        "atr": round(_compute_atr(candles), 2),
    }
