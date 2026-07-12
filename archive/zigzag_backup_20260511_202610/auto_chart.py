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


def get_structural_extremums(df: pd.DataFrame, timeframe: str = "1h") -> dict:
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

    # Разные глубины для разных ТФ
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
    cluster_tol = max(atr[-1] * 0.5, current_price * 0.001)

    pivot_highs, pivot_lows = _find_pivots(highs, lows, depth)

    sig_highs = []
    for i in pivot_highs:
        l, r = highs[max(0, i - depth):i], highs[i + 1:min(len(highs), i + depth + 1)]
        if l.size > 0 and r.size > 0 and (highs[i] - max(np.max(l), np.max(r))) >= atr[i] * atr_mult:
            sig_highs.append(i)

    sig_lows = []
    for i in pivot_lows:
        l, r = lows[max(0, i - depth):i], lows[i + 1:min(len(lows), i + depth + 1)]
        if l.size > 0 and r.size > 0 and (min(np.min(l), np.min(r)) - lows[i]) >= atr[i] * atr_mult:
            sig_lows.append(i)

    if not sig_highs:
        sig_highs = [int(np.argmax(highs))]
    if not sig_lows:
        sig_lows = [int(np.argmin(lows))]

    cluster_highs = _cluster_levels([float(highs[i]) for i in sig_highs], cluster_tol) or [float(np.max(highs))]
    cluster_lows = _cluster_levels([float(lows[i]) for i in sig_lows], cluster_tol) or [float(np.min(lows))]

    # 15m — строго последняя swing-пара
    if tf in ("15m", "15", "m15"):
        raw_upper, raw_lower = _build_strict_last_swing_range_15m(
            highs=highs,
            lows=lows,
            sig_highs=sig_highs,
            sig_lows=sig_lows,
            atr_last=float(atr[-1]) if len(atr) else 0.0
        )
        if raw_upper is None or raw_lower is None or raw_upper <= raw_lower:
            raw_upper = float(np.max(highs))
            raw_lower = float(np.min(lows))

        return {
            "upper": round(raw_upper, 2),
            "lower": round(raw_lower, 2),
            "zones": {
                "resistance": [round(float(x), 2) for x in cluster_highs[-5:]],
                "support": [round(float(x), 2) for x in cluster_lows[-5:]]
            }
        }

    # 1H — средняя swing-логика
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
                "support": [round(float(x), 2) for x in cluster_lows[-5:]]
            }
        }

    # 4H — широкая структура: берём крайние значимые swing-экстремумы,
    # но если они слишком узкие, fallback на более широкий диапазон
    if tf in ("4h", "4", "h4"):
        # ищем последнюю значимую восходящую/нисходящую волну
        raw_upper, raw_lower = None, None

        # Сначала пробуем строгую swing-пару по событиям
        raw_upper, raw_lower = _build_strict_last_swing_range_15m(
            highs=highs,
            lows=lows,
            sig_highs=sig_highs,
            sig_lows=sig_lows,
            atr_last=float(atr[-1]) if len(atr) else 0.0
        )

        # Если диапазон слишком узкий для 4H — берём более широкий взгляд
        window_high = float(np.max(highs))
        window_low = float(np.min(lows))
        window_range = window_high - window_low

        if raw_upper is None or raw_lower is None or raw_upper <= raw_lower:
            raw_upper = window_high
            raw_lower = window_low
        else:
            leg = raw_upper - raw_lower
            if leg < max(atr[-1] * 6.0, window_range * 0.35):
                # слишком узко для 4H — используем более широкий структурный диапазон
                raw_upper = window_high
                raw_lower = window_low

        return {
            "upper": round(raw_upper, 2),
            "lower": round(raw_lower, 2),
            "zones": {
                "resistance": [round(float(x), 2) for x in cluster_highs[-5:]],
                "support": [round(float(x), 2) for x in cluster_lows[-5:]]
            }
        }

    # fallback для остальных ТФ
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
            "support": [round(float(x), 2) for x in cluster_lows[-5:]]
        }
    }

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
            "support": [round(float(x), 2) for x in cluster_lows[-5:]]
        }
    }


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

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
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
    df['atr'] = tr.rolling(14).mean()

    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    vol_ratio = df['volume'].iloc[-1] / max(vol_ma, 1e-6)
    vol_trend = 'растёт' if vol_ratio > 1.2 else ('падает' if vol_ratio < 0.8 else 'нейтральный')

    last = df.iloc[-1]
    fib = calculate_fib(df)
    atr_val = last['atr']
    session = get_session_phase(datetime.now(timezone.utc).hour)
    structural = find_structural_levels(df)
    phase = detect_market_phase(df, fib, atr_val, vol_ratio)

    extremums = get_structural_extremums(df, timeframe=timeframe)
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
        'resistance': structural['resistance'],
        'support': structural['support'],
        'fib_context': fib_context,
        'phase': phase,
        'session': session,
        'zone': zone
    }


def fetch_and_plot(symbol: str, timeframe: str = "1h", limit: int = 100) -> tuple[bytes, dict]:
    try:
        tf = timeframe.strip().lower()

        market_type = 'future' if is_futures(symbol) else 'spot'
        exchange = ccxt.binance({'options': {'defaultType': market_type}})
        bars = exchange.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        last = df.iloc[-1]

        ticker = exchange.fetch_ticker(symbol)
        raw_price = ticker.get('last') or last['close']
        current_price = round(float(raw_price), 2)

        metrics = get_technical_metrics(df, timeframe=timeframe)

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

        ax2.bar(df['timestamp'], df['volume'], color=colors, width=0.005, alpha=0.6)
        phase_text = metrics['phase'].split(' ', 1)[-1] if ' ' in metrics['phase'] else metrics['phase']
        ax1.set_title(f"{symbol} | {tf} | {phase_text} | Curr:{current_price} RSI:{metrics['rsi']} Vol:{metrics['vol_ratio']}x", fontsize=11)
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