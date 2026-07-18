import ccxt
import pandas as pd
from typing import List, Optional, Dict, Any

from core.zigzag.structural_zigzag import get_structural_extremums_zigzag, analyze_multitimeframe


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    exchange = ccxt.binance({
        "options": {
            "defaultType": "future"
        }
    })
    bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(
        bars,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def format_levels(result: Dict[str, Any]) -> str:
    lines: List[str] = []

    lines.append(f"upper: {result.get('upper')}")
    lines.append(f"lower: {result.get('lower')}")
    lines.append(f"swing_direction: {result.get('swing_direction')}")
    lines.append(f"channel_state: {result.get('channel_state')}")
    lines.append(f"breakout_state: {result.get('breakout_state')}")
    lines.append(f"market_mode: {result.get('market_mode')}")
    lines.append(f"price_position: {result.get('price_position')}")
    lines.append(f"pattern_tags: {result.get('pattern_tags', [])}")
    lines.append(f"summary: {result.get('summary', '')}")

    legs = result.get("swing_legs", [])
    if legs:
        lines.append(f"last_swing_amplitude: {legs[-1].get('amplitude')}")
        lines.append(f"last_swing_bars: {legs[-1].get('bars')}")
        lines.append(f"last_swing_direction: {legs[-1].get('direction')}")
    else:
        lines.append("last_swing_amplitude: None")
        lines.append("last_swing_bars: None")
        lines.append("last_swing_direction: None")

    params = result.get("params", {})
    if params:
        lines.append("params:")
        for k, v in params.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("params: {}")

    pivots = result.get("pivots", {})
    lines.append(f"pivots.highs: {pivots.get('highs', [])}")
    lines.append(f"pivots.lows: {pivots.get('lows', [])}")

    swing_points = result.get("swing_points", [])
    if swing_points:
        lines.append("swing_points:")
        for sp in swing_points[-12:]:
            lines.append(
                f"  - index={sp.get('index')} type={sp.get('type')} price={sp.get('price')}"
            )
    else:
        lines.append("swing_points: []")

    zones = result.get("zones", {})
    lines.append(f"zones.resistance: {zones.get('resistance', [])}")
    lines.append(f"zones.support: {zones.get('support', [])}")

    forecast = result.get("forecast", {})
    if forecast:
        lines.append("forecast:")
        for k, v in forecast.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("forecast: {}")

    return "\n".join(lines)


def run_test(
    symbol: str = "XAUT/USDT",
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

    print(f"\n=== TEST: {symbol} ===")
    print(
        f"settings: mode={mode}, length={length}, percent={percent}, "
        f"confirmation_mode={confirmation_mode}, limit={limit}, debug={debug}"
    )

    layers: Dict[str, Dict[str, Any]] = {}

    for tf in timeframes:
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

            print(f"\n--- {tf.upper()} ---")
            print(format_levels(result))
        except Exception as e:
            print(f"\n--- {tf.upper()} ---")
            print(f"ERROR: {type(e).__name__}: {e}")

    if layers:
        print("\n=== MULTI-TF STACK SUMMARY ===")
        stack = analyze_multitimeframe(layers)
        for k, v in stack.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    run_test()