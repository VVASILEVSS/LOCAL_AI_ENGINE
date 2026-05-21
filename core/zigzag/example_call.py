import ccxt
import pandas as pd
from typing import Any, Dict, cast

from core.structural_zigzag import get_structural_extremums_zigzag


def fetch_ohlcv(
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    limit: int = 300,
) -> pd.DataFrame:
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


if __name__ == "__main__":
    df = fetch_ohlcv(symbol="BTCUSDT", timeframe="1h", limit=300)

    result: Dict[str, Any] = cast(
        Dict[str, Any],
        get_structural_extremums_zigzag(
            df=df,
            timeframe="1h",
            mode="lux_channel",
            length=100,
            debug=True,
        )
    )

    summary = result.get("summary", "")
    swing_points = result.get("swing_points", [])

    print(summary)
    print(swing_points[-6:] if isinstance(swing_points, list) else [])