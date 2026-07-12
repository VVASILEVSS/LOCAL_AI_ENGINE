import ccxt
import json
import pandas as pd
from typing import Any, Dict, List, Optional

from core.zigzag.structural_zigzag import get_structural_extremums_zigzag, analyze_multitimeframe


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    exchange = ccxt.binance({
        "options": {
            "defaultType": "future"
        }
    })
    bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def summarize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    upper = result.get("upper")
    lower = result.get("lower")
    swing_direction = result.get("swing_direction", "unknown")
    pivots = result.get("pivots", {})
    zones = result.get("zones", {})
    params = result.get("params", {})
    swing_points = result.get("swing_points", [])
    forecast = result.get("forecast", {})
    pattern_tags = result.get("pattern_tags", [])

    current_price = params.get("current_price")
    atr_last = params.get("atr_last")

    channel_width = None
    price_position = None
    breakout_state = result.get("breakout_state", "unknown")
    channel_state = result.get("channel_state", "unknown")
    market_mode = result.get("market_mode", "unknown")

    if isinstance(upper, (int, float)) and isinstance(lower, (int, float)) and upper > lower:
        channel_width = round(float(upper) - float(lower), 2)
        if isinstance(current_price, (int, float)):
            price_position = round((float(current_price) - float(lower)) / channel_width, 4)

    llm_summary = (
        f"TF structure is {swing_direction}. "
        f"Channel state is {channel_state}. "
        f"Breakout state is {breakout_state}. "
        f"Market mode is {market_mode}. "
        f"Price position in channel is {price_position}. "
        f"Current price is {current_price}. "
        f"ATR is {atr_last}. "
        f"Pattern tags: {', '.join(pattern_tags) if pattern_tags else 'none'}."
    )

    return {
        "upper": upper,
        "lower": lower,
        "channel_width": channel_width,
        "price_position": price_position,
        "swing_direction": swing_direction,
        "channel_state": channel_state,
        "breakout_state": breakout_state,
        "market_mode": market_mode,
        "pivot_highs_count": len(pivots.get("highs", [])) if isinstance(pivots, dict) else 0,
        "pivot_lows_count": len(pivots.get("lows", [])) if isinstance(pivots, dict) else 0,
        "resistance_levels": zones.get("resistance", []),
        "support_levels": zones.get("support", []),
        "last_swing_points": swing_points[-6:] if isinstance(swing_points, list) else [],
        "forecast": forecast,
        "pattern_tags": pattern_tags,
        "params": params,
        "llm_summary": llm_summary,
    }


def run_test(
    symbol: str = "BTCUSDT",
    timeframes: Optional[List[str]] = None,
    limit: int = 300,
    mode: str = "hybrid_atr",
    length: Optional[int] = None,
    percent: Optional[float] = None,
    confirmation_mode: str = "close",
    debug: bool = False,
) -> None:
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]

    print(f"\n=== ZIGZAG FORECAST TEST: {symbol} ===")
    print(
        f"settings: mode={mode}, length={length}, percent={percent}, "
        f"confirmation_mode={confirmation_mode}, limit={limit}, debug={debug}"
    )

    layers: Dict[str, Dict[str, Any]] = {}

    for tf in timeframes:
        print(f"\n--- {tf.upper()} ---")
        try:
            df = fetch_ohlcv(symbol, tf, limit=limit)
            result = get_structural_extremums_zigzag(
                df=df,
                timeframe=tf,
                mode=mode,
                length=length,
                percent=percent,
                confirmation_mode=confirmation_mode,
                symbol=symbol,
                debug=debug,
            )
            layers[tf] = result

            summary = summarize_result(result)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")

    if layers:
        print("\n=== MULTI-TF STACK SUMMARY ===")
        stack = analyze_multitimeframe(layers)
        print(json.dumps(stack, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_test()