import ccxt
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from core.utils import is_futures
from core.volume_filters import analyze_volume_context
from core.data_provider import OhlcvDataProvider, OhlcvRequest
import matplotlib


matplotlib.use('Agg')
logger = logging.getLogger(__name__)


def get_session_phase(utc_hour: int) -> str:
    if 7 <= utc_hour < 16:
        return "London/NY (высокая волатильность)"
    if 0 <= utc_hour < 7:
        return "Asia (низкая волатильность, риск ложных пробоев)"
    return "Pre-London (накопление)"


def calculate_fib(df: pd.DataFrame) -> dict:
    window = min(50, len(df))
    last = df.tail(window)
    swing_low = last['low'].min()
    swing_high = last['high'].max()
    diff = swing_high - swing_low
    if diff == 0:
        return {}
    return {f"fib_{k}": round(swing_high - (diff * k), 2) for k in [0.786, 0.618, 0.5, 0.382, 0.236, 0.0]}


def find_structural_levels(df: pd.DataFrame, lookback: int = 25) -> dict:
    """Ищет ближайшие значимые пики/впадины за lookback свечей для TP/SL"""
    n = min(lookback, len(df))
    recent = df.tail(n)
    high = recent['high'].values
    low = recent['low'].values

    res = round(float(recent['high'].max()), 2)
    sup = round(float(recent['low'].min()), 2)

    if len(high) > 3:
        for i in range(1, len(high) - 1):
            if high[i] > high[i - 1] and high[i] >= high[i + 1]:
                res = round(float(high[i]), 2)
            if low[i] < low[i - 1] and low[i] <= low[i + 1]:
                sup = round(float(low[i]), 2)
    return {'resistance': res, 'support': sup}


def _to_float_array(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)


def _compute_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1).fillna(close.iloc[0])
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean().to_numpy(dtype=float)


def _find_pivots(highs: np.ndarray, lows: np.ndarray, depth: int) -> Tuple[List[int], List[int]]:
    pivot_highs, pivot_lows = [], []
    n = min(len(highs), len(lows))
    if n < depth * 2 + 1:
        return pivot_highs, pivot_lows

    for i in range(depth, n - depth):
        if highs[i] > np.max(highs[i - depth:i]) and highs[i] > np.max(highs[i + 1:i + depth + 1]):
            pivot_highs.append(i)
        if lows[i] < np.min(lows[i - depth:i]) and lows[i] < np.min(lows[i + 1:i + depth + 1]):
            pivot_lows.append(i)
    return pivot_highs, pivot_lows


def _cluster_levels(levels: List[float], tolerance: float) -> List[float]:
    if not levels:
        return []
    levels = sorted(levels)
    clusters: List[List[float]] = [[levels[0]]]
    for level in levels[1:]:
        clusters[-1].append(level) if abs(level - clusters[-1][-1]) <= tolerance else clusters.append([level])
    return [float(np.mean(c)) for c in clusters]


def _select_last_significant(levels: List[float], current_price: float, side: str) -> Optional[float]:
    if not levels:
        return None
    if side == "high":
        cands = [x for x in levels if x >= current_price]
        return min(cands) if cands else max(levels)
    cands = [x for x in levels if x <= current_price]
    return max(cands) if cands else min(levels)

def _build_strict_last_swing_range_15m(highs: np.ndarray, lows: np.ndarray, sig_highs: List[int], sig_lows: List[int], atr_last: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Строго строит последний значимый swing-range для 15m.
    Ищет последнюю валидную смену типа экстремума:
    - low -> high
    - high -> low
    и отбрасывает слишком маленькие legs.
    """
    if not sig_highs or not sig_lows:
        return None, None

    events = []
    for i in sig_highs:
        events.append((i, "high"))
    for i in sig_lows:
        events.append((i, "low"))

    events.sort(key=lambda x: x[0])

    if len(events) < 2:
        return None, None

    min_leg = max(atr_last * 1.2, float(np.mean(highs - lows)) * 2.0 if len(highs) > 0 else atr_last * 1.2)

    # Идём с конца и ищем последнюю валидную противоположную пару
    for end in range(len(events) - 1, 0, -1):
        end_idx, end_type = events[end]

        # ищем предыдущий экстремум противоположного типа
        for start in range(end - 1, -1, -1):
            start_idx, start_type = events[start]
            if start_type == end_type:
                continue

            # bullish leg: low -> high
            if start_type == "low" and end_type == "high":
                upper = float(highs[end_idx])
                lower = float(lows[start_idx])
            # bearish leg: high -> low
            elif start_type == "high" and end_type == "low":
                upper = float(highs[start_idx])
                lower = float(lows[end_idx])
            else:
                continue

            if upper <= lower:
                continue

            leg = upper - lower
            if leg >= min_leg:
                return upper, lower

    return None, None

def _build_last_swing_range_15m(highs: np.ndarray, lows: np.ndarray, sig_highs: List[int], sig_lows: List[int]) -> Tuple[Optional[float], Optional[float]]:
    """
    Строит последнюю завершённую swing-пару для 15m:
    - если последним был high: берём последний low перед ним => low -> high
    - если последним был low: берём последний high перед ним => high -> low
    """
    if not sig_highs or not sig_lows:
        return None, None

    last_high_idx = sig_highs[-1]
    last_low_idx = sig_lows[-1]

    if last_high_idx > last_low_idx:
        prev_lows = [i for i in sig_lows if i < last_high_idx]
        if not prev_lows:
            return float(highs[last_high_idx]), float(lows[last_low_idx])
        lower_idx = prev_lows[-1]
        upper_idx = last_high_idx
    else:
        prev_highs = [i for i in sig_highs if i < last_low_idx]
        if not prev_highs:
            return float(highs[last_high_idx]), float(lows[last_low_idx])
        upper_idx = prev_highs[-1]
        lower_idx = last_low_idx

    upper = float(highs[upper_idx])
    lower = float(lows[lower_idx])

    if upper <= lower:
        return None, None

    return upper, lower


def get_structural_extremums(df: pd.DataFrame, timeframe: str = "1h", h1_reference: Optional[dict] = None) -> dict:
    required_cols = {"high", "low", "close"}
    if not required_cols.issubset(df.columns):
        return {"upper": None, "lower": None, "zones": {"resistance": [], "support": []}}

    clean_df = df.copy()
    clean_df["high"] = pd.to_numeric(clean_df["high"], errors="coerce")
    clean_df["low"] = pd.to_numeric(clean_df["low"], errors="coerce")
    clean_df["close"] = pd.to_numeric(clean_df["close"], errors="coerce")
    clean_df = clean_df.dropna(subset=["high", "low", "close"]).reset_index(drop=True)

    if len(clean_df) < 10:
        return {
            "upper": round(float(clean_df["high"].max()), 2),
            "lower": round(float(clean_df["low"].min()), 2),
            "zones": {"resistance": [], "support": []}
        }

    tf = timeframe.lower()

    # глубина и чувствительность по ТФ
    if tf in ("4h", "4", "h4"):
        depth = 7
        atr_mult = 0.8
    elif tf in ("1h", "1", "h1"):
        depth = 4
        atr_mult = 0.5
    elif tf in ("15m", "15", "m15"):
        depth = 3
        atr_mult = 0.4
    else:
        depth = 4
        atr_mult = 0.5

    highs = clean_df["high"].to_numpy(dtype=float)
    lows = clean_df["low"].to_numpy(dtype=float)
    closes = clean_df["close"].to_numpy(dtype=float)
    atr = _compute_atr(clean_df, 14)

    current_price = float(closes[-1])
    atr_last = float(atr[-1]) if len(atr) else 0.0
    cluster_tol = max(atr_last * 0.5, current_price * 0.001)

    pivot_highs, pivot_lows = _find_pivots(highs, lows, depth)

    sig_highs = []
    for i in pivot_highs:
        l = highs[max(0, i - depth):i]
        r = highs[i + 1:min(len(highs), i + depth + 1)]
        if l.size > 0 and r.size > 0 and (highs[i] - max(np.max(l), np.max(r))) >= atr[i] * atr_mult:
            sig_highs.append(i)

    sig_lows = []
    for i in pivot_lows:
        l = lows[max(0, i - depth):i]
        r = lows[i + 1:min(len(lows), i + depth + 1)]
        if l.size > 0 and r.size > 0 and (min(np.min(l), np.min(r)) - lows[i]) >= atr[i] * atr_mult:
            sig_lows.append(i)

    if not sig_highs:
        sig_highs = [int(np.argmax(highs))]
    if not sig_lows:
        sig_lows = [int(np.argmin(lows))]

    cluster_highs = _cluster_levels([float(highs[i]) for i in sig_highs], cluster_tol) or [float(np.max(highs))]
    cluster_lows = _cluster_levels([float(lows[i]) for i in sig_lows], cluster_tol) or [float(np.min(lows))]

    # --- M15: ищем последнюю завершённую swing-пару, но не слишком узкую
    if tf in ("15m", "15", "m15"):
        raw_upper, raw_lower = _build_strict_last_swing_range_15m(
            highs=highs,
            lows=lows,
            sig_highs=sig_highs,
            sig_lows=sig_lows,
            atr_last=atr_last,
        )

        if raw_upper is None or raw_lower is None or raw_upper <= raw_lower:
            raw_upper = float(np.max(highs))
            raw_lower = float(np.min(lows))

        raw_upper, raw_lower = _expand_range_to_h1_if_needed(
            tf=tf,
            upper=raw_upper,
            lower=raw_lower,
            current_price=current_price,
            atr_last=atr_last,
            h1_reference=h1_reference,
        )

        return {
            "upper": round(raw_upper, 2),
            "lower": round(raw_lower, 2),
            "zones": {
                "resistance": [round(float(x), 2) for x in cluster_highs[-5:]],
                "support": [round(float(x), 2) for x in cluster_lows[-5:]],
            },
        }

    # --- H1: рабочий reference range
    if tf in ("1h", "1", "h1"):
        raw_upper = _select_last_significant(cluster_highs, current_price, "high")
        raw_lower = _select_last_significant(cluster_lows, current_price, "low")

        window_high = float(np.max(highs))
        window_low = float(np.min(lows))

        upper = round(raw_upper, 2) if raw_upper is not None else round(window_high, 2)
        lower = round(raw_lower, 2) if raw_lower is not None else round(window_low, 2)

        if upper <= lower:
            upper = round(window_high, 2)
            lower = round(window_low, 2)

        return {
            "upper": upper,
            "lower": lower,
            "zones": {
                "resistance": [round(float(x), 2) for x in cluster_highs[-5:]],
                "support": [round(float(x), 2) for x in cluster_lows[-5:]],
            },
        }

    # --- H4: широкая структура
    if tf in ("4h", "4", "h4"):
        raw_upper, raw_lower = _build_strict_last_swing_range_15m(
            highs=highs,
            lows=lows,
            sig_highs=sig_highs,
            sig_lows=sig_lows,
            atr_last=atr_last,
        )

        window_high = float(np.max(highs))
        window_low = float(np.min(lows))
        window_range = window_high - window_low

        if raw_upper is None or raw_lower is None or raw_upper <= raw_lower:
            raw_upper = window_high
            raw_lower = window_low
        else:
            leg = raw_upper - raw_lower
            if leg < max(atr_last * 6.0, window_range * 0.35):
                raw_upper = window_high
                raw_lower = window_low

        return {
            "upper": round(raw_upper, 2),
            "lower": round(raw_lower, 2),
            "zones": {
                "resistance": [round(float(x), 2) for x in cluster_highs[-5:]],
                "support": [round(float(x), 2) for x in cluster_lows[-5:]],
            },
        }

    # --- fallback
    raw_upper = _select_last_significant(cluster_highs, current_price, "high")
    raw_lower = _select_last_significant(cluster_lows, current_price, "low")

    window_high = float(np.max(highs))
    window_low = float(np.min(lows))

    upper = round(raw_upper, 2) if raw_upper is not None else round(window_high, 2)
    lower = round(raw_lower, 2) if raw_lower is not None else round(window_low, 2)

    if upper <= lower:
        upper = round(window_high, 2)
        lower = round(window_low, 2)

    return {
        "upper": upper,
        "lower": lower,
        "zones": {
            "resistance": [round(float(x), 2) for x in cluster_highs[-5:]],
            "support": [round(float(x), 2) for x in cluster_lows[-5:]],
        },
    }
    
def _range_width(upper: Optional[float], lower: Optional[float]) -> Optional[float]:
    if upper is None or lower is None:
        return None
    if upper <= lower:
        return None
    return upper - lower


def _is_too_narrow_range(
    upper: Optional[float],
    lower: Optional[float],
    atr_last: Optional[float],
    current_price: float,
    factor: float = 2.0,
    min_pct: float = 0.0025,
) -> bool:
    width = _range_width(upper, lower)
    if width is None:
        return True

    atr_threshold = (atr_last or 0.0) * factor
    pct_threshold = current_price * min_pct
    threshold = max(atr_threshold, pct_threshold)

    return width < threshold


def _choose_h1_reference_range(
    h1_zones: dict,
    current_price: float,
    fallback_high: float,
    fallback_low: float,
) -> tuple[float, float]:
    upper = h1_zones.get("upper")
    lower = h1_zones.get("lower")

    upper = float(upper) if upper is not None else fallback_high
    lower = float(lower) if lower is not None else fallback_low

    if upper <= lower:
        return fallback_high, fallback_low

    return upper, lower


def _expand_range_to_h1_if_needed(
    tf: str,
    upper: Optional[float],
    lower: Optional[float],
    current_price: float,
    atr_last: Optional[float],
    h1_reference: Optional[dict] = None,
) -> tuple[float, float]:
    """
    Если локальный диапазон слишком узкий, расширяем его до H1 reference range.
    """
    fallback_upper = upper
    fallback_lower = lower

    if fallback_upper is None or fallback_lower is None or fallback_upper <= fallback_lower:
        return current_price, current_price

    if tf in ("15m", "15", "m15"):
        if _is_too_narrow_range(fallback_upper, fallback_lower, atr_last, current_price, factor=2.0, min_pct=0.0025):
            if isinstance(h1_reference, dict):
                h1_upper = h1_reference.get("upper")
                h1_lower = h1_reference.get("lower")
                if h1_upper is not None and h1_lower is not None and h1_upper > h1_lower:
                    ref_upper, ref_lower = float(h1_upper), float(h1_lower)

                    # M15 должен жить внутри H1, но не быть слишком узким
                    expanded_upper = max(fallback_upper, ref_upper)
                    expanded_lower = min(fallback_lower, ref_lower)

                    if expanded_upper > expanded_lower:
                        return expanded_upper, expanded_lower

                    return ref_upper, ref_lower

    return fallback_upper, fallback_lower
        


def detect_market_phase(df: pd.DataFrame, fib: dict, atr: float, vol_ratio: float) -> str:
    close = df['close'].iloc[-1]
    if not fib:
        return "Неопределённо"

    fib_50 = fib.get('fib_0.5', close)
    fib_618 = fib.get('fib_0.618', close)
    fib_382 = fib.get('fib_0.382', close)

    if abs(close - fib_50) <= atr and vol_ratio < 1.2:
        return "📦 Накопление (баланс у 50% Фибоначчи)"
    if close > fib_382 and vol_ratio > 1.5:
        return "🚀 Импульс/Пробой (подтверждение объёмом)"
    if close < fib_618 and vol_ratio < 1.0:
        return "📉 Коррекция (возврат к базе)"
    return "🔄 Боковое движение"


def get_technical_metrics(df: pd.DataFrame, timeframe: str = "1h") -> dict:
    df['sma20'] = df['close'].rolling(20).mean()
    df['sma50'] = df['close'].rolling(50).mean()
    # EMA200 for trend filter — long only if price > EMA200, short only if <
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

    # RSI по Wilder EMA (стандарт TradingView), не SMA
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, min_periods=14).mean()
    rs = gain / loss
    df['rsi'] = (100 - (100 / (1 + rs))).fillna(50)

    mid = df['close'].rolling(20).mean()
    std = df['close'].rolling(20).std()
    df['bb_upper'] = mid + (2 * std)
    df['bb_lower'] = mid - (2 * std)

    high_low = df['high'] - df['low']
    prev_close = df['close'].shift()
    high_close = (df['high'] - prev_close).abs()
    low_close = (df['low'] - prev_close).abs()
    tr = pd.DataFrame({'hl': high_low, 'hc': high_close, 'lc': low_close}).max(axis=1)
    # ATR по Wilder EMA (стандарт TradingView), не SMA
    df['atr'] = tr.ewm(alpha=1/14, min_periods=14).mean()

    # Volume ratio: среднее 3 последних свечей (стабильнее одной)
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    vol_recent = df['volume'].tail(3).mean()
    vol_ratio = vol_recent / max(vol_ma, 1e-6)
    vol_trend = 'растёт' if vol_ratio > 1.2 else ('падает' if vol_ratio < 0.8 else 'нейтральный')

    last = df.iloc[-1]
    fib = calculate_fib(df)
    volume_context = analyze_volume_context(df)
    atr_val = last['atr']
    session = get_session_phase(datetime.now(timezone.utc).hour)
    structural = find_structural_levels(df)
    phase = detect_market_phase(df, fib, atr_val, vol_ratio)

    h1_reference = None
    if timeframe.lower() in ("15m", "15", "m15"):
        # если хотим улучшать M15 через H1, можно подать H1 reference позже из внешнего слоя
        h1_reference = None

    extremums = get_structural_extremums(df, timeframe=timeframe, h1_reference=h1_reference)
    zone = {
        "upper": extremums["upper"],
        "lower": extremums["lower"],
        "resistance_levels": extremums["zones"]["resistance"],
        "support_levels": extremums["zones"]["support"]
    }

    fib_context = {
        '50%': fib.get('fib_0.5', 'N/A'),
        '61.8%': fib.get('fib_0.618', 'N/A'),
        '38.2%': fib.get('fib_0.382', 'N/A'),
        'rule': 'Только для оценки глубины коррекции. Запрещено ставить TP/SL.'
    }

    return {
        'last_price': round(float(last['close']), 2),
        'rsi': round(float(last['rsi']), 1),
        'bb_pos': 'верх' if last['close'] > last['bb_upper'] else ('низ' if last['close'] < last['bb_lower'] else 'середина'),
        'atr': round(float(atr_val), 2),
        'vol_ratio': round(float(vol_ratio), 2),
        'vol_trend': vol_trend,
        'sma_cross': 'bull' if last['sma20'] > last['sma50'] else 'bear',
        'ema200': round(float(last['ema200']), 2) if not pd.isna(last['ema200']) else None,
        'resistance': structural['resistance'],
        'support': structural['support'],
        'fib_context': fib_context,
        'phase': phase,
        'session': session,
        'zone': zone,
        'volume_context': volume_context
    }


def fetch_and_plot(symbol: str, timeframe: str = "1h", limit: int = 100) -> tuple[bytes, dict]:
    try:
        tf = timeframe.strip().lower()

        market_type = 'future' if is_futures(symbol) else 'spot'
        provider = OhlcvDataProvider()
        exchange = None  # чтобы Pylance не ругался на possibly unbound
        volume_context = {
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
            "comment": "A/D context unavailable.",
            "timeframe": tf,
            "tf_weight": 1.0,
            "signals": [],
            "last_price": 0.0,
            "last_flow": 0.0,
        }

        try:
            req = OhlcvRequest(
                symbol=symbol,
                timeframe=tf,
                limit=limit,
                market_type=market_type,
                force_refresh=False,
            )
            raw_df, _ = provider.ensure_ohlcv(req)

            df = raw_df.copy()
            if "timestamp" not in df.columns and "time" in df.columns:
                df["timestamp"] = pd.to_datetime(df["time"], errors="coerce", utc=True)

            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert(None)

            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"]).reset_index(drop=True)

            volume_context = analyze_volume_context(df, timeframe=tf)

        except Exception as e:
            logger.warning(f"CSV source unavailable for {symbol} {tf}: {e}. Falling back to Binance fetch.")
            exchange = ccxt.binance({'options': {'defaultType': market_type}})
            bars = exchange.fetch_ohlcv(symbol, tf, limit=limit)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
            volume_context = analyze_volume_context(df, timeframe=tf)

        last = df.iloc[-1]

        ticker = exchange.fetch_ticker(symbol) if exchange is not None else None
        raw_price = (ticker.get('last') if ticker else None) or last['close']
        current_price = round(float(raw_price), 2)

        metrics = get_technical_metrics(df, timeframe=timeframe)
        metrics["volume_context"] = volume_context
        metrics['current_price'] = current_price
        metrics['last_closed_price'] = round(float(last['close']), 2)
        metrics['symbol'] = symbol
        metrics['timeframe'] = timeframe

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]}, dpi=100)
        colors = ['green' if c >= o else 'red' for c, o in zip(df['close'], df['open'])]
        ax1.bar(df['timestamp'], df['high'] - df['low'], bottom=df['low'], color=colors, width=0.005)
        ax1.bar(df['timestamp'], df['close'] - df['open'], bottom=df['open'], color=colors, width=0.005)
        ax1.plot(df['timestamp'], df['sma20'], color='blue', linewidth=1.5, alpha=0.8)
        ax1.fill_between(df['timestamp'], df['bb_upper'], df['bb_lower'], color='gray', alpha=0.1)

        fib_vals = metrics['fib_context']
        if fib_vals['50%'] != 'N/A':
            for lvl, color in [('50%', 'purple'), ('61.8%', 'orange'), ('38.2%', 'cyan')]:
                if fib_vals[lvl] != 'N/A':
                    ax1.axhline(y=fib_vals[lvl], color=color, linestyle='--', alpha=0.6, linewidth=1)

        volume_short = (
            f"A/D={volume_context.get('bias', 'neutral')} | "
            f"conf={volume_context.get('volume_confirmation', 'none')} | "
            f"div={volume_context.get('divergence', 'none')} | "
            f"str={volume_context.get('strength', 0.0)}/10 | "
            f"CMF={volume_context.get('cmf', 0.0)} | "
            f"VR={volume_context.get('volume_ratio', 1.0)}"
        )

        ax2.bar(df['timestamp'], df['volume'], color=colors, width=0.005, alpha=0.6)
        phase_text = metrics['phase'].split(' ', 1)[-1] if ' ' in metrics['phase'] else metrics['phase']
        ax1.set_title(
            f"{symbol} | {tf} | {phase_text} | Curr:{current_price} RSI:{metrics['rsi']} Vol:{metrics['vol_ratio']}x | {volume_short}",
            fontsize=11
        )
        ax1.grid(True, alpha=0.2)
        ax2.grid(True, alpha=0.2)
        ax1.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))

        buf = io.BytesIO()
        fig.savefig(buf, format='JPEG', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)

        return buf.getvalue(), metrics
    except Exception as e:
        logger.error(f"❌ Ошибка построения графика {symbol}: {e}")
        raise